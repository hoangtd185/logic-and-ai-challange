"""Web page: the upload service (no framework needed) and the FastAPI layer (needs FastAPI)."""

import copy
import json
from pathlib import Path

import pytest

from medextract.ingest import IngestError
from medextract.llm.client import CallStats, LLMNetworkError, LLMResponse
from medextract.web.service import analyze

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
STATIC = Path(__file__).resolve().parents[2] / "src" / "medextract" / "web" / "static"


def f(value, confidence=0.95):
    return {"value": value, "confidence": confidence}


LAB = {
    "lab_name": f("Mekong Clinical Laboratory"), "patient_name": f("Le Thi Hoa"), "date": f("03/08/2024"),
    "tests": f([{"test_name": f("Hemoglobin"), "result": f(11.2), "unit": f("g/dL"),
                 "reference_range": f("12.0-15.5"), "flag": f("low")}], 0.9),
}


class Scripted:
    model = "fake"

    def __init__(self, *replies, error=None):
        self.replies, self.error, self.calls = list(replies), error, 0

    def generate_json(self, *, system, parts, schema):
        if self.error:
            raise self.error
        self.calls += 1
        return LLMResponse(self.replies[min(self.calls, len(self.replies)) - 1], CallStats(model="fake", seconds=2.0))


def scan_bytes():
    return (SAMPLES / "documents" / "lab_02.png").read_bytes()


def test_analyze_returns_envelope_and_meta():
    second = copy.deepcopy(LAB)
    second["patient_name"] = f("Le Thi Hoa B")  # readings disagree on one field
    out = analyze(scan_bytes(), "lab_02.png",
                  Scripted({"evidence": "LABORATORY REPORT title", "document_type": "lab_report", "confidence": 0.98},
                           LAB, second))
    assert list(out["result"]) == ["document_type", "confidence", "fields", "validation_errors"]
    meta = out["meta"]
    assert meta["confidence_check"] == "double_reading"
    assert meta["lowered"]["patient_name"]["to"] == 0.4
    assert meta["classification_evidence"] == "LABORATORY REPORT title"
    assert meta["low_confidence_threshold"] == 0.6 and meta["from_cache"] is False
    json.dumps(out)  # serialisable as-is


def test_analyze_unknown_document():
    out = analyze(scan_bytes(), "bill.png", Scripted({"evidence": "ELECTRICITY BILL", "document_type": "unknown",
                                                      "confidence": 0.95}))
    assert out["result"]["document_type"] == "unknown" and out["meta"]["lowered"] == {}


def test_analyze_rejects_unsupported_files():
    with pytest.raises(IngestError):
        analyze(b"PK\x03\x04 zip", "notes.zip", Scripted())


def test_page_is_self_contained():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/api/extract" in html and "<script src" not in html and "stylesheet" not in html


# --- FastAPI layer -------------------------------------------------------------------------


@pytest.fixture
def web():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from medextract.web.app import create_app

    def make(client):
        return TestClient(create_app(lambda: client))

    return make


def test_index_served(web):
    response = web(Scripted()).get("/")
    assert response.status_code == 200 and "Medical Document Extractor" in response.text


def test_extract_endpoint(web):
    client = web(Scripted({"evidence": "x", "document_type": "lab_report", "confidence": 0.9}, LAB))
    response = client.post("/api/extract", files={"file": ("lab_02.png", scan_bytes(), "image/png")})
    assert response.status_code == 200
    assert response.json()["result"]["document_type"] == "lab_report"


def test_unsupported_file_is_400(web):
    response = web(Scripted()).post("/api/extract", files={"file": ("a.txt", b"hello", "text/plain")})
    assert response.status_code == 400 and "unsupported" in response.json()["detail"]


def test_network_failure_is_503(web):
    client = web(Scripted(error=LLMNetworkError("getaddrinfo failed")))
    response = client.post("/api/extract", files={"file": ("lab_02.png", scan_bytes(), "image/png")})
    assert response.status_code == 503 and "network" in response.json()["detail"]


def test_missing_api_key_is_a_clear_500(web):
    from fastapi.testclient import TestClient

    from medextract.config import ConfigError
    from medextract.web.app import create_app

    def no_key():
        raise ConfigError("GEMINI_API_KEY is not set")

    response = TestClient(create_app(no_key)).post(
        "/api/extract", files={"file": ("lab_02.png", scan_bytes(), "image/png")})
    assert response.status_code == 500 and "GEMINI_API_KEY" in response.json()["detail"]
