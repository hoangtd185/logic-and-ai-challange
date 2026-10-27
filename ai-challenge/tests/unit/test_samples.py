"""Phase 2: the committed sample set is complete, consistent and matches the brief.

These tests read the committed files only, so they need no browser and no LLM.
Rebuild the samples with `python samples/generator/build.py`.
"""

import datetime as dt
import json
from collections import Counter
from pathlib import Path

import pytest

from medextract.config import TOTAL_MISMATCH_TOLERANCE
from medextract.schemas import FIELDS_BY_TYPE, DocumentType, parse_result

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
MAIN_GT = sorted((SAMPLES / "ground_truth").glob("*.json"))
NEGATIVE_GT = sorted((SAMPLES / "negative" / "ground_truth").glob("*.json"))
ALL_GT = MAIN_GT + NEGATIVE_GT


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def gt(doc_id: str) -> dict:
    return load(SAMPLES / "ground_truth" / f"{doc_id}.json")


def test_brief_mix_of_ten_documents():
    counts = Counter(load(p)["document_type"] for p in MAIN_GT)
    assert counts == {"receipt": 3, "discharge_summary": 3, "lab_report": 2, "prescription": 2}


def test_negative_samples_are_unknown():
    assert NEGATIVE_GT, "expected at least one non-medical sample"
    for path in NEGATIVE_GT:
        g = load(path)
        assert g["document_type"] == "unknown"
        assert all(v is None for v in g["fields"].values())
        assert set(g["absent_fields"]) == set(FIELDS_BY_TYPE[DocumentType.UNKNOWN].model_fields)


def test_pdfs_and_scans_both_present():
    formats = Counter(load(p)["meta"]["format"] for p in MAIN_GT)
    assert formats["pdf"] >= 1 and formats["png"] >= 1


@pytest.mark.parametrize("path", ALL_GT, ids=lambda p: p.stem)
def test_source_file_exists(path):
    g = load(path)
    base = SAMPLES / "negative" if g["document_type"] == "unknown" else SAMPLES
    assert (SAMPLES / g["source_file"]).is_file() or (base / g["source_file"]).is_file()
    assert g["document_id"] == path.stem


@pytest.mark.parametrize("path", ALL_GT, ids=lambda p: p.stem)
def test_ground_truth_fits_output_schema(path):
    g = load(path)

    def wrap(value):
        if isinstance(value, dict) and "printed" in value:
            value = value["printed"]
        elif isinstance(value, dict):
            value = {k: wrap(v) for k, v in value.items()}
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            value = [{k: wrap(v) for k, v in item.items()} for item in value]
        return {"value": value, "confidence": 0.0 if value is None else 1.0}

    parse_result(
        {
            "document_type": g["document_type"],
            "confidence": 1.0,
            "fields": {k: wrap(v) for k, v in g["fields"].items()},
        }
    )


@pytest.mark.parametrize("path", ALL_GT, ids=lambda p: p.stem)
def test_dates_store_printed_and_iso(path):
    for name, value in load(path)["fields"].items():
        if (name == "date" or name.endswith("_date")) and value is not None:
            assert set(value) == {"printed", "iso"}
            day, month, year = map(int, value["printed"].split("/"))
            try:
                expected = dt.date(year, month, day).isoformat()
            except ValueError:
                expected = None
            assert value["iso"] == expected


# --- the planted hard cases ------------------------------------------------------


def items_gap(doc_id: str) -> float:
    f = gt(doc_id)["fields"]
    return abs(sum(i["total"] for i in f["items"]) - f["grand_total"]) / f["grand_total"]


def test_receipt_02_unpaid_and_over_threshold():
    g = gt("receipt_02")
    assert "payment_method" in g["absent_fields"]
    assert items_gap("receipt_02") > TOTAL_MISMATCH_TOLERANCE
    assert {"field": "grand_total", "code": "TOTAL_MISMATCH"} in g["expected_validation_errors"]


def test_receipt_03_scan_under_threshold_not_flagged():
    g = gt("receipt_03")
    assert g["meta"]["scanned"]
    assert 0 < items_gap("receipt_03") < TOTAL_MISMATCH_TOLERANCE
    assert g["expected_validation_errors"] == []


def test_discharge_02_blank_physician_and_invalid_date():
    g = gt("discharge_02")
    assert g["fields"]["attending_physician"] is None
    assert g["fields"]["discharge_date"] == {"printed": "30/02/2024", "iso": None}
    assert {"field": "discharge_date", "code": "INVALID_DATE"} in g["expected_validation_errors"]


def test_discharge_03_vietnamese_primary_only():
    g = gt("discharge_03")
    assert g["meta"]["language"] == "vi"
    assert g["fields"]["diagnosis"]["secondary"] is None
    assert g["absent_fields"] == ["diagnosis.secondary"]


def test_lab_02_unprinted_flag_stays_null():
    g = gt("lab_02")
    urine = g["fields"]["tests"][4]
    assert urine["unit"] is None and urine["flag"] is None
    assert urine["reference_range"] == "Negative"


def test_prescription_02_missing_duration_and_quantity():
    g = gt("prescription_02")
    assert g["absent_fields"] == ["medications[1].duration", "medications[1].quantity"]


def test_only_planted_documents_expect_validation_errors():
    with_errors = {p.stem for p in MAIN_GT if load(p)["expected_validation_errors"]}
    assert with_errors == {"receipt_02", "discharge_02"}
