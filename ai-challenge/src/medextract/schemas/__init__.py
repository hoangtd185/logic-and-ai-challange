"""Output schemas: the contract every pipeline stage produces and consumes."""

from medextract.config import MISSING_CONFIDENCE, NULL_CONFIDENCE_MAX

from .common import (
    DocumentType,
    Extracted,
    IssueCode,
    RawDate,
    Severity,
    ValidationIssue,
)
from .discharge_summary import Diagnosis, DischargeSummaryFields
from .envelope import (
    FIELDS_BY_TYPE,
    RESULT_BY_TYPE,
    DischargeSummaryResult,
    ExtractionResult,
    LabReportResult,
    PrescriptionResult,
    ReceiptResult,
    UnknownResult,
    empty_fields,
    extraction_result_adapter,
    parse_result,
    unknown_result,
)
from .lab_report import LabFlag, LabReportFields, LabTest
from .prescription import Medication, PrescriptionFields
from .receipt import ReceiptFields, ReceiptItem
from .unknown import UnknownFields

__all__ = [
    "MISSING_CONFIDENCE",
    "NULL_CONFIDENCE_MAX",
    "DocumentType",
    "Extracted",
    "IssueCode",
    "RawDate",
    "Severity",
    "ValidationIssue",
    "Diagnosis",
    "DischargeSummaryFields",
    "FIELDS_BY_TYPE",
    "RESULT_BY_TYPE",
    "DischargeSummaryResult",
    "ExtractionResult",
    "LabReportResult",
    "PrescriptionResult",
    "ReceiptResult",
    "UnknownResult",
    "empty_fields",
    "extraction_result_adapter",
    "parse_result",
    "unknown_result",
    "LabFlag",
    "LabReportFields",
    "LabTest",
    "Medication",
    "PrescriptionFields",
    "ReceiptFields",
    "ReceiptItem",
    "UnknownFields",
]
