"""Step 2: extract the fields for the classified document type.

The model is constrained to the type's schema (structured output). Its reply then
goes through two deterministic steps before pydantic validation:

1. ``normalize``: enforces the null rule mechanically. A null (or blank) value
   gets its confidence capped at NULL_CONFIDENCE_MAX, and confidences are
   clamped to [0, 1]. Every change is recorded, so nothing is silently rewritten.
2. ``coerce_values``: driven by the field types. A number field that came back
   as text is parsed as an amount in either locale style ("15,750.00",
   "1.500.000 đ"); text that is not an amount becomes null. In ``number | text``
   fields (lab result, prescription quantity), a purely numeric string such as
   "126" becomes the number 126, and anything else ("Negative") stays text.

For scans, ``extract(..., reading=2)`` makes a second, independent reading; the
confidence step compares the two.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
import types
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from medextract.classify import page_parts
from medextract.config import NULL_CONFIDENCE_MAX
from medextract.ingest import Document
from medextract.llm.client import CallStats, LLMClient
from medextract.llm.prompts import load_prompt
from medextract.parsing import parse_amount
from medextract.llm.schema import llm_schema
from medextract.schemas import FIELDS_BY_TYPE, DocumentType


class ExtractionError(RuntimeError):
    def __init__(self, message: str, raw: dict[str, Any] | None = None, stats: CallStats | None = None):
        super().__init__(message)
        self.raw = raw
        self.stats = stats


@dataclass
class Extraction:
    fields: BaseModel
    raw: dict[str, Any]
    stats: CallStats
    adjustments: list[str] = field(default_factory=list)


def extraction_schema(doc_type: DocumentType) -> dict[str, Any]:
    return llm_schema(FIELDS_BY_TYPE[doc_type])


def system_prompt(doc_type: DocumentType) -> str:
    return f"{load_prompt('extract_common')}\n\n## This document\n{load_prompt(doc_type.value)}"


SECOND_READING = (
    "This is an independent second reading used to double-check a first one. Read every value "
    "again directly from the image, character by character, as if seeing the document for the first time."
)


def extract(doc: Document, doc_type: DocumentType, client: LLMClient, reading: int = 1) -> Extraction:
    if doc_type is DocumentType.UNKNOWN:
        raise ValueError("nothing to extract from an unknown document")
    model = FIELDS_BY_TYPE[doc_type]
    instruction = f"Extract the {doc_type.value.replace('_', ' ')} fields from this document."
    if reading > 1:
        instruction = f"{SECOND_READING}\n{instruction}"
    response = client.generate_json(
        system=system_prompt(doc_type),
        parts=[*page_parts(doc), instruction],
        schema=extraction_schema(doc_type),
    )
    raw = response.data
    normalized, adjustments = normalize(raw)
    adjustments += coerce_values(normalized, model)
    try:
        fields = model.model_validate(normalized)
    except ValidationError as exc:
        raise ExtractionError(
            f"model output does not fit the {doc_type.value} schema: {exc}", raw, response.stats
        ) from exc
    return Extraction(fields=fields, raw=raw, stats=response.stats, adjustments=adjustments)


# --- deterministic post-processing ----------------------------------------------------


def _is_field(node: Any) -> bool:
    return isinstance(node, dict) and set(node) == {"value", "confidence"}


def normalize(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    data = copy.deepcopy(raw)
    notes: list[str] = []

    def visit(node: Any, path: str) -> None:
        if _is_field(node):
            value, conf = node["value"], node["confidence"]
            if isinstance(conf, (int, float)) and not 0.0 <= conf <= 1.0:
                node["confidence"] = conf = min(1.0, max(0.0, float(conf)))
                notes.append(f"{path}: confidence clamped to {conf}")
            if isinstance(value, str) and not value.strip() or value == []:
                node["value"] = value = None
                notes.append(f"{path}: blank value treated as null")
            if value is None and isinstance(conf, (int, float)) and conf > NULL_CONFIDENCE_MAX:
                node["confidence"] = NULL_CONFIDENCE_MAX
                notes.append(f"{path}: null with confidence {conf} capped to {NULL_CONFIDENCE_MAX}")
            visit(node["value"], path)
        elif isinstance(node, dict):
            for key, child in node.items():
                visit(child, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for i, child in enumerate(node):
                visit(child, f"{path}[{i}]")

    visit(data, "")
    return data, notes


_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?")


def _value_types(model: type[BaseModel], name: str) -> tuple[Any, ...]:
    """The allowed types of ``model.<name>.value``, without None."""
    extracted = model.model_fields[name].annotation
    annotation = extracted.model_fields["value"].annotation
    args = get_args(annotation) if get_origin(annotation) in (Union, types.UnionType) else (annotation,)
    return tuple(a for a in args if a is not type(None))


def coerce_values(data: dict[str, Any], model: type[BaseModel], path: str = "") -> list[str]:
    """Fix value types in place, before pydantic validation, guided by the schema."""
    notes: list[str] = []
    for name in model.model_fields:
        node = data.get(name) if isinstance(data, dict) else None
        if not _is_field(node):
            continue
        here = f"{path}.{name}" if path else name
        allowed = _value_types(model, name)
        value = node["value"]
        if isinstance(value, str) and float in allowed:
            if str in allowed:  # number | text
                if _NUMBER.fullmatch(value.strip()):
                    node["value"] = float(value)
                    notes.append(f"{here}: numeric text {value!r} stored as a number")
            else:  # number only: an amount printed in some locale style
                parsed = parse_amount(value)
                if parsed is not None:
                    node["value"] = parsed
                    notes.append(f"{here}: amount text {value!r} parsed as {parsed:,}")
                else:
                    node["value"] = None
                    node["confidence"] = min(node["confidence"], NULL_CONFIDENCE_MAX)
                    notes.append(f"{here}: {value!r} is not an amount; set to null")
        for t in allowed:
            if isinstance(t, type) and issubclass(t, BaseModel) and isinstance(value, dict):
                notes += coerce_values(value, t, here)
            elif get_origin(t) is list and isinstance(value, list):
                (item_type,) = get_args(t)
                if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                    for i, row in enumerate(value):
                        notes += coerce_values(row, item_type, f"{here}[{i}]")
    return notes
