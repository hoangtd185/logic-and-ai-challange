"""Amount parsing for Vietnamese (VND) and Thai (THB) documents, and the same strings
coming back from a mocked Gemini through the pipeline."""

from pathlib import Path

import pytest

from conftest import classified, field
from medextract.parsing import numbers_in_text, parse_amount
from medextract.pipeline import process_document
from medextract.ingest import load_document

SCAN = Path(__file__).resolve().parents[2] / "samples" / "documents" / "receipt_03.png"


@pytest.mark.parametrize(
    "text,value",
    [
        # VND: dot thousands, no decimals, đ / ₫ / VND / VNĐ / đồng before or after
        ("1.500.000 đ", 1_500_000), ("1.500.000đ", 1_500_000), ("₫ 1.500.000", 1_500_000),
        ("1.500.000 ₫", 1_500_000), ("8.750.000 VNĐ", 8_750_000), ("VND 3.010.000", 3_010_000),
        ("3.010.000 vnd", 3_010_000), ("250.000 đồng", 250_000), ("1 234 567 ₫", 1_234_567),
        # VND written English-style (comma thousands), as on receipt_03
        ("3,010,000 VND", 3_010_000), ("2,920,000", 2_920_000),
        # VND with decimal comma
        ("1.500.000,50 VND", 1_500_000.5), ("12.345.678,90", 12_345_678.9),
        # THB: comma thousands, dot decimals; ฿ / THB / baht / บาท
        ("15,750.00", 15_750.0), ("฿ 3,252.00", 3_252.0), ("฿3,252", 3_252.0), ("THB 1,234.50", 1_234.5),
        ("24,950.00 THB", 24_950.0), ("1,234.50 บาท", 1_234.5), ("500 baht", 500.0), ("12.00", 12.0),
        # signs
        ("-250.00", -250.0), ("(250.00)", -250.0), ("- 1.500.000 đ", -1_500_000),
    ],
)
def test_parse_amount(text, value):
    assert parse_amount(text) == value


@pytest.mark.parametrize(
    "text",
    ["", "   ", "đ", "THB", "n/a", "Negative", "1.5.0", "1,23,456", "12.", "1.234.56", "15 tablets", "abc 100"],
)
def test_non_amounts_are_rejected(text):
    assert parse_amount(text) is None


def test_three_digit_group_reads_as_thousands():
    """'1.500' on a VND receipt is 1,500; documented choice for a single 3-digit group."""
    assert parse_amount("1.500") == 1_500
    assert parse_amount("1,500") == 1_500
    assert parse_amount("1.50") == 1.5


def test_numbers_in_vietnamese_and_thai_text():
    vn = numbers_in_text("Tổng chi phí điều trị: 8.750.000 VNĐ, BHYT chi trả 6.125.000 đ")
    assert {8_750_000.0, 6_125_000.0} <= vn
    th = numbers_in_text("รวมทั้งสิ้น 3,252.00 บาท")
    assert 3_252.0 in th


# --- through the pipeline with a mocked Gemini ------------------------------------------------------


def receipt_reply(grand_total, unit_price="500,000", total="500,000"):
    return {
        "hospital_name": field("Hoa Sen General Hospital"), "patient_name": field("Tran Minh Khoa"),
        "date": field("18/06/2024"), "payment_method": field("Cash"),
        "grand_total": field(grand_total),
        "items": field([{"description": field("Consultation"), "quantity": field(1),
                         "unit_price": field(unit_price), "total": field(total)}]),
    }


def test_amount_strings_from_the_model_are_parsed(mock_gemini):
    model = mock_gemini(classified("receipt"), receipt_reply("500.000 ₫", "500.000đ", "VND 500,000"))
    out = process_document(load_document(SCAN), model)
    f = out.result.fields
    assert f.grand_total.value == 500_000 and f.items.value[0].unit_price.value == 500_000
    assert f.items.value[0].total.value == 500_000
    assert out.result.validation_errors == []
    assert sum("parsed as" in a for a in out.trace["extraction"]["adjustments"]) == 3


def test_an_unreadable_amount_becomes_null_not_a_guess(mock_gemini):
    model = mock_gemini(classified("receipt"), receipt_reply("illegible"))
    out = process_document(load_document(SCAN), model)
    assert out.result.fields.grand_total.value is None
    assert out.result.fields.grand_total.confidence <= 0.2
    assert out.result.validation_errors == []  # total check skipped, nothing invented


def test_vnd_mismatch_flagged_after_parsing(mock_gemini):
    model = mock_gemini(classified("receipt"), receipt_reply("3.010.000 đ", "2.500.000", "2.500.000"))
    out = process_document(load_document(SCAN), model)
    assert [i.code.value for i in out.result.validation_errors] == ["TOTAL_MISMATCH"]
