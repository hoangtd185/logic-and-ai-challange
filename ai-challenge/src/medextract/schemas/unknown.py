"""Fields for documents that fit none of the four types.

``UnknownFields`` has every field name from the four real types, so consumers
always see the same keys whatever the classification. Each one is typed
``Extracted[None]``, so all of them must be null, and the null rule caps their
confidence. Nothing is extracted from a document we can't classify, because any
value would be a guess.

The model is built from the other schemas, so adding a field to a document
type adds it here automatically.
"""

from pydantic import create_model

from .common import Extracted, StrictModel
from .discharge_summary import DischargeSummaryFields
from .lab_report import LabReportFields
from .prescription import PrescriptionFields
from .receipt import ReceiptFields

_KNOWN_FIELD_MODELS = (ReceiptFields, DischargeSummaryFields, LabReportFields, PrescriptionFields)


def _all_field_names() -> list[str]:
    names: list[str] = []
    for model in _KNOWN_FIELD_MODELS:
        names.extend(n for n in model.model_fields if n not in names)
    return names


UnknownFields = create_model(
    "UnknownFields",
    __base__=StrictModel,
    __doc__="Every known field name, all null.",
    **{name: (Extracted[None], ...) for name in _all_field_names()},
)
