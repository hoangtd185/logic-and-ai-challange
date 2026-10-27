"""Validation rules in depth: the 5% boundary, amounts, dates. Pure functions, no model."""

import pytest

from medextract.config import TOTAL_MISMATCH_TOLERANCE
from medextract.schemas import (
    DischargeSummaryFields,
    DocumentType,
    LabReportFields,
    PrescriptionFields,
    ReceiptFields,
)
from medextract.validation import validate


def w(value, confidence=0.95):
    empty = value is None or value == []  # an empty list means "not found", like null
    return {"value": value, "confidence": 0.0 if empty else confidence}


def receipt(line_totals, grand_total, **extra):
    data = {
        "hospital_name": w("H"), "patient_name": w("P"), "date": w("05/04/2024"), "payment_method": w("Cash"),
        "grand_total": w(grand_total),
        "items": w([{"description": w(f"line {i}"), "quantity": w(1), "unit_price": w(t), "total": w(t)}
                    for i, t in enumerate(line_totals)] if line_totals is not None else None),
    }
    data.update(extra)
    return ReceiptFields.model_validate(data)


def codes(issues):
    return sorted((i.field, i.code.value, i.severity.value) for i in issues)


def flagged(line_totals, grand_total) -> bool:
    return any(i.code.value == "TOTAL_MISMATCH" for i in validate(DocumentType.RECEIPT, receipt(line_totals, grand_total)))


# --- the 5% boundary -------------------------------------------------------------------------


def test_tolerance_is_five_percent():
    assert TOTAL_MISMATCH_TOLERANCE == 0.05


@pytest.mark.parametrize(
    "lines,total,expected",
    [
        ([100.00], 100.00, False),   # exact
        ([95.00], 100.00, False),    # exactly 5% under: not "more than 5%"
        ([105.00], 100.00, False),   # exactly 5% over
        ([94.99], 100.00, True),     # 5.01% under
        ([105.01], 100.00, True),    # 5.01% over
        ([95.01], 100.00, False),    # 4.99%
        ([92.00], 100.00, True),     # 8% (receipt_02's trap)
        ([97.00], 100.00, False),    # 3% (receipt_03's trap)
    ],
    ids=["exact", "5pct-under", "5pct-over", "5.01-under", "5.01-over", "4.99", "8pct", "3pct"],
)
def test_boundary(lines, total, expected):
    assert flagged(lines, total) is expected


def test_exactly_five_percent_is_not_flagged_despite_float_rounding():
    # 5.35 + 19.08 + 70.57 == 95.00, but the float sum is 94.99999999999999
    assert sum([5.35, 19.08, 70.57]) != 95.0
    assert not flagged([5.35, 19.08, 70.57], 100.00)


@pytest.mark.parametrize(
    "lines,total,expected",
    [
        ([2_850_000], 3_000_000, False),      # VND, exactly 5%
        ([2_849_000], 3_000_000, True),       # VND, 5.03%
        ([2_920_000], 3_010_000, False),      # receipt_03: ~3%
        ([22_950.00], 24_950.00, True),       # receipt_02: ~8%
        ([3_000, 3_252.00 - 3_000], 3_252.00, False),  # THB, split over lines
    ],
)
def test_boundary_at_real_currency_scales(lines, total, expected):
    assert flagged(lines, total) is expected


def test_mismatch_message_reports_two_decimals():
    issue = validate(DocumentType.RECEIPT, receipt([94.99], 100.00))[0]
    assert "94.99" in issue.message and "100.00" in issue.message and "5.01%" in issue.message


@pytest.mark.parametrize(
    "lines,total",
    [(None, 100.0), ([], 100.0), ([100.0], None), ([100.0], 0.0)],
    ids=["no-items", "empty-items", "no-total", "zero-total"],
)
def test_total_check_skipped_when_it_cannot_be_computed(lines, total):
    assert not flagged(lines, total)


# --- amounts must be positive --------------------------------------------------------------------


def test_every_amount_is_checked():
    r = ReceiptFields.model_validate({
        "hospital_name": w("H"), "patient_name": w("P"), "date": w("05/04/2024"), "payment_method": w("Cash"),
        "grand_total": w(-10.0),
        "items": w([{"description": w("x"), "quantity": w(-1), "unit_price": w(-10.0), "total": w(10.0)}]),
    })
    found = {(i.field, i.severity.value) for i in validate(DocumentType.RECEIPT, r) if i.code.value == "NON_POSITIVE_AMOUNT"}
    assert found == {("grand_total", "error"), ("items[0].quantity", "error"), ("items[0].unit_price", "error")}


def test_zero_is_a_warning_not_an_error():
    issues = validate(DocumentType.RECEIPT, receipt([0.0, 50.0], 50.0))
    assert ("items[0].total", "NON_POSITIVE_AMOUNT", "warning") in codes(issues)
    assert not any(i.severity.value == "error" for i in issues)


def test_null_amounts_are_not_validated():
    r = receipt([None, 50.0], None)
    assert validate(DocumentType.RECEIPT, r) == []


def test_prescription_quantity_numbers_checked_text_ignored():
    def rx(q):
        return PrescriptionFields.model_validate({
            "doctor_name": w("Dr A"), "patient_name": w("P"), "date": w("01/02/2024"),
            "medications": w([{"name": w("X"), "dosage": w("5 mg"), "frequency": w("daily"),
                               "duration": w("7 days"), "quantity": w(q)}]),
        })
    assert codes(validate(DocumentType.PRESCRIPTION, rx(-2.0))) == [("medications[0].quantity", "NON_POSITIVE_AMOUNT", "error")]
    assert validate(DocumentType.PRESCRIPTION, rx("14 tablets")) == []


def test_item_arithmetic_tolerates_rounding_but_not_misreads():
    def one(q, p, t):
        r = ReceiptFields.model_validate({
            "hospital_name": w("H"), "patient_name": w("P"), "date": w("05/04/2024"), "payment_method": w("Cash"),
            "grand_total": w(t),
            "items": w([{"description": w("x"), "quantity": w(q), "unit_price": w(p), "total": w(t)}]),
        })
        return [i.code.value for i in validate(DocumentType.RECEIPT, r)]
    assert one(3, 33.33, 100.00) == []                        # 99.99 vs 100.00: rounding on the unit price
    assert one(21, 12.00, 262.00) == ["ITEM_TOTAL_MISMATCH"]  # 252 printed as 262: a misread digit


# --- dates -------------------------------------------------------------------------------------------


def discharge(admission, discharge_date):
    return DischargeSummaryFields.model_validate({
        "hospital_name": w("H"), "patient_name": w("P"),
        "admission_date": w(admission), "discharge_date": w(discharge_date),
        "diagnosis": w({"primary": w("d"), "secondary": w(None)}), "procedures_performed": w(["p"]),
        "attending_physician": w("Dr A"), "discharge_instructions": w("rest"),
    })


@pytest.mark.parametrize(
    "admission,discharge_date,expected",
    [
        ("10/05/2024", "15/05/2024", []),
        ("10/05/2024", "10/05/2024", []),  # same-day discharge is fine
        ("15/05/2024", "10/05/2024", [("discharge_date", "DATE_ORDER", "error")]),
        ("26/02/2024", "30/02/2024", [("discharge_date", "INVALID_DATE", "error")]),
        ("31/04/2024", "02/05/2024", [("admission_date", "INVALID_DATE", "error")]),
        ("29/02/2023", "01/03/2023", [("admission_date", "INVALID_DATE", "error")]),  # not a leap year
        ("29/02/2024", "01/03/2024", []),  # leap year
        ("10 พ.ค. 2567", "15/05/2024", []),  # Thai Buddhist Era vs Gregorian: same calendar
        ("15/05/2567", "10/05/2024", [("discharge_date", "DATE_ORDER", "error")]),
        ("ngày 10 tháng 05 năm 2024", "15/05/2024", []),
        ("unknown", "15/05/2024", [("admission_date", "INVALID_DATE", "error")]),
        (None, "15/05/2024", []),  # a missing date is not validated
    ],
)
def test_discharge_dates(admission, discharge_date, expected):
    assert codes(validate(DocumentType.DISCHARGE_SUMMARY, discharge(admission, discharge_date))) == expected


def test_invalid_date_message_quotes_the_printed_value():
    issue = validate(DocumentType.DISCHARGE_SUMMARY, discharge("26/02/2024", "30/02/2024"))[0]
    assert "'30/02/2024'" in issue.message


def test_lab_report_dates_validated_and_no_amount_rules():
    lab = LabReportFields.model_validate({
        "lab_name": w("L"), "patient_name": w("P"), "date": w("32/01/2024"),
        "tests": w([{"test_name": w("CRP"), "result": w(-1.0), "unit": w("mg/L"),
                     "reference_range": w("< 5"), "flag": w("normal")}]),
    })
    assert codes(validate(DocumentType.LAB_REPORT, lab)) == [("date", "INVALID_DATE", "error")]


def test_unknown_documents_have_nothing_to_validate():
    from medextract.schemas import empty_fields

    assert validate(DocumentType.UNKNOWN, empty_fields(DocumentType.UNKNOWN)) == []
