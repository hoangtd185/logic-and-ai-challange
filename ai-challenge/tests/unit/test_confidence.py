"""Confidence calibration: validation caps, text-layer check, two-reading check."""

import json
from pathlib import Path

import pytest

from medextract.config import (
    CONF_CAP_NOT_IN_TEXT,
    CONF_CAP_PARTLY_IN_TEXT,
    CONF_CAP_READINGS_DISAGREE,
    CONF_CAP_VALIDATION_ERROR,
)
from medextract.confidence import calibrate, readings_agree
from medextract.schemas import DocumentType, FIELDS_BY_TYPE, ReceiptFields
from medextract.validation import validate

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def wrap(value, confidence=0.95):
    if isinstance(value, dict) and "printed" in value:
        value = value["printed"]
    elif isinstance(value, dict):
        value = {k: wrap(v, confidence) for k, v in value.items()}
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        value = [{k: wrap(v, confidence) for k, v in row.items()} for row in value]
    return {"value": value, "confidence": 0.0 if value is None else confidence}


def receipt(**overrides):
    data = {
        "hospital_name": wrap("Siam Riverside Hospital"), "patient_name": wrap("Mr. Anan Wongsakul"),
        "date": wrap("12/03/2024"), "payment_method": wrap("Credit card"), "grand_total": wrap(3252.0),
        "items": wrap([{"description": "Consultation - Internal Medicine", "quantity": 1,
                        "unit_price": 1200.0, "total": 1200.0},
                       {"description": "Amoxicillin 500 mg capsule", "quantity": 21,
                        "unit_price": 12.0, "total": 252.0}]),
    }
    data.update(overrides)
    return ReceiptFields.model_validate(data)


TEXT = """SIAM RIVERSIDE HOSPITAL  RECEIPT
Receipt no. SRH-RC-2403-00871   Date 12/03/2024
Patient Mr. Anan Wongsakul
1 Consultation - Internal Medicine 1 1,200.00 1,200.00
2 Amoxicillin 500 mg capsule 21 12.00 252.00
Grand total 3,252.00 THB   Payment method Credit card"""


def test_values_present_in_text_are_untouched():
    fields = receipt()
    report = calibrate(fields, [], [TEXT])
    assert report.method == "text_layer" and report.adjustments == []
    assert fields.grand_total.confidence == 0.95


def test_value_missing_from_text_is_lowered():
    fields = receipt(payment_method=wrap("Bank transfer", 0.9), grand_total=wrap(3525.0, 0.97))
    report = calibrate(fields, [], [TEXT])
    lowered = {a.field: a for a in report.adjustments}
    assert fields.payment_method.confidence == CONF_CAP_NOT_IN_TEXT
    assert fields.grand_total.confidence == CONF_CAP_NOT_IN_TEXT  # 3525 is not printed (3252 is)
    assert lowered["payment_method"].model_confidence == 0.9
    assert "not found in the PDF text layer" in lowered["payment_method"].reasons[0]


def test_long_text_partly_present():
    text = "Discharge instructions: keep the wound dry for 5 days. No heavy lifting for 2 weeks. Soft diet."
    fields = FIELDS_BY_TYPE[DocumentType.DISCHARGE_SUMMARY].model_validate({
        "hospital_name": wrap(None), "patient_name": wrap(None), "admission_date": wrap(None),
        "discharge_date": wrap(None), "diagnosis": wrap(None), "procedures_performed": wrap(None),
        "attending_physician": wrap(None),
        # 8 of 11 words printed (73%): partly found
        "discharge_instructions": wrap("Keep the wound dry for 5 days and call your surgeon"),
    })
    calibrate(fields, [], [text])
    assert fields.discharge_instructions.confidence == CONF_CAP_PARTLY_IN_TEXT


def test_short_value_must_match_fully():
    fields = receipt(patient_name=wrap("Mr. Anan Srisuk"))  # 2 of 3 words printed
    calibrate(fields, [], [TEXT])
    assert fields.patient_name.confidence == CONF_CAP_NOT_IN_TEXT


def test_validation_issue_lowers_the_field_and_its_related_fields():
    fields = receipt(grand_total=wrap(9999.0))
    issues = validate(DocumentType.RECEIPT, fields)
    report = calibrate(fields, issues, [TEXT + " 9,999.00"])
    lowered = {a.field for a in report.adjustments}
    assert lowered == {"grand_total", "items"}
    assert fields.grand_total.confidence == CONF_CAP_VALIDATION_ERROR


def test_final_is_the_lowest_cap_and_lists_every_reason():
    fields = receipt(grand_total=wrap(9999.0))
    issues = validate(DocumentType.RECEIPT, fields)
    report = calibrate(fields, issues, [TEXT])  # 9999 also not printed
    gt = next(a for a in report.adjustments if a.field == "grand_total")
    assert gt.final_confidence == min(CONF_CAP_VALIDATION_ERROR, CONF_CAP_NOT_IN_TEXT)
    assert len(gt.reasons) == 2


def test_never_raises_a_confidence():
    fields = receipt(hospital_name=wrap("Bangkok General", 0.1))
    calibrate(fields, [], [TEXT])
    assert fields.hospital_name.confidence == 0.1


def test_null_fields_are_left_alone():
    fields = receipt(payment_method=wrap(None))
    report = calibrate(fields, [], [TEXT])
    assert all(a.field != "payment_method" for a in report.adjustments)


def test_two_readings():
    first = receipt()
    second = receipt(grand_total=wrap(3352.0), items=wrap([{"description": "Consultation - Internal Medicine",
                                                             "quantity": 1, "unit_price": 1200.0, "total": 1200.0}]))
    report = calibrate(first, [], None, second)
    lowered = {a.field for a in report.adjustments}
    assert report.method == "double_reading"
    assert {"grand_total", "items", "items[1].description", "items[1].total"} <= lowered
    assert "hospital_name" not in lowered
    assert first.grand_total.confidence == CONF_CAP_READINGS_DISAGREE


def test_model_only_when_nothing_to_check():
    report = calibrate(receipt(), [], None, None)
    assert report.method == "model_only" and report.notes


@pytest.mark.parametrize(
    "a,b,same",
    [
        (3252.0, 3252.0, True), (3252.0, 3352.0, False), ("Mr. Anan", "mr.  anan", True),
        ("Dr Lim", "Dr Kim", False), (None, None, True), ("x", None, False), (["a", "b"], ["B", "a"], True),
        ("Keep the wound dry for five days and avoid lifting anything heavy.",
         "Keep the wound dry for five days, and avoid lifting anything heavy", True),
    ],
)
def test_readings_agree(a, b, same):
    assert readings_agree(a, b) is same


# --- against the real sample PDFs (needs PyMuPDF for the text layer) --------------------------


@pytest.mark.parametrize("stem", ["receipt_01", "receipt_02", "discharge_01", "discharge_02", "discharge_03",
                                  "lab_01", "prescription_01"])
def test_ground_truth_values_are_all_found_in_the_text_layer(stem):
    """No false alarms: a perfect extraction of a PDF loses confidence only to validation."""
    pytest.importorskip("pymupdf")
    from medextract.ingest import load_document

    gt = json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text(encoding="utf-8"))
    doc_type = DocumentType(gt["document_type"])
    fields = FIELDS_BY_TYPE[doc_type].model_validate({k: wrap(v) for k, v in gt["fields"].items()})
    doc = load_document(SAMPLES / "documents" / f"{stem}.pdf")
    report = calibrate(fields, [], [p.text for p in doc.pages])
    assert report.adjustments == [], [(a.field, a.reasons) for a in report.adjustments]
