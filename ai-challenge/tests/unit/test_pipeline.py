"""Pipeline wiring with a scripted fake model. No network, no PDF rendering needed."""

from pathlib import Path

import pytest

from medextract.config import NULL_CONFIDENCE_MAX
import copy

from medextract.extract import SECOND_READING, coerce_values, normalize
from medextract.ingest import IngestError, load_bytes, load_document
from medextract.llm.client import CallStats, LLMResponse
from medextract.pipeline import process_document
from medextract.schemas import LabReportFields, ReceiptFields

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "documents"


def f(value, confidence=0.95):
    return {"value": value, "confidence": confidence}


class ScriptedClient:
    """Answers classification, extraction and (for scans) the second reading from a script.

    ``second`` defaults to the same reply as the first extraction (readings agree).
    """

    model = "fake-model"

    def __init__(self, classification, extraction=None, second=None):
        self.replies = [classification, extraction, second if second is not None else extraction]
        self.requests = []

    def generate_json(self, *, system, parts, schema):
        self.requests.append({"system": system, "parts": parts, "schema": schema})
        return LLMResponse(self.replies[len(self.requests) - 1], CallStats(model=self.model, seconds=1.0))


LAB_REPLY = {
    "lab_name": f("Mekong Clinical Laboratory"),
    "patient_name": f("Le Thi Hoa"),
    "date": f("03/08/2024"),
    "tests": f(
        [
            {
                "test_name": f("Hemoglobin"),
                "result": f("11.2"),
                "unit": f("g/dL"),
                "reference_range": f("12.0-15.5"),
                "flag": f("low"),
            },
            {
                "test_name": f("Urine protein"),
                "result": f("Negative"),
                "unit": f(None, 0.9),
                "reference_range": f("Negative"),
                "flag": f(None, 0.0),
            },
        ],
        0.9,
    ),
}


@pytest.fixture
def scan():
    return load_document(SAMPLES / "lab_02.png")


def test_classify_then_extract(scan):
    client = ScriptedClient({"evidence": "LABORATORY REPORT", "document_type": "lab_report", "confidence": 0.97}, LAB_REPLY)
    out = process_document(scan, client)
    result = out.result.model_dump(mode="json")

    assert list(result) == ["document_type", "confidence", "fields", "validation_errors"]
    assert result["document_type"] == "lab_report" and result["confidence"] == 0.97
    tests = result["fields"]["tests"]["value"]
    assert tests[0]["result"]["value"] == 11.2  # numeric text became a number
    assert tests[1]["result"]["value"] == "Negative"  # qualitative stays text
    assert tests[1]["unit"] == {"value": None, "confidence": NULL_CONFIDENCE_MAX}  # capped
    assert out.trace["extraction"]["raw"] == LAB_REPLY  # raw reply kept untouched
    assert len(out.trace["extraction"]["adjustments"]) == 2
    assert out.trace["classification"]["call"]["seconds"] == 1.0
    assert out.trace["extraction"]["call"]["model"] == "fake-model"


def test_requests_carry_image_and_schema(scan):
    client = ScriptedClient({"evidence": "x", "document_type": "lab_report", "confidence": 0.9}, LAB_REPLY)
    process_document(scan, client)
    classify_req, extract_req, second_req = client.requests
    assert SECOND_READING in second_req["parts"][-1] and SECOND_READING not in extract_req["parts"][-1]
    assert classify_req["parts"][0].mime_type == "image/jpeg"  # the 900 KB scan is sent as JPEG
    assert classify_req["parts"][0].data == scan.pages[0].data
    assert "flag" in extract_req["system"] and "Never work out a flag" in extract_req["system"]
    assert list(extract_req["schema"]["properties"]) == ["lab_name", "patient_name", "date", "tests"]


def test_unknown_skips_extraction(scan):
    client = ScriptedClient({"evidence": "ELECTRICITY BILL", "document_type": "unknown", "confidence": 0.93})
    out = process_document(scan, client)
    assert len(client.requests) == 1
    result = out.result.model_dump(mode="json")
    assert result["document_type"] == "unknown" and result["confidence"] == 0.93
    assert all(v["value"] is None for v in result["fields"].values())


def test_bad_extraction_returns_all_null_with_issue(scan):
    broken = {**LAB_REPLY, "lab_name": "not a field object"}
    client = ScriptedClient({"evidence": "x", "document_type": "lab_report", "confidence": 0.9}, broken)
    out = process_document(scan, client)
    result = out.result.model_dump(mode="json")
    assert result["validation_errors"][0]["code"] == "EXTRACTION_FAILED"
    assert all(v["value"] is None for v in result["fields"].values())
    assert out.trace["extraction"]["raw"] == broken


# --- normalize / coerce ------------------------------------------------------------


def test_normalize_records_every_change():
    raw = {"a": f("  ", 0.8), "b": f(None, 0.7), "c": f("x", 1.4), "d": f([], 0.9), "e": f("ok", 0.5)}
    data, notes = normalize(raw)
    assert data["a"] == f(None, NULL_CONFIDENCE_MAX)
    assert data["b"] == f(None, NULL_CONFIDENCE_MAX)
    assert data["c"] == f("x", 1.0)
    assert data["d"] == f(None, NULL_CONFIDENCE_MAX)
    assert data["e"] == f("ok", 0.5)
    assert len(notes) == 6
    assert raw["a"] == f("  ", 0.8)  # input not mutated


def test_coerce_numbers_only_where_number_allowed():
    data = normalize(LAB_REPLY)[0]
    notes = coerce_values(data, LabReportFields)
    assert notes == ["tests[0].result: numeric text '11.2' stored as a number"]
    fields = LabReportFields.model_validate(data)
    assert fields.date.value == "03/08/2024"  # a date-like string is never touched
    assert fields.tests.value[1].result.value == "Negative"


def test_amount_text_in_either_locale_style():
    data = {
        "hospital_name": f("H"), "patient_name": f("P"), "date": f("01/02/2024"), "payment_method": f("Cash"),
        "grand_total": f("1.500.000 đ"),
        "items": f([{"description": f("x"), "quantity": f(1), "unit_price": f("15,750.00"), "total": f("n/a")}]),
    }
    notes = coerce_values(data, ReceiptFields)
    fields = ReceiptFields.model_validate(data)
    assert fields.grand_total.value == 1_500_000
    assert fields.items.value[0].unit_price.value == 15_750
    assert fields.items.value[0].total.value is None  # not an amount: null, low confidence
    assert fields.items.value[0].total.confidence <= NULL_CONFIDENCE_MAX
    assert len(notes) == 3


# --- validation + confidence in the pipeline ------------------------------------------


def test_scan_readings_that_disagree_lower_confidence(scan):
    second = copy.deepcopy(LAB_REPLY)
    second["tests"]["value"][0]["result"] = f("11.7")  # a misread digit
    client = ScriptedClient({"evidence": "x", "document_type": "lab_report", "confidence": 0.9}, LAB_REPLY, second)
    out = process_document(scan, client)
    tests = out.result.fields.tests.value
    assert tests[0].result.value == 11.2  # first reading kept
    assert tests[0].result.confidence == 0.4  # lowered: readings disagree
    assert tests[0].test_name.confidence == 0.95  # agreed: untouched
    assert out.trace["confidence"]["method"] == "double_reading"
    lowered = out.trace["confidence"]["adjustments"]
    assert [a["field"] for a in lowered] == ["tests[0].result"]
    assert "11.2" in lowered[0]["reasons"][0] and "11.7" in lowered[0]["reasons"][0]


def test_second_reading_can_be_turned_off(scan):
    client = ScriptedClient({"evidence": "x", "document_type": "lab_report", "confidence": 0.9}, LAB_REPLY)
    out = process_document(scan, client, double_read_scans=False)
    assert len(client.requests) == 2 and out.trace["confidence"]["method"] == "model_only"


def test_validation_errors_do_not_fail_the_document(scan):
    bad_date = copy.deepcopy(LAB_REPLY)
    bad_date["date"] = f("31/02/2024")
    client = ScriptedClient({"evidence": "x", "document_type": "lab_report", "confidence": 0.9}, bad_date)
    out = process_document(scan, client)
    result = out.result.model_dump(mode="json")
    assert result["fields"]["lab_name"]["value"] == "Mekong Clinical Laboratory"  # rest still returned
    assert result["validation_errors"][0]["code"] == "INVALID_DATE"
    assert result["fields"]["date"] == {"value": "31/02/2024", "confidence": 0.3}


# --- ingest -------------------------------------------------------------------------


def test_large_scan_is_sent_as_jpeg(scan):
    assert scan.source_mime_type == "image/png" and len(scan.pages) == 1
    page = scan.pages[0]
    assert page.mime_type == "image/jpeg" and page.data.startswith(b"\xff\xd8")
    assert len(page.data) < len((SAMPLES / "lab_02.png").read_bytes()) / 4
    assert page.note.startswith("re-encoded PNG") and not scan.has_text_layer


@pytest.mark.parametrize(
    "data,mime",
    [(b"\xff\xd8\xff\xe0rest", "image/jpeg"), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp")],
)
def test_image_types_sniffed_from_bytes(data, mime):
    assert load_bytes(data, "x.bin").source_mime_type == mime


@pytest.mark.parametrize("data", [b"", b"hello world", b"PK\x03\x04zip"])
def test_unsupported_files_rejected(data):
    with pytest.raises(IngestError):
        load_bytes(data, "bad")


def test_pdf_rendered_to_pages():
    pytest.importorskip("pymupdf")
    doc = load_document(SAMPLES / "discharge_01.pdf")
    assert len(doc.pages) == 2
    assert all(p.mime_type == "image/png" and p.data.startswith(b"\x89PNG") for p in doc.pages)
    assert doc.has_text_layer and "Chao Phraya Medical Center" in doc.pages[0].text
