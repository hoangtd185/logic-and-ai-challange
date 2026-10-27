"""Output envelope, matching the brief exactly:

    {"document_type": ..., "confidence": ..., "fields": {...}, "validation_errors": []}

``ExtractionResult`` is a union discriminated on ``document_type``, so the
``fields`` block is always checked against the schema for that document type.
"""

from typing import Annotated, Any, Literal, Union, get_args

from pydantic import BaseModel, Field, TypeAdapter

from .common import DocumentType, Extracted, StrictModel, ValidationIssue
from .discharge_summary import DischargeSummaryFields
from .lab_report import LabReportFields
from .prescription import PrescriptionFields
from .receipt import ReceiptFields
from .unknown import UnknownFields


class _ResultBase(StrictModel):
    document_type: DocumentType
    confidence: float = Field(ge=0.0, le=1.0)  # classification confidence
    fields: BaseModel
    validation_errors: list[ValidationIssue] = Field(default_factory=list)


class ReceiptResult(_ResultBase):
    document_type: Literal["receipt"]
    fields: ReceiptFields


class DischargeSummaryResult(_ResultBase):
    document_type: Literal["discharge_summary"]
    fields: DischargeSummaryFields


class LabReportResult(_ResultBase):
    document_type: Literal["lab_report"]
    fields: LabReportFields


class PrescriptionResult(_ResultBase):
    document_type: Literal["prescription"]
    fields: PrescriptionFields


class UnknownResult(_ResultBase):
    """Fits none of the four types. ``confidence`` is how sure we are that it
    fits none of them. Every field is null."""

    document_type: Literal["unknown"]
    fields: UnknownFields


ExtractionResult = Annotated[
    Union[
        ReceiptResult,
        DischargeSummaryResult,
        LabReportResult,
        PrescriptionResult,
        UnknownResult,
    ],
    Field(discriminator="document_type"),
]
extraction_result_adapter: TypeAdapter[ExtractionResult] = TypeAdapter(ExtractionResult)

FIELDS_BY_TYPE: dict[DocumentType, type[StrictModel]] = {
    DocumentType.RECEIPT: ReceiptFields,
    DocumentType.DISCHARGE_SUMMARY: DischargeSummaryFields,
    DocumentType.LAB_REPORT: LabReportFields,
    DocumentType.PRESCRIPTION: PrescriptionFields,
    DocumentType.UNKNOWN: UnknownFields,
}

RESULT_BY_TYPE: dict[DocumentType, type[_ResultBase]] = {
    DocumentType.RECEIPT: ReceiptResult,
    DocumentType.DISCHARGE_SUMMARY: DischargeSummaryResult,
    DocumentType.LAB_REPORT: LabReportResult,
    DocumentType.PRESCRIPTION: PrescriptionResult,
    DocumentType.UNKNOWN: UnknownResult,
}


def parse_result(data: dict[str, Any] | str) -> ExtractionResult:
    """Parse a dict or JSON string into the correct typed result."""
    if isinstance(data, str):
        return extraction_result_adapter.validate_json(data)
    return extraction_result_adapter.validate_python(data)


def empty_fields(document_type: DocumentType | str) -> StrictModel:
    """Every field null with minimum confidence.

    The safe fallback when extraction fails: it reports nothing rather than
    guessing anything.
    """
    model = FIELDS_BY_TYPE[DocumentType(document_type)]
    values = {}
    for name, info in model.model_fields.items():
        annotation = info.annotation
        if not (isinstance(annotation, type) and issubclass(annotation, Extracted)):
            raise TypeError(f"{model.__name__}.{name} is not an Extracted[...] field")
        values[name] = annotation.missing()
    return model(**values)


def unknown_result(confidence: float) -> UnknownResult:
    """Result for a document that fits none of the four types."""
    return UnknownResult(
        document_type="unknown",
        confidence=confidence,
        fields=empty_fields(DocumentType.UNKNOWN),
    )


def _check_literals_match_enum() -> None:
    assert set(RESULT_BY_TYPE) == set(DocumentType) == set(FIELDS_BY_TYPE)
    for doc_type, result_model in RESULT_BY_TYPE.items():
        (literal,) = get_args(result_model.model_fields["document_type"].annotation)
        assert literal == doc_type.value, (result_model.__name__, literal)


_check_literals_match_enum()
