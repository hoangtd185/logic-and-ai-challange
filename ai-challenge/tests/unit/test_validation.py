"""Validation rules, parsing, and a ground-truth check: a perfect extraction of each
sample must produce exactly the validation errors planted in that sample."""

import datetime as dt
import json
from pathlib import Path

import pytest

from medextract.parsing import numbers_in_text, parse_amount, parse_date
from medextract.schemas import FIELDS_BY_TYPE, DocumentType, IssueCode, ReceiptFields, DischargeSummaryFields
from medextract.validation import validate

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
GT_FILES = sorted((SAMPLES / "ground_truth").glob("*.json"))


def wrap(value, confidence=0.95):
    if isinstance(value, dict) and "printed" in value:
        value = value["printed"]
    elif isinstance(value, dict):
        value = {k: wrap(v, confidence) for k, v in value.items()}
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        value = [{k: wrap(v, confidence) for k, v in row.items()} for row in value]
    return {"value": value, "confidence": 0.0 if value is None else confidence}


def fields_from_ground_truth(gt):
    model = FIELDS_BY_TYPE[DocumentType(gt["document_type"])]
    return model.model_validate({k: wrap(v) for k, v in gt["fields"].items()})


@pytest.mark.parametrize("path", GT_FILES, ids=lambda p: p.stem)
def test_perfect_extraction_raises_exactly_the_planted_errors(path):
    gt = json.loads(path.read_text(encoding="utf-8"))
    doc_type = DocumentType(gt["document_type"])
    issues = validate(doc_type, fields_from_ground_truth(gt))
    found = {(i.field, i.code.value) for i in issues}
    expected = {(e["field"], e["code"]) for e in gt["expected_validation_errors"]}
    assert found == expected, [i.message for i in issues]


# --- rules ----------------------------------------------------------------------------


def receipt(items, grand_total, date="05/04/2024"):
    data = {
        "hospital_name": wrap("H"), "patient_name": wrap("P"), "date": wrap(date), "payment_method": wrap("Cash"),
        "grand_total": wrap(grand_total),
        "items": wrap([{"description": d, "quantity": q, "unit_price": p, "total": t} for d, q, p, t in items]),
    }
    return ReceiptFields.model_validate(data)


def codes(issues):
    return sorted((i.field, i.code.value, i.severity.value) for i in issues)


def test_total_mismatch_threshold():
    over = receipt([("a", 1, 92.0, 92.0)], 100.0)  # 8% apart
    under = receipt([("a", 1, 97.0, 97.0)], 100.0)  # 3% apart
    assert codes(validate(DocumentType.RECEIPT, over)) == [("grand_total", "TOTAL_MISMATCH", "error")]
    assert validate(DocumentType.RECEIPT, under) == []


def test_negative_and_zero_amounts():
    r = receipt([("refund", 1, -50.0, -50.0), ("free", 1, 0.0, 0.0)], -50.0)
    found = codes(validate(DocumentType.RECEIPT, r))
    assert ("grand_total", "NON_POSITIVE_AMOUNT", "error") in found
    assert ("items[0].total", "NON_POSITIVE_AMOUNT", "error") in found
    assert ("items[1].total", "NON_POSITIVE_AMOUNT", "warning") in found


def test_item_arithmetic():
    r = receipt([("x", 21, 12.0, 262.0)], 262.0)  # 21 x 12 = 252
    assert codes(validate(DocumentType.RECEIPT, r)) == [("items[0].total", "ITEM_TOTAL_MISMATCH", "warning")]


def test_total_not_checked_when_a_line_amount_is_missing():
    r = receipt([("x", 1, 10.0, None), ("y", 1, 10.0, 10.0)], 500.0)
    assert validate(DocumentType.RECEIPT, r) == []


def discharge(admission, discharge_date):
    data = {k: wrap("x") for k in DischargeSummaryFields.model_fields}
    data["admission_date"], data["discharge_date"] = wrap(admission), wrap(discharge_date)
    data["diagnosis"] = wrap({"primary": "d", "secondary": None})
    data["procedures_performed"] = wrap(["p"])
    return DischargeSummaryFields.model_validate(data)


def test_discharge_before_admission():
    issues = validate(DocumentType.DISCHARGE_SUMMARY, discharge("10/05/2024", "05/05/2024"))
    assert codes(issues) == [("discharge_date", "DATE_ORDER", "error")]
    assert validate(DocumentType.DISCHARGE_SUMMARY, discharge("10/05/2024", "10/05/2024")) == []


def test_invalid_date_skips_order_check():
    issues = validate(DocumentType.DISCHARGE_SUMMARY, discharge("26/02/2024", "30/02/2024"))
    assert codes(issues) == [("discharge_date", "INVALID_DATE", "error")]
    assert "30/02/2024" in issues[0].message


def test_a_crashing_rule_becomes_a_warning(monkeypatch):
    import medextract.validation as v

    def boom(fields):
        raise RuntimeError("bug")

    monkeypatch.setattr(v, "_check_items_sum", boom)
    issues = v.validate(DocumentType.RECEIPT, receipt([("a", 1, 1.0, 1.0)], 1.0))
    assert codes(issues) == [("fields", "VALIDATION_FAILED", "warning")]


# --- parsing --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,value",
    [
        ("15,750.00", 15750.0), ("1.500.000 đ", 1_500_000.0), ("1.500.000,50 VND", 1_500_000.5),
        ("8.750.000 VNĐ", 8_750_000.0), ("฿ 3,252", 3252.0), ("24,950.00 THB", 24950.0), ("12.50", 12.5),
        ("12,5", 12.5), ("1 500 000 ₫", 1_500_000.0), ("(250.00)", -250.0), ("-90", -90.0), ("0.9", 0.9),
        ("abc", None), ("1.5.0", None), ("1,23,456", None), ("", None),
    ],
)
def test_parse_amount(text, value):
    assert parse_amount(text) == value


def test_numbers_in_text_accepts_both_readings_of_ambiguous_groups():
    found = numbers_in_text("Total 3,252.00 THB, qty 21 x 12.00, 1.500")
    assert {3252.0, 21.0, 12.0, 1500.0, 1.5} <= found


@pytest.mark.parametrize(
    "raw,status,iso",
    [
        ("05/04/2024", "valid", "2024-04-05"),  # day-first
        ("30/02/2024", "invalid", None),
        ("2024-02-30", "invalid", None),
        ("15 มี.ค. 2567", "valid", "2024-03-15"),  # Thai month, Buddhist Era
        ("15/03/2567", "valid", "2024-03-15"),
        ("03/25/2024", "valid", "2024-03-25"),  # only month-first fits
        ("15 March 2024", "valid", "2024-03-15"),
        ("March 15, 2024", "valid", "2024-03-15"),
        ("ngày 15 tháng 05 năm 2024", "valid", "2024-05-15"),
        ("31/12/24", "valid", "2024-12-31"),
        ("01/01/1800", "invalid", None),
        ("yesterday", "unparseable", None),
    ],
)
def test_parse_date(raw, status, iso):
    parsed = parse_date(raw)
    assert parsed.status == status and parsed.iso == iso


def test_ground_truth_iso_dates_agree_with_parser():
    for path in GT_FILES:
        for name, value in json.loads(path.read_text(encoding="utf-8"))["fields"].items():
            if isinstance(value, dict) and "printed" in value:
                assert parse_date(value["printed"]).iso == value["iso"], (path.stem, name)


def test_issue_codes_are_known():
    assert {"INVALID_DATE", "DATE_ORDER", "NON_POSITIVE_AMOUNT", "TOTAL_MISMATCH", "ITEM_TOTAL_MISMATCH"} <= {
        c.value for c in IssueCode
    }
