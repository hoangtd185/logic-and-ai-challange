"""Business-rule validation. Results go into ``validation_errors``; nothing here can
fail the document.

Rules (from the brief, plus one arithmetic check):
- every date field must be a real date (``INVALID_DATE``);
- a discharge date can't be before the admission date (``DATE_ORDER``);
- amounts must be positive (``NON_POSITIVE_AMOUNT``; zero is a warning);
- receipt line items must add up to the grand total within 5% (``TOTAL_MISMATCH``);
- each line's quantity x unit price should equal its total (``ITEM_TOTAL_MISMATCH``,
  warning). Not in the brief, but it catches a misread digit on the exact line.

Null fields are not validated: absence is handled by confidence, not by rules.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from medextract.config import ITEM_TOTAL_TOLERANCE, TOTAL_MISMATCH_TOLERANCE
from medextract.parsing import ParsedDate, parse_date
from medextract.schemas import DocumentType, IssueCode, Severity, ValidationIssue

log = logging.getLogger(__name__)

DATE_FIELDS = ("date", "admission_date", "discharge_date")


def validate(doc_type: DocumentType, fields: BaseModel) -> list[ValidationIssue]:
    """Never raises: a crashing rule becomes a VALIDATION_FAILED warning."""
    issues: list[ValidationIssue] = []
    rules = [_check_dates]
    if doc_type is DocumentType.DISCHARGE_SUMMARY:
        rules.append(_check_date_order)
    if doc_type is DocumentType.RECEIPT:
        rules += [_check_receipt_amounts, _check_item_arithmetic, _check_items_sum]
    if doc_type is DocumentType.PRESCRIPTION:
        rules.append(_check_prescription_quantities)
    for rule in rules:
        try:
            issues += rule(fields)
        except Exception as exc:  # a bug in one rule must not lose the document
            log.exception("validation rule %s crashed", rule.__name__)
            issues.append(_issue("fields", IssueCode.VALIDATION_FAILED, f"{rule.__name__} crashed: {exc}", Severity.WARNING))
    return issues


def parsed_dates(fields: BaseModel) -> dict[str, ParsedDate]:
    out = {}
    for name in DATE_FIELDS:
        field = getattr(fields, name, None)
        if field is not None and field.value is not None:
            out[name] = parse_date(field.value)
    return out


# --- rules -----------------------------------------------------------------------------


def _check_dates(fields):
    issues = []
    for name, parsed in parsed_dates(fields).items():
        if parsed.status != "valid":
            issues.append(_issue(name, IssueCode.INVALID_DATE, f"'{parsed.raw}' is not a valid date: {parsed.note}"))
    return issues


def _check_date_order(fields):
    dates = parsed_dates(fields)
    adm, dis = dates.get("admission_date"), dates.get("discharge_date")
    if adm and dis and adm.value and dis.value and dis.value < adm.value:
        return [_issue(
            "discharge_date", IssueCode.DATE_ORDER,
            f"discharge date {dis.iso} ('{dis.raw}') is before admission date {adm.iso} ('{adm.raw}')",
        )]
    return []


def _positive(path: str, value, what: str) -> list[ValidationIssue]:
    if value is None:
        return []
    if value < 0:
        return [_issue(path, IssueCode.NON_POSITIVE_AMOUNT, f"{what} is negative ({value:,.2f})")]
    if value == 0:
        return [_issue(path, IssueCode.NON_POSITIVE_AMOUNT, f"{what} is zero", Severity.WARNING)]
    return []


def _items(fields):
    return fields.items.value or []


def _check_receipt_amounts(fields):
    issues = _positive("grand_total", fields.grand_total.value, "grand total")
    for i, item in enumerate(_items(fields)):
        issues += _positive(f"items[{i}].quantity", item.quantity.value, f"line {i + 1} quantity")
        issues += _positive(f"items[{i}].unit_price", item.unit_price.value, f"line {i + 1} unit price")
        issues += _positive(f"items[{i}].total", item.total.value, f"line {i + 1} total")
    return issues


def _check_item_arithmetic(fields):
    issues = []
    for i, item in enumerate(_items(fields)):
        q, p, t = item.quantity.value, item.unit_price.value, item.total.value
        if None in (q, p, t) or t <= 0:
            continue
        expected = q * p
        if abs(expected - t) > max(ITEM_TOTAL_TOLERANCE * abs(t), 0.01):
            issues.append(_issue(
                f"items[{i}].total", IssueCode.ITEM_TOTAL_MISMATCH,
                f"line {i + 1}: {q:g} x {p:,.2f} = {expected:,.2f}, but the printed total is {t:,.2f}",
                Severity.WARNING,
            ))
    return issues


def _check_items_sum(fields):
    grand_total = fields.grand_total.value
    items = _items(fields)
    if grand_total is None or grand_total <= 0 or not items:
        return []
    totals = [item.total.value for item in items]
    if any(t is None for t in totals):
        return []  # can't compare; the null line amounts already carry low confidence
    # Compare in cents: a float sum of 5.35 + 19.08 + 70.57 is 94.99999999999999, which
    # would wrongly put an exactly-5% receipt over the line. Exactly 5% is not flagged.
    items_sum = round(sum(totals), 2)
    difference = round(abs(items_sum - grand_total), 2)
    allowed = round(TOTAL_MISMATCH_TOLERANCE * grand_total, 2)
    if difference > allowed:
        return [_issue(
            "grand_total", IssueCode.TOTAL_MISMATCH,
            f"line items sum to {items_sum:,.2f} but the grand total is {grand_total:,.2f} "
            f"({difference / grand_total:.2%} apart, more than {TOTAL_MISMATCH_TOLERANCE:.0%})",
        )]
    return []


def _check_prescription_quantities(fields):
    issues = []
    for i, med in enumerate(fields.medications.value or []):
        q = med.quantity.value
        if isinstance(q, (int, float)):
            issues += _positive(f"medications[{i}].quantity", q, f"medication {i + 1} quantity")
    return issues


def _issue(field, code, message, severity=Severity.ERROR) -> ValidationIssue:
    return ValidationIssue(field=field, code=code, message=message, severity=severity)
