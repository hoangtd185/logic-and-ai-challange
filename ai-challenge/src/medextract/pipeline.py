"""ingest → classify → extract (twice for scans) → validate → calibrate confidence → envelope.

Validation and calibration never fail the document: problems end up in
``validation_errors`` and in lowered confidences, and the trace records why.

``progress`` (optional) receives one line per step as it finishes, plus a notice
while a slow model call is still in flight, so a batch never looks stuck.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from medextract.classify import classify
from medextract.config import PROGRESS_FIRST_NOTICE_S, PROGRESS_HEARTBEAT_S, SECOND_READING_MAX_RETRIES
from medextract.confidence import calibrate
from medextract.extract import Extraction, ExtractionError, extract
from medextract.ingest import Document, load_document
from medextract.llm.client import CALL_LABEL, CallStats, LLMClient, LLMError
from medextract.schemas import (
    RESULT_BY_TYPE,
    DocumentType,
    ExtractionResult,
    IssueCode,
    Severity,
    ValidationIssue,
    empty_fields,
    unknown_result,
)
from medextract.validation import validate

log = logging.getLogger(__name__)

Progress = Callable[[str], None]


@dataclass
class PipelineOutput:
    result: ExtractionResult
    trace: dict[str, Any] = field(default_factory=dict)


def process_file(
    path: str | Path, client: LLMClient, double_read_scans: bool = True, progress: Progress | None = None
) -> PipelineOutput:
    return process_document(load_document(path), client, double_read_scans, progress)


def process_document(
    doc: Document, client: LLMClient, double_read_scans: bool = True, progress: Progress | None = None
) -> PipelineOutput:
    started = time.perf_counter()
    trace: dict[str, Any] = {
        "document": doc.name,
        "sha256": doc.sha256,
        "source_mime_type": doc.source_mime_type,
        "pages": len(doc.pages),
        "has_text_layer": doc.has_text_layer,
        "model": client.model,
    }
    notes = [p.note for p in doc.pages if p.note]
    if notes:
        trace["input_notes"] = notes

    t0 = time.perf_counter()
    with model_call("classify", progress):
        cls = classify(doc, client)
    trace["classification"] = {"raw": cls.raw, "seconds": _since(t0), "call": cls.stats.to_dict()}
    _report(progress, "classify", cls.stats, f"→ {cls.document_type.value} ({cls.confidence:.2f})")

    if cls.document_type is DocumentType.UNKNOWN:
        trace["extraction"] = None
        trace["seconds"] = _since(started)
        return PipelineOutput(unknown_result(cls.confidence), trace)

    result_model = RESULT_BY_TYPE[cls.document_type]
    t0 = time.perf_counter()
    try:
        with model_call("extract", progress):
            ext = extract(doc, cls.document_type, client)
    except ExtractionError as exc:
        trace["extraction"] = {
            "raw": exc.raw, "error": str(exc), "seconds": _since(t0),
            "call": exc.stats.to_dict() if exc.stats else None,
        }
        _say(progress, "extract", "reply did not fit the schema: all fields null (EXTRACTION_FAILED)")
        issue = ValidationIssue(field="fields", code=IssueCode.EXTRACTION_FAILED, message=str(exc).splitlines()[0])
        result = result_model(document_type=cls.document_type.value, confidence=cls.confidence,
                              fields=empty_fields(cls.document_type), validation_errors=[issue])
        trace["seconds"] = _since(started)
        return PipelineOutput(result, trace)
    trace["extraction"] = _extraction_trace(ext, _since(t0))
    _report(progress, "extract", ext.stats)

    second = None
    if double_read_scans and not doc.has_text_layer:
        second = _second_reading(doc, cls.document_type, client, trace, progress)

    issues = validate(cls.document_type, ext.fields)
    trace["validation"] = [i.model_dump(mode="json") for i in issues]
    try:
        report = calibrate(ext.fields, issues, [p.text for p in doc.pages], second.fields if second else None)
        trace["confidence"] = report.to_dict()
    except Exception as exc:  # calibration must not lose the document
        log.exception("confidence calibration failed")
        issues.append(ValidationIssue(field="fields", code=IssueCode.VALIDATION_FAILED,
                                      message=f"confidence calibration failed: {exc}", severity=Severity.WARNING))
        trace["confidence"] = {"method": "failed", "error": str(exc), "adjustments": []}

    result = result_model(
        document_type=cls.document_type.value,
        confidence=cls.confidence,
        fields=ext.fields,
        validation_errors=issues,
    )
    trace["seconds"] = _since(started)
    return PipelineOutput(result, trace)


def _second_reading(
    doc: Document, doc_type: DocumentType, client: LLMClient, trace: dict, progress: Progress | None
) -> Extraction | None:
    """Optional double-check for scans. A failure keeps the model's own confidences."""
    t0 = time.perf_counter()
    limit = getattr(client, "limited_retries", None)
    try:
        with (limit(SECOND_READING_MAX_RETRIES) if limit else contextlib.nullcontext()):
            with model_call("re-read scan", progress):
                second = extract(doc, doc_type, client, reading=2)
    except (ExtractionError, LLMError) as exc:
        trace["second_reading"] = {"error": str(exc), "seconds": _since(t0)}
        _say(progress, "re-read scan", f"failed ({_short_error(exc)}); keeping the model's own confidence")
        return None
    trace["second_reading"] = _extraction_trace(second, _since(t0))
    _report(progress, "re-read scan", second.stats)
    return second


# --- progress -------------------------------------------------------------------------------


@contextlib.contextmanager
def model_call(label: str, progress: Progress | None) -> Iterator[None]:
    """Label the call (for retry warnings) and, while it runs, report that it is still waiting."""
    token = CALL_LABEL.set(label)
    done = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        if done.wait(PROGRESS_FIRST_NOTICE_S):
            return
        _say(progress, label, "waiting for Gemini…")
        while not done.wait(PROGRESS_HEARTBEAT_S):
            _say(progress, label, f"still waiting ({time.monotonic() - started:.0f} s)…")

    thread = threading.Thread(target=heartbeat, daemon=True) if progress else None
    if thread:
        thread.start()
    try:
        yield
    finally:
        done.set()
        if thread:
            thread.join(timeout=1)
        CALL_LABEL.reset(token)


def _report(progress: Progress | None, label: str, stats: CallStats, extra: str = "") -> None:
    if progress is None:
        return
    if stats.cached:
        timing = "cached"
    else:
        timing = f"{stats.seconds:.1f} s"
        if len(stats.attempts) > 1:
            timing += f" ({len(stats.attempts)} attempts)"
    _say(progress, label, f"{timing} {extra}".rstrip())


def _say(progress: Progress | None, label: str, message: str) -> None:
    if progress is not None:
        progress(f"    {label:<13}{message}")


def _short_error(exc: Exception) -> str:
    text = str(exc)
    if "timed out" in text.lower():
        return "timed out"
    return text.splitlines()[0][:80]


def _extraction_trace(ext: Extraction, seconds: float) -> dict[str, Any]:
    return {"raw": ext.raw, "adjustments": ext.adjustments, "seconds": seconds, "call": ext.stats.to_dict()}


def _since(t0: float) -> float:
    return round(time.perf_counter() - t0, 2)
