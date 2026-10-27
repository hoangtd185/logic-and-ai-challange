"""What the web endpoint does, without the web framework, so it can be tested on its own."""

from __future__ import annotations

import threading
from typing import Any

from medextract.config import LOW_CONFIDENCE_THRESHOLD
from medextract.ingest import load_bytes
from medextract.llm.client import LLMClient
from medextract.pipeline import process_document

# The LLM clients keep per-call bookkeeping that is not thread-safe, and this is a
# single-user demo page: process one upload at a time.
_lock = threading.Lock()


def analyze(data: bytes, filename: str, client: LLMClient) -> dict[str, Any]:
    """Run the pipeline on uploaded bytes and return the result plus what the page needs.

    Raises IngestError for files that can't be read, LLMError / LLMNetworkError when
    the model can't be reached.
    """
    doc = load_bytes(data, name=filename)
    with _lock:
        out = process_document(doc, client)
    trace = out.trace
    confidence = trace.get("confidence") or {}
    calls = [
        (trace.get(step) or {}).get("call") or {}
        for step in ("classification", "extraction", "second_reading")
        if trace.get(step)
    ]
    return {
        "result": out.result.model_dump(mode="json"),
        "meta": {
            "file": filename,
            "pages": trace.get("pages"),
            "has_text_layer": trace.get("has_text_layer"),
            "model": trace.get("model"),
            "seconds": trace.get("seconds"),
            "from_cache": bool(calls) and all(c.get("cached") for c in calls),
            "classification_evidence": ((trace.get("classification") or {}).get("raw") or {}).get("evidence"),
            "confidence_check": confidence.get("method"),
            "confidence_notes": confidence.get("notes", []),
            "lowered": {
                a["field"]: {"from": a["model_confidence"], "to": a["final_confidence"], "reasons": a["reasons"]}
                for a in confidence.get("adjustments", [])
            },
            "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
        },
    }
