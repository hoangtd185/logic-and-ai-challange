"""Discharge summary fields."""

from .common import Extracted, RawDate, StrictModel


class Diagnosis(StrictModel):
    primary: Extracted[str]
    secondary: Extracted[list[str]]


class DischargeSummaryFields(StrictModel):
    hospital_name: Extracted[str]
    patient_name: Extracted[str]
    admission_date: Extracted[RawDate]
    discharge_date: Extracted[RawDate]
    diagnosis: Extracted[Diagnosis]
    procedures_performed: Extracted[list[str]]
    attending_physician: Extracted[str]
    discharge_instructions: Extracted[str]
