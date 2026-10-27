"""Null handling end to end, with a mocked Gemini: a field that is not in the document must
come out null with low confidence, never invented, never confidently empty."""

import copy
from pathlib import Path

import pytest

from conftest import classified, field
from medextract.config import NULL_CONFIDENCE_MAX
from medextract.confidence import calibrate
from medextract.evaluate import evaluate_document
from medextract.ingest import load_document
from medextract.pipeline import process_document
from medextract.schemas import LabReportFields

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
SCAN = SAMPLES / "documents" / "prescription_02.png"


def rx(**overrides):
    reply = {
        "doctor_name": field("Dr. Hoang Duc Anh"), "patient_name": field("Vu Thanh Tung"), "date": field("22/09/2024"),
        "medications": field([
            {"name": field("Omeprazole"), "dosage": field("20 mg"), "frequency": field("once daily"),
             "duration": field("14 days"), "quantity": field("14 capsules")},
            {"name": field("Salbutamol inhaler"), "dosage": field("100 mcg/puff"), "frequency": field("as needed"),
             "duration": field(None, 0.0), "quantity": field(None, 0.0)},
        ]),
    }
    reply.update(overrides)
    return reply


def run(mock_gemini, extraction, second=None):
    model = mock_gemini(classified("prescription"), extraction, second)
    return process_document(load_document(SCAN), model)


def test_nulls_from_the_model_stay_null(mock_gemini):
    out = run(mock_gemini, rx())
    med = out.result.fields.medications.value[1]
    assert med.duration.value is None and med.quantity.value is None
    assert med.duration.confidence == 0.0


@pytest.mark.parametrize("confidence", [0.21, 0.5, 0.99])
def test_confident_null_is_capped(mock_gemini, confidence):
    """The model says 'not there' with high confidence: kept null, confidence capped at 0.2."""
    out = run(mock_gemini, rx(doctor_name=field(None, confidence)))
    assert out.result.fields.doctor_name.value is None
    assert out.result.fields.doctor_name.confidence == NULL_CONFIDENCE_MAX
    assert any("doctor_name: null with confidence" in a for a in out.trace["extraction"]["adjustments"])


@pytest.mark.parametrize("blank", ["", "   ", []])
def test_blank_values_become_null(mock_gemini, blank):
    reply = rx()
    reply["medications"]["value"][0]["duration"] = field(blank, 0.9)
    out = run(mock_gemini, reply)
    duration = out.result.fields.medications.value[0].duration
    assert duration.value is None and duration.confidence <= NULL_CONFIDENCE_MAX


def test_empty_medication_list_is_null(mock_gemini):
    out = run(mock_gemini, rx(medications=field([], 0.9)))
    assert out.result.fields.medications.value is None
    assert out.result.fields.medications.confidence <= NULL_CONFIDENCE_MAX


def test_missing_key_in_reply_fails_safe_to_all_null(mock_gemini):
    reply = rx()
    del reply["doctor_name"]  # the model left a field out instead of returning null
    out = run(mock_gemini, reply)
    result = out.result.model_dump(mode="json")
    assert [i["code"] for i in result["validation_errors"]] == ["EXTRACTION_FAILED"]
    assert all(v["value"] is None for v in result["fields"].values())  # nothing half-trusted


def test_null_vs_value_across_two_readings_lowers_confidence(mock_gemini):
    """Scan: one reading finds a duration, the other doesn't → the found value is not trusted."""
    first = rx()
    first["medications"]["value"][1]["duration"] = field("5 days", 0.9)  # possibly invented
    out = run(mock_gemini, first, rx())
    duration = out.result.fields.medications.value[1].duration
    assert duration.value == "5 days" and duration.confidence == 0.4
    assert "medications[1].duration" in {a["field"] for a in out.trace["confidence"]["adjustments"]}


def test_both_readings_null_is_agreement(mock_gemini):
    out = run(mock_gemini, rx(), rx())
    assert out.trace["confidence"]["adjustments"] == []


def test_calibration_never_touches_null_fields():
    fields = LabReportFields.model_validate({
        "lab_name": field("Lab"), "patient_name": field(None, 0.1), "date": field("01/02/2024"),
        "tests": field(None, 0.0),
    })
    report = calibrate(fields, [], ["Lab 01/02/2024"])
    assert report.adjustments == []
    assert fields.patient_name.confidence == 0.1


def test_evaluator_scores_nulls_both_ways():
    import json

    gt = json.loads((SAMPLES / "ground_truth" / "prescription_02.json").read_text(encoding="utf-8"))

    def wrap(v):
        if isinstance(v, dict) and "printed" in v:
            v = v["printed"]
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            v = [{k: wrap(x) for k, x in row.items()} for row in v]
        return {"value": v, "confidence": 0.0 if v is None else 0.95}

    perfect = {"document_type": "prescription", "confidence": 0.9, "validation_errors": [],
               "fields": {k: wrap(v) for k, v in gt["fields"].items()}}
    outcomes = {f.field: f.outcome for f in evaluate_document("prescription_02", gt, perfect).fields}
    assert outcomes["medications[1].duration"] == "correct_null"

    invented = copy.deepcopy(perfect)
    invented["fields"]["medications"]["value"][1]["duration"] = {"value": "7 days", "confidence": 0.9}
    outcomes = {f.field: f.outcome for f in evaluate_document("prescription_02", gt, invented).fields}
    assert outcomes["medications[1].duration"] == "hallucinated"
