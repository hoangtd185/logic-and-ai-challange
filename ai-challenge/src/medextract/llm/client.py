"""LLM access behind a small interface, so the pipeline can be tested without a network.

``LLMClient.generate_json`` takes a system prompt, text and image parts and a
JSON Schema, and returns an ``LLMResponse``: the decoded JSON object plus
``CallStats`` (timing per attempt, HTTP requests, bytes sent, token usage). The
model is constrained to the schema by the API (structured output); callers
validate the result with pydantic.

- ``GeminiClient``: the real client (google-genai), with an explicit timeout and
  retries we control and log.
- ``CachingClient``: disk cache keyed by the full request. A cache hit keeps the
  stats of the original call, so timings survive re-runs.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import re
import json
import logging
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

log = logging.getLogger(__name__)

# The pipeline step a call belongs to ("classify", "re-read scan"), so retry warnings
# say which call they are about.
CALL_LABEL: contextvars.ContextVar[str] = contextvars.ContextVar("medextract_call_label", default="")


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime_type: str


Part = str | ImagePart


@dataclass
class CallStats:
    model: str
    seconds: float = 0.0  # wall time of the whole call, retries and backoff included
    attempts: list[dict[str, Any]] = field(default_factory=list)  # one entry per attempt we made
    http_requests: int = 0  # real HTTP requests, including the SDK's own hidden retry
    request_bytes: int = 0
    usage: dict[str, int] | None = None  # token counts reported by the API
    cached: bool = False
    original: dict[str, Any] | None = None  # for a cache hit: the stats of the call that filled it

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LLMResponse:
    data: dict[str, Any]
    stats: CallStats


class LLMError(RuntimeError):
    """The model call failed or returned something unusable."""


class LLMNetworkError(LLMError):
    """The API could not be reached (DNS, connection, timeout) after all retries."""


class DeadlineExceeded(TimeoutError):
    """One attempt (upload, server time, the SDK's own resend) ran past its deadline."""


class LLMClient(Protocol):
    model: str

    def generate_json(self, *, system: str, parts: list[Part], schema: dict[str, Any]) -> LLMResponse: ...


def estimate_request_bytes(system: str, parts: list[Part]) -> int:
    """Payload size before JSON framing: base64 images plus UTF-8 text."""
    total = len(system.encode())
    for part in parts:
        total += (len(part.data) + 2) // 3 * 4 if isinstance(part, ImagePart) else len(part.encode())
    return total


# --- error classification ----------------------------------------------------------

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_NETWORK_ERROR_NAMES = {"ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
                        "TimeoutException", "NetworkError", "RemoteProtocolError", "ReadError", "WriteError"}


def classify_error(exc: BaseException) -> str:
    """'network', 'retryable' (rate limit / server error) or 'fatal'."""
    for err in _chain(exc):
        names = {cls.__name__ for cls in type(err).__mro__}
        if names & _NETWORK_ERROR_NAMES or isinstance(err, (ConnectionError, TimeoutError)):
            return "network"
        if isinstance(err, OSError) and not isinstance(err, FileNotFoundError):
            return "network"  # e.g. getaddrinfo failed, WinError 10051 unreachable network
        code = _status(err)
        if code in RETRYABLE_STATUS:
            return "retryable"
    return "fatal"


def _status(err: BaseException) -> int | None:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int):
        return code
    match = re.search(r"\b(?:Error code|status)[: ]+(\d{3})\b", str(err))
    return int(match.group(1)) if match else None


OVERLOAD_STATUS = {429, 503}


def is_overload(exc: BaseException) -> bool:
    """The server is busy (429 / 503 'high demand'): retrying quickly only adds to the load."""
    return any(_status(err) in OVERLOAD_STATUS for err in _chain(exc))


def retry_after_s(exc: BaseException) -> float | None:
    """Seconds the server asked us to wait (Retry-After header), when it says."""
    for err in _chain(exc):
        response = getattr(err, "response", None) or getattr(err, "raw_response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            continue
        try:
            value = headers.get("retry-after")
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


# --- Gemini --------------------------------------------------------------------------


class GeminiClient:
    """google-genai client using structured output.

    - Timeouts at two levels. ``timeout_s`` is httpx's per-operation timeout
      (connect, each upload chunk, waiting for bytes); it does NOT cap a whole
      request: a slow upload that keeps trickling never trips it, and the SDK
      resends a failed request once on its own. So each attempt also has a hard
      wall-clock ``deadline_s``. When it passes, the HTTP client is closed (which
      aborts the request in flight) and rebuilt for the next attempt.
    - Our own retries with exponential backoff on network errors, rate limits and
      5xx. On overload (429 / 503 "high demand") the wait is longer (15, 30, 60 s)
      or whatever the server's Retry-After asks. The SDK's built-in retries are
      reduced to the minimum it allows (one extra attempt), and request hooks
      count every real HTTP request, so a hidden SDK retry shows up in
      ``CallStats.http_requests``.
    - Uses the Interactions API when the installed SDK has it, otherwise
      ``generate_content`` with ``response_json_schema``.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        thinking_level: str | None = "low",
        timeout_s: float = 90.0,
        connect_timeout_s: float = 15.0,
        max_retries: int = 4,
        backoff_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        deadline_s: float | None = 120.0,
        overload_backoff_s: float = 15.0,
    ):
        self.model = model
        self.thinking_level = thinking_level
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.overload_backoff_s = overload_backoff_s
        self.deadline_s = deadline_s
        self._sleep = sleep
        self._http_log: list[dict[str, Any]] = []
        self._api_key, self._timeout_s, self._connect_timeout_s = api_key, timeout_s, connect_timeout_s
        self._http_client = None
        self._client = self._make_client()

    def _make_client(self):
        from google import genai  # imported lazily so tests never need the SDK

        return genai.Client(api_key=self._api_key, http_options=self._http_options(self._timeout_s, self._connect_timeout_s))

    def _abort_in_flight(self) -> None:
        """Close the HTTP client (the request in flight fails) and start a fresh one."""
        if self._http_client is not None:
            try:
                self._http_client.close()
            except Exception:  # closing a busy client can raise; we only want it gone
                pass
        self._client = self._make_client()

    def _http_options(self, timeout_s: float, connect_timeout_s: float):
        try:
            import httpx
            from google.genai import types
        except ImportError:  # stand-in SDK in tests
            return None

        def on_request(request):
            try:
                size = len(request.content)
            except Exception:  # streamed body
                size = 0
            self._http_log.append({"start": time.perf_counter(), "bytes": size, "status": None})

        def on_response(response):
            if self._http_log:
                self._http_log[-1]["status"] = response.status_code
                self._http_log[-1]["seconds"] = round(time.perf_counter() - self._http_log[-1]["start"], 2)

        http_client = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            event_hooks={"request": [on_request], "response": [on_response]},
        )
        self._http_client = http_client
        return types.HttpOptions(
            timeout=int(timeout_s * 1000),  # milliseconds
            httpx_client=http_client,
            retry_options=types.HttpRetryOptions(attempts=1),
        )

    @contextlib.contextmanager
    def limited_retries(self, max_retries: int) -> Iterator[None]:
        """Calls inside this block retry at most ``max_retries`` times (never more than usual)."""
        saved = self.max_retries
        self.max_retries = min(saved, max_retries)
        try:
            yield
        finally:
            self.max_retries = saved

    def generate_json(self, *, system: str, parts: list[Part], schema: dict[str, Any]) -> LLMResponse:
        stats = CallStats(model=self.model, request_bytes=estimate_request_bytes(system, parts))
        started = time.perf_counter()
        http_before = len(self._http_log)
        last_error: BaseException | None = None
        kind = "fatal"

        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            log_start = len(self._http_log)
            try:
                text, usage = self._call_with_deadline(system, parts, schema)
                stats.attempts.append(self._attempt_record(t0, log_start, None))
                stats.usage = usage
                break
            except Exception as exc:  # the SDK's error classes vary between versions
                kind = classify_error(exc)
                last_error = exc
                stats.attempts.append(self._attempt_record(t0, log_start, f"{kind}: {exc}"))
                if kind == "fatal" or attempt == self.max_retries:
                    stats.seconds = round(time.perf_counter() - started, 2)
                    stats.http_requests = len(self._http_log) - http_before
                    error_cls = LLMNetworkError if kind == "network" else LLMError
                    raise error_cls(f"Gemini call failed after {attempt + 1} attempt(s): {exc}") from exc
                delay = self._retry_delay(exc, attempt)
                label = CALL_LABEL.get()
                log.warning("%sGemini %s error (%s); retrying in %.1fs (attempt %d of %d)",
                            f"{label}: " if label else "", kind, exc, delay, attempt + 2, self.max_retries + 1)
                self._sleep(delay)

        stats.seconds = round(time.perf_counter() - started, 2)
        stats.http_requests = len(self._http_log) - http_before
        if self._http_log[http_before:]:
            stats.request_bytes = max(stats.request_bytes, max(r["bytes"] for r in self._http_log[http_before:]))
        return LLMResponse(self._decode(text), stats)

    def _retry_delay(self, exc: BaseException, attempt: int) -> float:
        if is_overload(exc):
            asked = retry_after_s(exc)
            if asked is not None:
                return min(120.0, max(1.0, asked))
            return min(60.0, self.overload_backoff_s * 2**attempt) + random.uniform(0, 2)
        return min(30.0, self.backoff_s * 2**attempt) + random.uniform(0, 1)

    def _call_with_deadline(self, system: str, parts: list[Part], schema: dict[str, Any]):
        if not self.deadline_s:
            return self._call(system, parts, schema)
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["result"] = self._call(system, parts, schema)
            except BaseException as exc:  # handed back to the calling thread
                box["error"] = exc

        worker = threading.Thread(target=run, daemon=True, name="gemini-call")
        worker.start()
        worker.join(self.deadline_s)
        if worker.is_alive():
            self._abort_in_flight()
            raise DeadlineExceeded(
                f"no reply within {self.deadline_s:.0f} s (upload + server time, including the SDK's own resend)"
            )
        if "error" in box:
            raise box["error"]
        return box["result"]

    def _attempt_record(self, t0: float, log_start: int, error: str | None) -> dict[str, Any]:
        record: dict[str, Any] = {
            "seconds": round(time.perf_counter() - t0, 2),
            "http_requests": len(self._http_log) - log_start,
        }
        if error:
            record["error"] = error[:300]
        return record

    @staticmethod
    def _decode(text: str | None) -> dict[str, Any]:
        if not text:
            raise LLMError("model returned an empty response")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:  # should not happen with structured output
            raise LLMError(f"model returned invalid JSON despite the response schema: {text[:200]!r}") from exc
        if not isinstance(data, dict):
            raise LLMError(f"expected a JSON object, got {type(data).__name__}")
        return data

    def _call(self, system: str, parts: list[Part], schema: dict[str, Any]) -> tuple[str, dict[str, int] | None]:
        if hasattr(self._client, "interactions"):
            return self._call_interactions(system, parts, schema)
        return self._call_generate_content(system, parts, schema)

    def _call_interactions(self, system: str, parts: list[Part], schema: dict[str, Any]):
        items: list[dict[str, Any]] = []
        for part in parts:
            if isinstance(part, ImagePart):
                items.append(
                    {"type": "image", "data": base64.b64encode(part.data).decode("ascii"), "mime_type": part.mime_type}
                )
            else:
                items.append({"type": "text", "text": part})
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system_instruction": system,
            "input": items,
            "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
        }
        if self.thinking_level:
            kwargs["generation_config"] = {"thinking_level": self.thinking_level}
        interaction = self._client.interactions.create(**kwargs)
        return interaction.output_text, _usage(getattr(interaction, "usage", None))

    def _call_generate_content(self, system: str, parts: list[Part], schema: dict[str, Any]):
        from google.genai import types

        contents = [
            types.Part.from_bytes(data=p.data, mime_type=p.mime_type) if isinstance(p, ImagePart) else p
            for p in parts
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        response = self._client.models.generate_content(model=self.model, contents=contents, config=config)
        meta = getattr(response, "usage_metadata", None)
        usage = None
        if meta is not None:
            usage = {
                "total_input_tokens": getattr(meta, "prompt_token_count", None),
                "total_output_tokens": getattr(meta, "candidates_token_count", None),
                "total_thought_tokens": getattr(meta, "thoughts_token_count", None),
                "total_tokens": getattr(meta, "total_token_count", None),
            }
        return response.text, _usage(usage)


def _usage(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return None
    return {k: v for k, v in usage.items() if isinstance(v, int)} or None


# --- disk cache -----------------------------------------------------------------------

_CACHE_VERSION = 2


class CachingClient:
    """Caches responses on disk, keyed by model + prompt + images + schema.

    Entries store the response and the stats of the call that produced it, so a
    cached re-run still reports how long the real call took.
    """

    def __init__(self, inner: LLMClient, cache_dir: Path):
        self.inner = inner
        self.model = inner.model
        self.cache_dir = Path(cache_dir)
        self.hits = 0
        self.misses = 0

    def limited_retries(self, max_retries: int):
        inner = getattr(self.inner, "limited_retries", None)
        return inner(max_retries) if inner else contextlib.nullcontext()

    def generate_json(self, *, system: str, parts: list[Part], schema: dict[str, Any]) -> LLMResponse:
        path = self.cache_dir / f"{self.key(system, parts, schema)}.json"
        if path.is_file():
            self.hits += 1
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry.get("cache_version") == _CACHE_VERSION:
                data, original = entry["data"], entry["stats"]
            else:  # first-format entry: the bare reply, no stats were kept
                data, original = entry, None
            return LLMResponse(data, CallStats(model=self.model, cached=True, original=original))
        self.misses += 1
        response = self.inner.generate_json(system=system, parts=parts, schema=schema)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"cache_version": _CACHE_VERSION, "data": response.data, "stats": response.stats.to_dict()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return response

    def key(self, system: str, parts: list[Part], schema: dict[str, Any]) -> str:
        h = hashlib.sha256()
        h.update(self.model.encode())
        h.update(system.encode())
        h.update(json.dumps(schema, sort_keys=True).encode())
        for part in parts:
            if isinstance(part, ImagePart):
                h.update(part.mime_type.encode())
                h.update(hashlib.sha256(part.data).digest())
            else:
                h.update(part.encode())
        return h.hexdigest()[:32]


def build_client(settings) -> LLMClient:
    client: LLMClient = GeminiClient(
        settings.api_key,
        settings.model,
        settings.thinking_level,
        timeout_s=settings.timeout_s,
        max_retries=settings.max_retries,
        deadline_s=settings.deadline_s,
    )
    if settings.cache_dir:
        client = CachingClient(client, settings.cache_dir)
    return client
