"""LLM layer: schema conversion, Gemini request building, retries, caching. No network."""

import json
import sys
import types

import pytest

from medextract.classify import CLASSIFY_SCHEMA
from medextract.llm.client import (
    CachingClient,
    CallStats,
    GeminiClient,
    ImagePart,
    LLMError,
    LLMNetworkError,
    LLMResponse,
    classify_error,
)
from medextract.llm.schema import llm_schema
from medextract.schemas import FIELDS_BY_TYPE, DocumentType


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


EXTRACTABLE = [t for t in DocumentType if t is not DocumentType.UNKNOWN]


# --- schema ----------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", EXTRACTABLE, ids=lambda t: t.value)
def test_schema_has_no_refs_or_titles(doc_type):
    schema = llm_schema(FIELDS_BY_TYPE[doc_type])
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and '"title"' not in text and "anyOf" not in text


@pytest.mark.parametrize("doc_type", EXTRACTABLE, ids=lambda t: t.value)
def test_every_object_requires_all_properties(doc_type):
    for node in walk(llm_schema(FIELDS_BY_TYPE[doc_type])):
        if isinstance(node, dict) and "properties" in node:
            assert node["required"] == list(node["properties"])
            assert node["additionalProperties"] is False


@pytest.mark.parametrize("doc_type", EXTRACTABLE, ids=lambda t: t.value)
def test_every_field_value_is_nullable(doc_type):
    for node in walk(llm_schema(FIELDS_BY_TYPE[doc_type])):
        if isinstance(node, dict) and set(node.get("properties", {})) == {"value", "confidence"}:
            assert "null" in node["properties"]["value"]["type"]
            assert node["properties"]["confidence"] == {"type": "number", "minimum": 0.0, "maximum": 1.0}


def test_top_level_keys_match_output_schema():
    for doc_type in EXTRACTABLE:
        schema = llm_schema(FIELDS_BY_TYPE[doc_type])
        assert list(schema["properties"]) == list(FIELDS_BY_TYPE[doc_type].model_fields)


def test_nullable_enum_allows_null():
    flag = llm_schema(FIELDS_BY_TYPE[DocumentType.LAB_REPORT])["properties"]["tests"]["properties"]["value"]["items"][
        "properties"
    ]["flag"]["properties"]["value"]
    assert flag["type"] == ["string", "null"]
    assert flag["enum"] == ["normal", "high", "low", None]


def test_number_or_text_union():
    result = llm_schema(FIELDS_BY_TYPE[DocumentType.LAB_REPORT])["properties"]["tests"]["properties"]["value"][
        "items"
    ]["properties"]["result"]["properties"]["value"]
    assert result["type"] == ["number", "string", "null"]


def test_classification_schema():
    assert list(CLASSIFY_SCHEMA["properties"]) == ["evidence", "document_type", "confidence"]
    assert set(CLASSIFY_SCHEMA["properties"]["document_type"]["enum"]) == {t.value for t in DocumentType}


# --- GeminiClient against a stand-in SDK -------------------------------------------


class _Err(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install a minimal `google.genai` that records calls."""
    state = {"calls": [], "replies": []}

    class Interactions:
        def create(self, **kwargs):
            state["calls"].append(kwargs)
            reply = state["replies"].pop(0)
            if isinstance(reply, Exception):
                raise reply
            return types.SimpleNamespace(output_text=reply, usage=state.get("usage"))

    class Client:
        def __init__(self, api_key, http_options=None):
            state["api_key"] = api_key
            state["http_options"] = http_options
            self.interactions = Interactions()

    genai = types.ModuleType("google.genai")
    genai.Client = Client
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return state


def make_client(**kwargs):
    delays = []
    client = GeminiClient("k", "m", sleep=delays.append, **kwargs)
    client.delays = delays
    return client


def test_gemini_request_shape(fake_sdk):
    fake_sdk["replies"] = ['{"ok": true}']
    fake_sdk["usage"] = {"total_input_tokens": 1800, "total_output_tokens": 90, "total_thought_tokens": 400}
    client = GeminiClient(api_key="k", model="m-1", thinking_level="low", sleep=lambda s: None)
    out = client.generate_json(
        system="sys", parts=[ImagePart(b"\x89PNG..", "image/png"), "do it"], schema={"type": "object"}
    )
    assert out.data == {"ok": True}
    assert out.stats.usage == fake_sdk["usage"]
    assert len(out.stats.attempts) == 1 and out.stats.request_bytes > 0
    call = fake_sdk["calls"][0]
    assert call["model"] == "m-1" and call["system_instruction"] == "sys"
    assert call["input"][0]["type"] == "image" and call["input"][0]["mime_type"] == "image/png"
    assert call["input"][1] == {"type": "text", "text": "do it"}
    assert call["response_format"] == {"type": "text", "mime_type": "application/json", "schema": {"type": "object"}}
    assert call["generation_config"] == {"thinking_level": "low"}


def test_gemini_retries_rate_limits(fake_sdk):
    fake_sdk["replies"] = [_Err(429), _Err(503), '{"ok": 1}']
    client = make_client()
    out = client.generate_json(system="s", parts=["x"], schema={})
    assert out.data == {"ok": 1}
    assert len(fake_sdk["calls"]) == 3
    assert [a.get("error", "")[:9] for a in out.stats.attempts] == ["retryable", "retryable", ""]
    # 429 and 503 mean the server is overloaded: wait longer (15 s, then 30 s), not 2 s
    assert 15 <= client.delays[0] < 17 and 30 <= client.delays[1] < 32


@pytest.mark.parametrize(
    "error",
    [OSError(11001, "getaddrinfo failed"), OSError(10051, "A socket operation was attempted to an unreachable network"),
     ConnectionResetError("reset"), TimeoutError("timed out")],
    ids=["dns", "unreachable", "reset", "timeout"],
)
def test_gemini_retries_network_errors(fake_sdk, error):
    fake_sdk["replies"] = [error, error, '{"ok": 1}']
    assert make_client().generate_json(system="s", parts=["x"], schema={}).data == {"ok": 1}
    assert len(fake_sdk["calls"]) == 3


def test_network_error_after_all_retries_is_typed(fake_sdk):
    fake_sdk["replies"] = [OSError(11001, "getaddrinfo failed")] * 5
    client = make_client(max_retries=4)
    with pytest.raises(LLMNetworkError, match="after 5 attempt"):
        client.generate_json(system="s", parts=["x"], schema={})
    assert len(client.delays) == 4 and sum(client.delays) < 40


def test_gemini_does_not_retry_client_errors(fake_sdk):
    fake_sdk["replies"] = [_Err(400)]
    with pytest.raises(LLMError, match="HTTP 400"):
        make_client().generate_json(system="s", parts=["x"], schema={})
    assert len(fake_sdk["calls"]) == 1


def test_gemini_gives_up_after_max_retries(fake_sdk):
    fake_sdk["replies"] = [_Err(429)] * 3
    with pytest.raises(LLMError):
        make_client(max_retries=2).generate_json(system="s", parts=["x"], schema={})


@pytest.mark.parametrize("bad", ["", "not json", "[1, 2]"])
def test_gemini_rejects_unusable_replies(fake_sdk, bad):
    fake_sdk["replies"] = [bad]
    with pytest.raises(LLMError):
        make_client().generate_json(system="s", parts=["x"], schema={})


def test_error_classification():
    ConnectError = type("ConnectError", (Exception,), {})
    assert classify_error(ConnectError("x")) == "network"
    assert classify_error(OSError(11001, "getaddrinfo failed")) == "network"
    assert classify_error(_Err(503)) == "retryable"
    assert classify_error(_Err(400)) == "fatal"
    assert classify_error(ValueError("bad schema")) == "fatal"
    wrapped = RuntimeError("sdk wrapper")
    wrapped.__cause__ = OSError(10051, "unreachable")
    assert classify_error(wrapped) == "network"


# --- cache ---------------------------------------------------------------------------


class CountingClient:
    model = "m"

    def __init__(self):
        self.calls = 0

    def generate_json(self, *, system, parts, schema):
        self.calls += 1
        return LLMResponse({"n": self.calls}, CallStats(model="m", seconds=12.5, attempts=[{"seconds": 12.5}]))


def test_cache_hits_on_identical_request(tmp_path):
    inner = CountingClient()
    client = CachingClient(inner, tmp_path)
    args = dict(system="s", parts=[ImagePart(b"img", "image/png"), "t"], schema={"a": 1})
    first = client.generate_json(**args)
    second = client.generate_json(**args)
    assert first.data == second.data == {"n": 1}
    assert inner.calls == 1 and client.hits == 1
    assert not first.stats.cached and first.stats.seconds == 12.5
    assert second.stats.cached and second.stats.original["seconds"] == 12.5  # original timing kept


def test_cache_reads_first_format_entries(tmp_path):
    client = CachingClient(CountingClient(), tmp_path)
    args = dict(system="s", parts=["t"], schema={})
    (tmp_path / f"{client.key(**args)}.json").write_text('{"legacy": true}', encoding="utf-8")
    out = client.generate_json(**args)
    assert out.data == {"legacy": True} and out.stats.cached and out.stats.original is None


@pytest.mark.parametrize(
    "change",
    [
        {"system": "other"},
        {"parts": [ImagePart(b"img2", "image/png"), "t"]},
        {"schema": {"a": 2}},
    ],
)
def test_cache_misses_when_anything_changes(tmp_path, change):
    inner = CountingClient()
    client = CachingClient(inner, tmp_path)
    base = dict(system="s", parts=[ImagePart(b"img", "image/png"), "t"], schema={"a": 1})
    client.generate_json(**base)
    client.generate_json(**{**base, **change})
    assert inner.calls == 2


# --- deadline and overload --------------------------------------------------------------------


def test_network_errors_keep_the_short_backoff(fake_sdk):
    fake_sdk["replies"] = [ConnectionResetError("reset"), ConnectionResetError("reset"), '{"ok": 1}']
    client = make_client()
    client.generate_json(system="s", parts=["x"], schema={})
    assert 2 <= client.delays[0] < 3 and 4 <= client.delays[1] < 5


def test_overload_honours_retry_after(fake_sdk):
    class Busy(Exception):
        code = 503
        response = types.SimpleNamespace(headers={"retry-after": "42"})

    fake_sdk["replies"] = [Busy("high demand"), '{"ok": 1}']
    client = make_client()
    client.generate_json(system="s", parts=["x"], schema={})
    assert client.delays == [42.0]


def test_overload_status_read_from_the_message(fake_sdk):
    """The Interactions API error carries the code only in its text: 'Error code: 503 - {...}'."""
    msg = "Error code: 503 - {'error': {'message': 'gemini-3.8-flash is currently experiencing high demand'}}"
    fake_sdk["replies"] = [RuntimeError(msg), '{"ok": 1}']
    client = make_client()
    client.generate_json(system="s", parts=["x"], schema={})
    assert 15 <= client.delays[0] < 17


def test_deadline_caps_an_attempt_and_rebuilds_the_client(fake_sdk, monkeypatch):
    """A call that never answers is cut off at the deadline, then retried on a fresh client."""
    import threading

    release = threading.Event()
    calls = {"n": 0}

    class Interactions:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                release.wait(5)  # hangs far past the deadline
                raise ConnectionError("closed")
            return types.SimpleNamespace(output_text='{"ok": 1}', usage=None)

    built = []

    class Client:
        def __init__(self, api_key, http_options=None):
            built.append(self)
            self.interactions = Interactions()

    monkeypatch.setattr(sys.modules["google.genai"], "Client", Client)
    client = GeminiClient("k", "m", sleep=lambda s: None, deadline_s=0.2)
    import time as _t

    t0 = _t.perf_counter()
    out = client.generate_json(system="s", parts=["x"], schema={})
    elapsed = _t.perf_counter() - t0
    release.set()
    assert out.data == {"ok": 1}
    assert elapsed < 2  # not the 5 s the hung request would have taken
    assert len(built) == 2  # the hung client was replaced
    assert "no reply within" in out.stats.attempts[0]["error"]
    assert out.stats.attempts[0]["error"].startswith("network:")


def test_deadline_exceeded_on_every_attempt_is_a_network_failure(fake_sdk, monkeypatch):
    import threading

    gate = threading.Event()

    class Interactions:
        def create(self, **kwargs):
            gate.wait(5)
            raise ConnectionError("closed")

    class Client:
        def __init__(self, api_key, http_options=None):
            self.interactions = Interactions()

    monkeypatch.setattr(sys.modules["google.genai"], "Client", Client)
    client = GeminiClient("k", "m", sleep=lambda s: None, deadline_s=0.05, max_retries=1)
    with pytest.raises(LLMNetworkError, match="after 2 attempt"):
        client.generate_json(system="s", parts=["x"], schema={})
    gate.set()
