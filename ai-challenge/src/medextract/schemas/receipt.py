"""Receipt / hospital invoice fields."""

from .common import Extracted, RawDate, StrictModel


class ReceiptItem(StrictModel):
    description: Extracted[str]
    quantity: Extracted[float]
    unit_price: Extracted[float]
    total: Extracted[float]


class ReceiptFields(StrictModel):
    hospital_name: Extracted[str]
    patient_name: Extracted[str]
    date: Extracted[RawDate]
    items: Extracted[list[ReceiptItem]]
    grand_total: Extracted[float]
    payment_method: Extracted[str]
