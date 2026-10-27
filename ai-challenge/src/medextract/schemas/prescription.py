"""Prescription fields."""

from .common import Extracted, RawDate, StrictModel


class Medication(StrictModel):
    name: Extracted[str]
    dosage: Extracted[str]  # "500 mg"
    frequency: Extracted[str]  # "twice daily", "1-0-1"
    duration: Extracted[str]  # "7 days"
    # A count (30) or as written ("30 tablets", "1 bottle").
    quantity: Extracted[float | str]


class PrescriptionFields(StrictModel):
    doctor_name: Extracted[str]
    patient_name: Extracted[str]
    date: Extracted[RawDate]
    medications: Extracted[list[Medication]]
