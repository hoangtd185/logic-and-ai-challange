"""Batch runs with persistent per-document state.

``results/runs.json`` records the latest outcome of every document ever run into
that folder (ok / partial / failed / skipped, timings, token usage, error). It is written
after each document, and ``results/summary.md`` is rebuilt from it each time, so
an interrupted batch still leaves an accurate summary.

- ``partial``: the document was extracted, but its scan double-check (second
  reading) failed, so its confidences are the model's own. It counts as not done.
- ``only_failed``: run only documents without a complete result from the same
  model (failed, skipped or partial). Complete ones are skipped outright; reruns
  are cheap anyway because model replies are cached.
- Early stop: after ``max_consecutive_network_failures`` documents in a row fail
  with network errors, the remaining documents are marked ``skipped`` instead of
  each burning through its own retries.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from medextract.config import DEFAULT_MAX_CONSECUTIVE_NETWORK_FAILURES
from medextract.ingest import IngestError
from medextract.llm.client import LLMNetworkError
from medextract.pipeline import PipelineOutput

SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
RUNS_FILE = "runs.json"


def find_documents(inputs: list[str | Path]) -> list[Path]:
    files: list[Path] = []
    for item in map(Path, inputs):
        candidates = sorted(item.iterdir()) if item.is_dir() else [item]
        files += [p for p in candidates if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES]
    return files


@dataclass
class BatchReport:
    processed: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)  # extracted, but the scan double-check failed
    failed: list[str] = field(default_factory=list)
    skipped_ok: list[str] = field(default_factory=list)  # already done (only_failed)
    stopped_early: bool = False
    not_attempted: list[str] = field(default_factory=list)  # left over after an early stop


class RunState:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.path = out_dir / RUNS_FILE
        self.documents: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            self.documents = json.loads(self.path.read_text(encoding="utf-8")).get("documents", {})

    def bootstrap_from_results(self, files: list[Path], model: str) -> list[str]:
        """Adopt results written before runs.json existed (same model, no extraction error)."""
        adopted = []
        for path in files:
            if path.stem in self.documents:
                continue
            result, trace = self.out_dir / f"{path.stem}.json", self.out_dir / "raw" / f"{path.stem}.json"
            if not (result.is_file() and trace.is_file()):
                continue
            t = json.loads(trace.read_text(encoding="utf-8"))
            if t.get("model") != model or (t.get("extraction") or {}).get("error"):
                continue
            r = json.loads(result.read_text(encoding="utf-8"))
            self.documents[path.stem] = {
                "file": path.as_posix(), "status": "ok", "model": model,
                "document_type": r["document_type"], "confidence": r["confidence"],
                "note": "adopted from results written before run tracking",
                "updated": _now(),
            }
            adopted.append(path.stem)
        return adopted

    def is_done(self, path: Path, model: str) -> bool:
        rec = self.documents.get(path.stem)
        return bool(
            rec and rec.get("status") == "ok" and rec.get("model") == model
            and not check_failed(rec)
            and (self.out_dir / f"{path.stem}.json").is_file()
        )

    def record(self, stem: str, record: dict[str, Any]) -> None:
        self.documents[stem] = {**record, "updated": _now()}
        self.save()

    def save(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        data = {"documents": dict(sorted(self.documents.items()))}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        (self.out_dir / "summary.md").write_text(render_summary(self.documents), encoding="utf-8")
        (self.out_dir / "confidence_report.md").write_text(render_confidence_report(self.documents), encoding="utf-8")


def run_batch(
    files: list[Path],
    process: Callable[[Path], PipelineOutput],
    out_dir: Path,
    model: str,
    only_failed: bool = False,
    max_consecutive_network_failures: int = DEFAULT_MAX_CONSECUTIVE_NETWORK_FAILURES,
    echo: Callable[[str], None] = print,
) -> BatchReport:
    out_dir = Path(out_dir)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    state = RunState(out_dir)
    report = BatchReport()

    if only_failed:
        adopted = state.bootstrap_from_results(files, model)
        if adopted:
            echo(f"adopted {len(adopted)} earlier result(s): {', '.join(adopted)}")
        report.skipped_ok = [p.stem for p in files if state.is_done(p, model)]
        files = [p for p in files if not state.is_done(p, model)]
        echo(f"--only-failed: {len(report.skipped_ok)} already done, {len(files)} to run")

    network_streak = 0
    for index, path in enumerate(files):
        echo(f"- {path.name} ... ")
        report.processed.append(path.stem)
        try:
            out = process(path)
        except (IngestError, LLMNetworkError, Exception) as exc:  # noqa: B014 - order documents intent
            kind = "network" if isinstance(exc, LLMNetworkError) else "ingest" if isinstance(exc, IngestError) else "error"
            network_streak = network_streak + 1 if kind == "network" else 0
            report.failed.append(path.stem)
            state.record(path.stem, {
                "file": path.as_posix(), "status": "failed", "model": model,
                "error_kind": kind, "error": str(exc)[:500],
            })
            echo(f"    FAILED ({kind}): {exc}")
            if kind == "network" and network_streak >= max_consecutive_network_failures:
                rest = files[index + 1:]
                for p in rest:
                    state.record(p.stem, {
                        "file": p.as_posix(), "status": "skipped", "model": model,
                        "error_kind": "network",
                        "error": f"batch stopped after {network_streak} consecutive network failures",
                    })
                report.stopped_early = True
                report.not_attempted = [p.stem for p in rest]
                echo(f"stopping: {network_streak} documents in a row failed with network errors; "
                     f"{len(rest)} not attempted. Fix the connection and rerun with --only-failed.")
                break
            continue

        network_streak = 0
        result = out.result.model_dump(mode="json")
        (out_dir / f"{path.stem}.json").write_text(_dump(result) + "\n", encoding="utf-8")
        (out_dir / "raw" / f"{path.stem}.json").write_text(_dump(out.trace) + "\n", encoding="utf-8")
        record = success_record(path, result, out.trace, model)
        state.record(path.stem, record)
        report.succeeded.append(path.stem)
        lowered, issues = len(record["lowered"]), record["issues"]
        line = (f"    done: {record['document_type']} ({record['confidence']:.2f}), {lowered} field(s) lowered, "
                f"{', '.join(issues) if issues else 'no validation errors'}")
        if record["status"] == "partial":
            report.partial.append(path.stem)
            line += "  [partial: scan double-check failed; --only-failed will retry it]"
        echo(line)

    state.save()
    return report


# --- records -----------------------------------------------------------------------------


def success_record(path: Path, result: dict[str, Any], trace: dict[str, Any], model: str) -> dict[str, Any]:
    leaves = list(leaf_fields(result["fields"]))
    calls = []
    for step in ("classification", "extraction", "second_reading"):
        block = trace.get(step) or {}
        if block.get("call"):
            calls.append(call_summary(step, block["call"]))
    second_error = (trace.get("second_reading") or {}).get("error")
    return {
        "file": path.as_posix(), "status": "partial" if second_error else "ok", "model": model,
        "document_type": result["document_type"], "confidence": result["confidence"],
        "null_fields": f"{sum(1 for _, f in leaves if f['value'] is None)}/{len(leaves)}",
        "issues": [i["code"] for i in result["validation_errors"]],
        "validation_errors": result["validation_errors"],
        "adjustments": len((trace.get("extraction") or {}).get("adjustments", [])),
        "confidence_method": (trace.get("confidence") or {}).get("method"),
        "confidence_notes": (trace.get("confidence") or {}).get("notes", [])
        + ([f"second reading failed: {second_error}"] if second_error else []),
        "lowered": [
            {"field": a["field"], "from": a["model_confidence"], "to": a["final_confidence"], "reasons": a["reasons"]}
            for a in (trace.get("confidence") or {}).get("adjustments", [])
        ],
        "pages": trace.get("pages"),
        "wall_seconds": trace.get("seconds"),
        "calls": calls,
    }


def check_failed(record: dict[str, Any]) -> bool:
    """The scan double-check failed (also recognises records written before 'partial' existed)."""
    return record.get("status") == "partial" or any(
        str(n).startswith("second reading failed") for n in record.get("confidence_notes") or []
    )


def call_summary(step: str, call: dict[str, Any]) -> dict[str, Any]:
    """Timing of the real model call; for a cache hit, the call that filled the cache."""
    source = call.get("original") if call.get("cached") else call
    summary: dict[str, Any] = {"step": step, "cached": bool(call.get("cached"))}
    if source:
        summary.update(
            seconds=source.get("seconds"),
            attempts=len(source.get("attempts") or []),
            http_requests=source.get("http_requests"),
            request_kb=round((source.get("request_bytes") or 0) / 1024),
            usage=source.get("usage"),
        )
    return summary


def describe_calls(calls: list[dict[str, Any]]) -> str:
    parts = []
    for c in calls:
        name = {"classification": "classify", "extraction": "extract", "second_reading": "re-read"}[c["step"]]
        if c.get("seconds") is None:
            parts.append(f"{name} cached (no timing recorded)")
            continue
        text = f"{name} {c['seconds']:.1f}s"
        extras = []
        if c.get("cached"):
            extras.append("cached")
        if (c.get("attempts") or 1) > 1:
            extras.append(f"{c['attempts']} attempts")
        if (c.get("http_requests") or 0) > (c.get("attempts") or 1):
            extras.append(f"{c['http_requests']} HTTP requests")
        parts.append(text + (f" ({', '.join(extras)})" if extras else ""))
    return " · ".join(parts)


def render_summary(documents: dict[str, dict[str, Any]]) -> str:
    models = sorted({r.get("model", "?") for r in documents.values()})
    lines = [
        f"# Batch results ({', '.join(models)})",
        "",
        "Model time is the real API time; for cached calls it is the time of the call that filled the cache.",
        "",
        "| Document | Status | Type | Confidence | Null fields | Check | Lowered | Classify | Extract | Re-read | Tokens in / out / thinking | Validation |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for stem, r in sorted(documents.items()):
        name = Path(r.get("file", stem)).name
        if r.get("status") not in ("ok", "partial"):
            lines.append(f"| {name} | **{r.get('status')}** | - | - | - | - | - | - | - | - | - | {r.get('error_kind')}: {_cell(r.get('error', ''))} |")
            continue
        calls = {c["step"]: c for c in r.get("calls", [])}
        lines.append(
            f"| {name} | {'**partial**' if check_failed(r) else 'ok'} | {r['document_type']} | {r['confidence']:.2f} | {r.get('null_fields', '-')} "
            f"| {_METHOD.get(r.get('confidence_method'), '-')} | {len(r.get('lowered') or [])} "
            f"| {_time_cell(calls.get('classification'))} | {_time_cell(calls.get('extraction'))} "
            f"| {_time_cell(calls.get('second_reading'))} "
            f"| {_tokens_cell(r.get('calls', []))} | {', '.join(r.get('issues') or []) or '-'} |"
        )
    return "\n".join(lines) + "\n"


_METHOD = {"text_layer": "text layer", "double_reading": "2 readings", "model_only": "model only", "failed": "failed"}


def render_confidence_report(documents: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Confidence report",
        "",
        "Final confidence = min(model score, validation cap, text-layer cap for PDFs, "
        "two-reading agreement cap for scans). Only fields whose confidence was lowered are listed.",
        "",
    ]
    for stem, r in sorted(documents.items()):
        name = Path(r.get("file", stem)).name
        if r.get("status") not in ("ok", "partial"):
            lines += [f"## {name}", "", f"Not processed ({r.get('status')}).", ""]
            continue
        method = _METHOD.get(r.get("confidence_method"), "-")
        lowered = r.get("lowered") or []
        issues = r.get("validation_errors") or []
        lines += [f"## {name} ({r['document_type']}, check: {method})", ""]
        for note in r.get("confidence_notes") or []:
            lines.append(f"> {note}")
        if issues:
            lines.append("Validation: " + "; ".join(f"`{i['code']}` on `{i['field']}` ({i['severity']}): {i['message']}" for i in issues))
            lines.append("")
        if not lowered:
            lines += ["No confidence lowered.", ""]
            continue
        lines += ["| Field | Model | Final | Why |", "|---|---|---|---|"]
        for a in lowered:
            lines.append(f"| `{a['field']}` | {a['from']:.2f} | {a['to']:.2f} | {_cell('; '.join(a['reasons']))} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def _time_cell(call: dict[str, Any] | None) -> str:
    if not call:
        return "-"
    if call.get("seconds") is None:
        return "cached"
    retry = f", {call['attempts']} tries" if (call.get("attempts") or 1) > 1 else ""
    return f"{call['seconds']:.1f}s{retry}"


def _tokens_cell(calls: list[dict[str, Any]]) -> str:
    totals = {"total_input_tokens": 0, "total_output_tokens": 0, "total_thought_tokens": 0}
    seen = False
    for c in calls:
        for k in totals:
            v = (c.get("usage") or {}).get(k)
            if isinstance(v, int):
                totals[k] += v
                seen = True
    return " / ".join(f"{v:,}" for v in totals.values()) if seen else "-"


def leaf_fields(node: dict[str, Any], path: str = ""):
    """Yield (path, {value, confidence}) for every leaf field, including list rows."""
    for name, f in node.items():
        here = f"{path}.{name}" if path else name
        value = f["value"]
        if isinstance(value, dict):
            yield from leaf_fields(value, here)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for i, item in enumerate(value):
                yield from leaf_fields(item, f"{here}[{i}]")
        else:
            yield here, f


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")[:300]


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
