"""Shared building blocks for every document schema.

Core rule: every extracted field is an ``Extracted[T]`` = ``{value, confidence}``.
If a field is not visible in the document, ``value`` is ``None`` and
``confidence`` is at most ``NULL_CONFIDENCE_MAX``. A null with high confidence
is rejected, so "not found" can never be dressed up as a confident answer.

Schema vs. validation: the schema checks *shape* (keys, types, the null rule).
Business rules from the brief (valid dates, positive amounts, totals that add
up) are checked by the validation layer and reported in ``validation_errors``,
so one bad value never discards the rest of the document.
"""

from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from medextract.config import MISSING_CONFIDENCE, NULL_CONFIDENCE_MAX

T = TypeVar("T")

# Dates are stored as the string read from the document ("15/03/2024",
# "2024-02-30", "15 มี.ค. 2567"), not parsed by the schema. Checking that it is
# a real date is a validation rule, so an invalid date shows up in
# validation_errors while the rest of the document is still returned.
RawDate = str


class StrictModel(BaseModel):
    """Base for every schema model.

    - ``extra="forbid"``: unknown keys (e.g. a field the LLM invented) fail loudly.
    - ``validate_assignment=True``: invariants still hold when later pipeline
      stages (confidence adjustment) modify a model in place.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class DocumentType(str, Enum):
    RECEIPT = "receipt"
    DISCHARGE_SUMMARY = "discharge_summary"
    LAB_REPORT = "lab_report"
    PRESCRIPTION = "prescription"
    # Fits none of the four types. Nothing is extracted: every field is null.
    UNKNOWN = "unknown"


class Extracted(StrictModel, Generic[T]):
    """One extracted field: ``{"value": ..., "confidence": 0.0-1.0}``."""

    value: T | None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("value", mode="before")
    @classmethod
    def _blank_is_null(cls, v: Any) -> Any:
        """Blank strings and empty lists mean "nothing found", so they become null.

        This keeps the null/low-confidence rule from being bypassed with ``""``
        or ``[]``.
        """
        if isinstance(v, str):
            v = v.strip()
            return v or None
        if isinstance(v, list) and not v:
            return None
        return v

    @model_validator(mode="after")
    def _null_means_low_confidence(self) -> "Extracted[T]":
        if self.value is None and self.confidence > NULL_CONFIDENCE_MAX:
            raise ValueError(
                f"null value must have confidence <= {NULL_CONFIDENCE_MAX}, "
                f"got {self.confidence}: a field that is not visible must not be "
                "reported with high confidence"
            )
        return self

    @classmethod
    def missing(cls, confidence: float = MISSING_CONFIDENCE) -> "Extracted[T]":
        """A field that is not visible in the document."""
        return cls(value=None, confidence=confidence)


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class IssueCode(str, Enum):
    """Machine-readable validation codes. Phase 3 (validation) may add more."""

    INVALID_DATE = "INVALID_DATE"
    NON_POSITIVE_AMOUNT = "NON_POSITIVE_AMOUNT"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    DATE_ORDER = "DATE_ORDER"
    # A line's quantity x unit price differs from its printed total.
    ITEM_TOTAL_MISMATCH = "ITEM_TOTAL_MISMATCH"
    # A validation rule crashed; the document is still returned.
    VALIDATION_FAILED = "VALIDATION_FAILED"
    # The model's reply could not be turned into this document type's fields;
    # every field is returned null so nothing unverified reaches the claim.
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class ValidationIssue(StrictModel):
    """One entry in ``validation_errors``.

    ``field`` is a path into ``fields``, e.g. ``"grand_total"`` or ``"items[2].total"``.
    """

    field: str
    code: IssueCode
    message: str
    severity: Severity = Severity.ERROR
