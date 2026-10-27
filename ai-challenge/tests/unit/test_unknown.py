"""Documents that are none of the four types, with a mocked Gemini."""

import json
from pathlib import Path

import pytest

from conftest import classified
from medextract.batch import run_batch
from medextract.classify import classify
from medextract.evaluate import evaluate_document
from medextract.ingest import load_document
from medextract.llm.client import LLMError
from medextract.pipeline import process_document, process_file
from medextract.schemas import FIELDS_BY_TYPE, DocumentType

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
IMAGE = SAMPLES / "documents" / "lab_02.png"  # any readable image; the mock decides the type


def test_unknown_makes_one_call_and_returns_all_null(mock_gemini):
    model = mock_gemini(classified("unknown", 0.96))  # an extraction call would fail the test
    out = process_document(load_document(IMAGE), model)
    result = out.result.model_dump(mode="json")
    assert model.calls == ["classification"]
    assert result["document_type"] == "unknown" and result["confidence"] == 0.96
    assert set(result["fields"]) == set(FIELDS_BY_TYPE[DocumentType.UNKNOWN].model_fields)
    assert all(f == {"value": None, "confidence": 0.0} for f in result["fields"].values())
    assert result["validation_errors"] == []
    assert out.trace["extraction"] is None


def test_unknown_keeps_a_low_classification_confidence(mock_gemini):
    out = process_document(load_document(IMAGE), mock_gemini(classified("unknown", 0.35)))
    assert out.result.document_type == "unknown" and out.result.confidence == 0.35


def test_out_of_schema_document_type_is_a_model_error_not_a_crash(mock_gemini):
    model = mock_gemini({"evidence": "an invoice", "document_type": "invoice", "confidence": 0.9})
    with pytest.raises(LLMError, match="does not fit the schema"):
        classify(load_document(IMAGE), model)


def test_batch_records_unknown_as_success(mock_gemini, tmp_path):
    model = mock_gemini(classified("unknown"))
    out_dir = tmp_path / "results"
    report = run_batch([IMAGE], lambda p: process_file(p, model), out_dir, model.model, echo=lambda s: None)
    assert report.succeeded == ["lab_02"]
    record = json.loads((out_dir / "runs.json").read_text(encoding="utf-8"))["documents"]["lab_02"]
    assert record["status"] == "ok" and record["document_type"] == "unknown"
    assert "| unknown |" in (out_dir / "summary.md").read_text(encoding="utf-8")


def test_batch_records_a_bad_classification_as_failed(mock_gemini, tmp_path):
    model = mock_gemini({"evidence": "x", "document_type": "invoice", "confidence": 0.9})
    report = run_batch([IMAGE], lambda p: process_file(p, model), tmp_path, model.model, echo=lambda s: None)
    assert report.failed == ["lab_02"]


def ground_truth():
    return json.loads((SAMPLES / "negative" / "ground_truth" / "electricity_bill_01.json").read_text(encoding="utf-8"))


def test_evaluator_unknown_correct(mock_gemini):
    out = process_document(load_document(IMAGE), mock_gemini(classified("unknown")))
    doc = evaluate_document("electricity_bill_01", ground_truth(), out.result.model_dump(mode="json"))
    assert doc.type_correct and all(f.correct for f in doc.fields)


def test_evaluator_bill_misread_as_receipt_counts_invented_fields(mock_gemini):
    """If the bill is taken for a receipt, everything extracted from it is made up."""
    bill = {
        "hospital_name": {"value": "Lumen Electric Utility Co.", "confidence": 0.9},
        "patient_name": {"value": "Ms. Chalida Prasert", "confidence": 0.9},
        "date": {"value": "01/10/2024", "confidence": 0.9},
        "items": {"value": None, "confidence": 0.0},
        "grand_total": {"value": 1845.32, "confidence": 0.9},
        "payment_method": {"value": "Direct debit", "confidence": 0.9},
    }
    out = process_document(load_document(IMAGE), mock_gemini(classified("receipt", 0.6), bill))
    doc = evaluate_document("electricity_bill_01", ground_truth(), out.result.model_dump(mode="json"))
    assert not doc.type_correct
    assert sum(f.outcome == "hallucinated" for f in doc.fields) == 5
