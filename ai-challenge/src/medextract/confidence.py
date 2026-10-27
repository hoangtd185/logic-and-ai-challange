"""Calibrate field confidence instead of trusting the model's own score.

final confidence = min(
    Gemini's score,
    validation cap      if the field fails a validation rule,
    text-layer cap      PDFs with a text layer: the value must appear in that text,
    double-reading cap  scans: a second, independent extraction must agree,
)

Each check can only lower a confidence, never raise it, and every lowering is
recorded with its reason. Null fields keep their (already low) confidence.
"""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterator

from pydantic import BaseModel

from medextract.config import (
    CONF_CAP_NOT_IN_TEXT,
    CONF_CAP_PARTLY_IN_TEXT,
    CONF_CAP_READINGS_DISAGREE,
    CONF_CAP_VALIDATION_ERROR,
    CONF_CAP_VALIDATION_WARNING,
    READINGS_TEXT_SIMILARITY,
    TEXT_FOUND_COVERAGE,
    TEXT_PARTLY_COVERAGE,
)
from medextract.parsing import normalize_text, numbers_in_text, tokens
from medextract.schemas import Extracted, IssueCode, Severity, ValidationIssue


@dataclass
class FieldAdjustment:
    field: str
    model_confidence: float
    final_confidence: float
    reasons: list[str]


@dataclass
class CalibrationReport:
    method: str  # "text_layer", "double_reading" or "model_only"
    adjustments: list[FieldAdjustment] = field(default_factory=list)
    checked_fields: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- walking the typed result ------------------------------------------------------------


def walk(model: BaseModel, path: str = "") -> Iterator[tuple[str, Extracted, str]]:
    """Yield (path, field, kind) for every Extracted field. kind: 'leaf', 'object' or 'rows'."""
    for name in type(model).model_fields:
        f = getattr(model, name)
        if not isinstance(f, Extracted):
            continue
        here = f"{path}.{name}" if path else name
        value = f.value
        if isinstance(value, BaseModel):
            yield here, f, "object"
            yield from walk(value, here)
        elif isinstance(value, list) and value and isinstance(value[0], BaseModel):
            yield here, f, "rows"
            for i, row in enumerate(value):
                yield from walk(row, f"{here}[{i}]")
        else:
            yield here, f, "leaf"


# --- the calibration -----------------------------------------------------------------------


def calibrate(
    fields: BaseModel,
    issues: list[ValidationIssue],
    page_texts: list[str] | None = None,
    second_reading: BaseModel | None = None,
) -> CalibrationReport:
    """Lower confidences in place on ``fields``; return what changed and why."""
    text = "\n".join(page_texts or [])
    has_text = bool(text.strip())
    method = "text_layer" if has_text else "double_reading" if second_reading is not None else "model_only"
    report = CalibrationReport(method=method)
    if method == "model_only":
        report.notes.append("no text layer and no second reading: confidences are the model's own")

    caps: dict[str, list[tuple[float, str]]] = {}

    def cap(path: str, value: float, reason: str) -> None:
        caps.setdefault(path, []).append((value, reason))

    for issue in issues:
        limit = CONF_CAP_VALIDATION_ERROR if issue.severity is Severity.ERROR else CONF_CAP_VALIDATION_WARNING
        for path in _affected_paths(issue):
            cap(path, limit, f"validation {issue.code.value}: {issue.message}")

    entries = list(walk(fields))
    if has_text:
        checker = _TextChecker(text)
        for path, f, kind in entries:
            if kind == "leaf" and f.value is not None:
                report.checked_fields += 1
                found = checker.check(f.value)
                if found is not None and found < TEXT_FOUND_COVERAGE:
                    if found >= TEXT_PARTLY_COVERAGE:
                        cap(path, CONF_CAP_PARTLY_IN_TEXT, f"only {found:.0%} of the value's words are in the PDF text layer")
                    else:
                        cap(path, CONF_CAP_NOT_IN_TEXT, "value not found in the PDF text layer (possibly made up)")
    elif second_reading is not None:
        second = {path: f for path, f, _ in walk(second_reading)}
        for path, f, kind in entries:
            report.checked_fields += 1
            other = second.get(path)
            if kind == "rows":
                n1, n2 = len(f.value), len((other.value if other else None) or [])
                if n1 != n2:
                    cap(path, CONF_CAP_READINGS_DISAGREE, f"two readings found {n1} vs {n2} rows")
                continue
            if kind == "object":
                continue
            other_value = other.value if other is not None else None
            if not readings_agree(f.value, other_value):
                cap(path, CONF_CAP_READINGS_DISAGREE,
                    f"two readings disagree: {_short(f.value)} vs {_short(other_value)}")

    by_path = {path: f for path, f, _ in entries}
    for path, limits in caps.items():
        f = by_path.get(path)
        if f is None or f.value is None:
            continue
        lowest = min(v for v, _ in limits)
        if lowest < f.confidence:
            report.adjustments.append(
                FieldAdjustment(path, f.confidence, lowest, [reason for _, reason in limits])
            )
            f.confidence = lowest
    report.adjustments.sort(key=lambda a: a.field)
    return report


def _affected_paths(issue: ValidationIssue) -> list[str]:
    """A rule names one field, but a mismatch can be caused by either side."""
    path = issue.field
    if issue.code is IssueCode.DATE_ORDER:
        return ["admission_date", "discharge_date"]
    if issue.code is IssueCode.TOTAL_MISMATCH:
        return ["grand_total", "items"]
    if issue.code is IssueCode.ITEM_TOTAL_MISMATCH and path.endswith(".total"):
        row = path.rsplit(".", 1)[0]
        return [f"{row}.quantity", f"{row}.unit_price", f"{row}.total"]
    return [path]


# --- text-layer check -------------------------------------------------------------------------


class _TextChecker:
    def __init__(self, text: str):
        self.norm = normalize_text(text)
        self.squashed = self.norm.replace(" ", "")
        self.words = set(tokens(text))
        self.numbers = numbers_in_text(text)

    def check(self, value: Any) -> float | None:
        """Share of the value found in the text (1.0 = fully present), or None if not checkable."""
        if isinstance(value, Enum):
            return None  # e.g. lab flag "high" is printed as "H": not comparable as text
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return 1.0 if any(abs(n - value) <= max(0.005, abs(value) * 1e-9) for n in self.numbers) else 0.0
        if isinstance(value, list):
            scores = [s for s in (self.check(v) for v in value) if s is not None]
            return min(scores) if scores else None
        return self._text(str(value))

    def _text(self, value: str) -> float:
        norm = normalize_text(value)
        if not norm or norm in self.norm or norm.replace(" ", "") in self.squashed:
            return 1.0
        words = tokens(value)
        if not words:
            return 1.0
        coverage = sum(w in self.words for w in words) / len(words)
        if len(words) <= 3 and coverage < 1.0:
            return 0.0  # short values must match fully: "Dr Lim" is not confirmed by "Dr"
        return coverage


# --- double-reading check -------------------------------------------------------------------


def readings_agree(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, Enum) or isinstance(b, Enum):
        return getattr(a, "value", a) == getattr(b, "value", b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= max(0.01, 0.005 * max(abs(a), abs(b)))
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(_norm_any, a)) == sorted(map(_norm_any, b))
    sa, sb = normalize_text(str(a)), normalize_text(str(b))
    if sa == sb:
        return True
    if min(len(sa), len(sb)) < 40:  # names, dates, codes: must be identical
        return False
    return difflib.SequenceMatcher(None, sa, sb).ratio() >= READINGS_TEXT_SIMILARITY


def _norm_any(v: Any) -> str:
    return normalize_text(str(getattr(v, "value", v)))


def _short(v: Any) -> str:
    text = repr(getattr(v, "value", v))
    return text if len(text) <= 60 else text[:57] + "...'"
