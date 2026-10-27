"""Phase 1: schema contract tests. Pure Python, no LLM, no network."""

import copy
import json

import pytest
from pydantic import ValidationError

from medextract.schemas import (
    FIELDS_BY_TYPE,
    NULL_CONFIDENCE_MAX,
    DocumentType,
    Extracted,
    LabFlag,
    ReceiptResult,
    empty_fields,
    parse_result,
    unknown_result,
)


def ex(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


@pytest.fixture
def receipt_doc():
    """Brief's example, completed with every field the receipt schema requires."""
    return {
        "document_type": "receipt",
        "confidence": 0.95,
        "fields": {
            "hospital_name": ex("Bangkok Hospital", 0.98),
            "patient_name": ex("John Doe", 0.92),
            "date": ex("2024-03-15", 0.9),
            "items": ex(
                [
                    {
                        "description": ex("Consultation"),
                        "quantity": ex(1),
                        "unit_price": ex(750.0),
                        "total": ex(750.0),
                    },
                    {
                        "description": ex("Room charge"),
                        "quantity": ex(3),
                        "unit_price": ex(5000),
                        "total": ex(15000),
                    },
                ],
                0.85,
            ),
            "grand_total": ex(15750.00, 0.97),
            "payment_method": ex(None, 0.05),
        },
        "validation_errors": [],
    }


@pytest.fixture
def discharge_doc():
    return {
        "document_type": "discharge_summary",
        "confidence": 0.9,
        "fields": {
            "hospital_name": ex("Cho Ray Hospital"),
            "patient_name": ex("Nguyen Van A"),
            "admission_date": ex("2024-05-01"),
            "discharge_date": ex("2024-05-06"),
            "diagnosis": ex(
                {"primary": ex("Community-acquired pneumonia"), "secondary": ex(["Type 2 diabetes"])}
            ),
            "procedures_performed": ex(["Chest X-ray"]),
            "attending_physician": ex("Dr. Tran Thi B"),
            "discharge_instructions": ex("Complete antibiotics. Follow up in 7 days."),
        },
        "validation_errors": [],
    }


@pytest.fixture
def lab_doc():
    return {
        "document_type": "lab_report",
        "confidence": 0.93,
        "fields": {
            "lab_name": ex("N Health Lab"),
            "patient_name": ex("Somchai P."),
            "date": ex("2024-02-10"),
            "tests": ex(
                [
                    {
                        "test_name": ex("Glucose"),
                        "result": ex(126),
                        "unit": ex("mg/dL"),
                        "reference_range": ex("70-100"),
                        "flag": ex("high"),
                    },
                    {
                        "test_name": ex("HBsAg"),
                        "result": ex("Negative"),
                        "unit": ex(None, 0.0),
                        "reference_range": ex("Negative"),
                        "flag": ex("normal"),
                    },
                ]
            ),
        },
        "validation_errors": [],
    }


@pytest.fixture
def prescription_doc():
    return {
        "document_type": "prescription",
        "confidence": 0.88,
        "fields": {
            "doctor_name": ex("Dr. Le Van C"),
            "patient_name": ex("Pham Thi D"),
            "date": ex("2024-06-20"),
            "medications": ex(
                [
                    {
                        "name": ex("Amoxicillin"),
                        "dosage": ex("500 mg"),
                        "frequency": ex("3 times daily"),
                        "duration": ex("7 days"),
                        "quantity": ex("21 capsules"),
                    }
                ]
            ),
        },
        "validation_errors": [],
    }


@pytest.fixture
def all_docs(receipt_doc, discharge_doc, lab_doc, prescription_doc):
    return [receipt_doc, discharge_doc, lab_doc, prescription_doc]


# --- envelope -------------------------------------------------------------


def test_each_document_type_parses_to_its_own_result(all_docs):
    for doc in all_docs:
        result = parse_result(doc)
        assert result.document_type == doc["document_type"]
        assert isinstance(result.fields, FIELDS_BY_TYPE[DocumentType(doc["document_type"])])


def test_envelope_keys_and_order_match_brief(receipt_doc):
    dumped = parse_result(receipt_doc).model_dump(mode="json")
    assert list(dumped) == ["document_type", "confidence", "fields", "validation_errors"]
    assert dumped["fields"]["hospital_name"] == {"value": "Bangkok Hospital", "confidence": 0.98}
    assert dumped["fields"]["grand_total"] == {"value": 15750.0, "confidence": 0.97}


def test_json_round_trip_is_lossless(all_docs):
    for doc in all_docs:
        first = parse_result(doc)
        again = parse_result(first.model_dump_json())
        assert again == first


def test_dates_kept_as_raw_strings(receipt_doc):
    result = parse_result(receipt_doc)
    assert result.fields.date.value == "2024-03-15"
    assert result.model_dump(mode="json")["fields"]["date"]["value"] == "2024-03-15"


def test_fields_must_match_the_declared_document_type(receipt_doc, lab_doc):
    mismatched = copy.deepcopy(lab_doc)
    mismatched["fields"] = receipt_doc["fields"]
    with pytest.raises(ValidationError):
        parse_result(mismatched)


def test_unknown_document_type_rejected(receipt_doc):
    receipt_doc["document_type"] = "insurance_card"
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


def test_validation_errors_defaults_to_empty(receipt_doc):
    del receipt_doc["validation_errors"]
    assert parse_result(receipt_doc).validation_errors == []


def test_validation_issue_shape(receipt_doc):
    receipt_doc["validation_errors"] = [
        {"field": "grand_total", "code": "TOTAL_MISMATCH", "message": "items sum 7% below grand_total"}
    ]
    issue = parse_result(receipt_doc).model_dump(mode="json")["validation_errors"][0]
    assert issue == {
        "field": "grand_total",
        "code": "TOTAL_MISMATCH",
        "message": "items sum 7% below grand_total",
        "severity": "error",
    }


@pytest.mark.parametrize("bad", [-0.01, 1.01])
def test_document_confidence_bounded(receipt_doc, bad):
    receipt_doc["confidence"] = bad
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


# --- no omission, no invention -------------------------------------------


def test_every_field_key_is_required(receipt_doc):
    """A field that is not visible must be present as null, not left out."""
    del receipt_doc["fields"]["payment_method"]
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


def test_extra_field_rejected(receipt_doc):
    receipt_doc["fields"]["insurance_provider"] = ex("AIA")
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


def test_extra_envelope_key_rejected(receipt_doc):
    receipt_doc["notes"] = "hello"
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


def test_bare_value_without_confidence_rejected(receipt_doc):
    receipt_doc["fields"]["hospital_name"] = "Bangkok Hospital"
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


def test_nested_item_fields_are_also_value_confidence(receipt_doc):
    receipt_doc["fields"]["items"]["value"][0]["quantity"] = 1
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


# --- the null rule ----------------------------------------------------------


def test_threshold_comes_from_config():
    from medextract import config
    from medextract.schemas import common

    assert NULL_CONFIDENCE_MAX is config.NULL_CONFIDENCE_MAX
    assert common.NULL_CONFIDENCE_MAX is config.NULL_CONFIDENCE_MAX
    assert 0.0 <= config.MISSING_CONFIDENCE <= config.NULL_CONFIDENCE_MAX < 0.5


def test_null_with_low_confidence_allowed():
    field = Extracted[str](value=None, confidence=NULL_CONFIDENCE_MAX)
    assert field.value is None


def test_null_with_high_confidence_rejected():
    with pytest.raises(ValidationError, match="null value must have confidence"):
        Extracted[str](value=None, confidence=0.9)


def test_null_rule_applies_inside_nested_items(receipt_doc):
    receipt_doc["fields"]["items"]["value"][1]["quantity"] = ex(None, 0.8)
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


@pytest.mark.parametrize("blank", ["", "   ", []])
def test_blank_values_become_null(blank):
    assert Extracted[str | list[str]](value=blank, confidence=0.1).value is None
    with pytest.raises(ValidationError):
        Extracted[str | list[str]](value=blank, confidence=0.9)


def test_string_values_are_stripped():
    assert Extracted[str](value="  Bangkok Hospital ", confidence=0.9).value == "Bangkok Hospital"


def test_null_rule_holds_on_assignment():
    field = Extracted[str](value="John Doe", confidence=0.9)
    with pytest.raises(ValidationError):
        field.value = None


def test_missing_helper():
    field = Extracted[float].missing()
    assert field.value is None and field.confidence == 0.0


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_field_confidence_bounded(bad):
    with pytest.raises(ValidationError):
        Extracted[str](value="x", confidence=bad)


# --- types --------------------------------------------------------------------


@pytest.mark.parametrize("raw_date", ["2024-02-30", "15/03/2024", "15 มี.ค. 2567", "yesterday"])
def test_invalid_or_non_iso_dates_do_not_fail_parsing(receipt_doc, raw_date):
    """'Dates must be valid' is a validation rule: an invalid date is kept
    verbatim (to be flagged in validation_errors) and the rest of the
    document is still returned."""
    receipt_doc["fields"]["date"] = ex(raw_date)
    result = parse_result(receipt_doc)
    assert result.fields.date.value == raw_date
    assert result.fields.grand_total.value == 15750.0


def test_invalid_dates_in_discharge_summary_also_parse(discharge_doc):
    discharge_doc["fields"]["admission_date"] = ex("2024-13-45")
    discharge_doc["fields"]["discharge_date"] = ex("31/02/2024")
    result = parse_result(discharge_doc)
    assert result.fields.admission_date.value == "2024-13-45"
    assert result.fields.discharge_date.value == "31/02/2024"


def test_date_must_still_be_a_string(receipt_doc):
    receipt_doc["fields"]["date"] = ex(20240315)
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


def test_numeric_strings_coerced_to_numbers(receipt_doc):
    receipt_doc["fields"]["grand_total"] = ex("15750.00")
    assert parse_result(receipt_doc).fields.grand_total.value == 15750.0


def test_non_numeric_amount_rejected(receipt_doc):
    receipt_doc["fields"]["grand_total"] = ex("15,750 THB")
    with pytest.raises(ValidationError):
        parse_result(receipt_doc)


def test_negative_amounts_allowed_by_schema(receipt_doc):
    """Positivity is a validation rule (reported in validation_errors), not a parse failure."""
    receipt_doc["fields"]["grand_total"] = ex(-10.0)
    assert parse_result(receipt_doc).fields.grand_total.value == -10.0


def test_lab_result_keeps_numbers_and_text(lab_doc):
    tests = parse_result(lab_doc).fields.tests.value
    assert tests[0].result.value == 126 and isinstance(tests[0].result.value, float)
    assert tests[1].result.value == "Negative"
    assert tests[0].flag.value is LabFlag.HIGH


@pytest.mark.parametrize("bad_flag", ["H", "critical", "High"])
def test_lab_flag_restricted_to_brief_values(lab_doc, bad_flag):
    lab_doc["fields"]["tests"]["value"][0]["flag"] = ex(bad_flag)
    with pytest.raises(ValidationError):
        parse_result(lab_doc)


def test_diagnosis_has_primary_and_secondary(discharge_doc):
    diagnosis = parse_result(discharge_doc).fields.diagnosis.value
    assert diagnosis.primary.value == "Community-acquired pneumonia"
    assert diagnosis.secondary.value == ["Type 2 diabetes"]


def test_prescription_quantity_number_or_text(prescription_doc):
    med = prescription_doc["fields"]["medications"]["value"][0]
    med["quantity"] = ex(21)
    assert parse_result(prescription_doc).fields.medications.value[0].quantity.value == 21


# --- empty_fields fallback ------------------------------------------------------


@pytest.mark.parametrize("doc_type", list(DocumentType))
def test_empty_fields_is_all_null_and_valid(doc_type):
    fields = empty_fields(doc_type)
    dumped = fields.model_dump(mode="json")
    assert set(dumped) == set(FIELDS_BY_TYPE[doc_type].model_fields)
    assert all(f == {"value": None, "confidence": 0.0} for f in dumped.values())
    result = parse_result(
        {"document_type": doc_type.value, "confidence": 0.5, "fields": dumped}
    )
    assert result.fields == fields


def test_field_names_match_brief():
    expected = {
        DocumentType.RECEIPT: {
            "hospital_name", "patient_name", "date", "items", "grand_total", "payment_method",
        },
        DocumentType.DISCHARGE_SUMMARY: {
            "hospital_name", "patient_name", "admission_date", "discharge_date", "diagnosis",
            "procedures_performed", "attending_physician", "discharge_instructions",
        },
        DocumentType.LAB_REPORT: {"lab_name", "patient_name", "date", "tests"},
        DocumentType.PRESCRIPTION: {"doctor_name", "patient_name", "date", "medications"},
    }
    expected[DocumentType.UNKNOWN] = set().union(*expected.values())
    for doc_type, names in expected.items():
        assert set(FIELDS_BY_TYPE[doc_type].model_fields) == names


# --- unknown document type -------------------------------------------------------


def test_unknown_result_has_every_field_null():
    result = unknown_result(confidence=0.8)
    dumped = result.model_dump(mode="json")
    assert dumped["document_type"] == "unknown"
    assert dumped["confidence"] == 0.8
    assert dumped["fields"]
    assert all(f["value"] is None for f in dumped["fields"].values())
    assert parse_result(dumped) == result


def test_unknown_rejects_any_extracted_value():
    doc = unknown_result(confidence=0.8).model_dump(mode="json")
    doc["fields"]["patient_name"] = ex("John Doe", 0.1)
    with pytest.raises(ValidationError):
        parse_result(doc)


def test_unknown_rejects_high_confidence_nulls():
    doc = unknown_result(confidence=0.8).model_dump(mode="json")
    doc["fields"]["patient_name"] = ex(None, 0.9)
    with pytest.raises(ValidationError):
        parse_result(doc)


def test_unknown_requires_every_key():
    doc = unknown_result(confidence=0.8).model_dump(mode="json")
    del doc["fields"]["grand_total"]
    with pytest.raises(ValidationError):
        parse_result(doc)


def test_receipt_result_json_schema_generates():
    """The JSON Schema is what later phases document and adapt for the LLM."""
    schema = ReceiptResult.model_json_schema()
    json.dumps(schema)
    assert "fields" in schema["properties"]
