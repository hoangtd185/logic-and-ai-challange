"""Lab report fields."""

from enum import Enum

from .common import Extracted, RawDate, StrictModel


class LabFlag(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"


class LabTest(StrictModel):
    test_name: Extracted[str]
    # Numeric ("5.4") or qualitative ("Negative", "<0.5"); a number is kept as a number.
    result: Extracted[float | str]
    unit: Extracted[str]
    # Kept as printed ("3.5-5.0", "<200", "M: 13-17"); parsing belongs to validation.
    reference_range: Extracted[str]
    flag: Extracted[LabFlag]


class LabReportFields(StrictModel):
    lab_name: Extracted[str]
    patient_name: Extracted[str]
    date: Extracted[RawDate]
    tests: Extracted[list[LabTest]]
