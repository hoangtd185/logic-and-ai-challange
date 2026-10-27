"""Locale-aware parsing of amounts and dates, plus text normalisation.

Used by extraction (an amount the model returned as text), validation (is this a
real date?) and confidence (does this value appear in the PDF's text layer?).

Amounts: "15,750.00", "1.500.000 đ", "1.500.000,50 VND", "฿ 3,252", "1,234.50 บาท", "(250.00)".
Dates are day-first, the convention in the region these claims come from:
"05/04/2024" is 5 April. Thai Buddhist Era years (2567) are converted to the
Gregorian year (2024).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

# --- text -------------------------------------------------------------------------------

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")


def normalize_text(text: str) -> str:
    """Unicode-normalised, case-folded, single-spaced: for comparing, never for output."""
    text = unicodedata.normalize("NFC", unicodedata.normalize("NFKC", text))
    text = text.translate(_DASHES).replace(" ", " ").replace("​", "")
    return " ".join(text.casefold().split())


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", normalize_text(text))


# --- amounts -----------------------------------------------------------------------------

_CURRENCY = re.compile(
    r"(?i)\b(?:vnđ|vnd|thb|usd|sgd|myr|php|idr|baht|dong|đồng|bath)\b|[đ₫฿$€£¥]|bht|บาท",
)


def parse_amount(text: str) -> float | None:
    """'15,750.00' → 15750.0, '1.500.000 đ' → 1500000.0, '1.500.000,50' → 1500000.5.

    Rules for separators:
    - both '.' and ',' present: the last one is the decimal separator;
    - one kind, repeated ('1.500.000'): thousands separator;
    - one kind, once, followed by exactly 3 digits ('15,750', '1.500'): thousands;
    - one kind, once, followed by 1-2 digits ('12.50', '12,5'): decimal.
    Returns None if the text is not an amount.
    """
    if text is None:
        return None
    s = _CURRENCY.sub("", str(text)).strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith("-"):
        negative, s = True, s[1:].strip()
    s = s.replace(" ", "").replace("'", "").replace(" ", "")
    if not re.fullmatch(r"\d[\d.,]*", s) or s[-1] in ".,":
        return None

    if "." in s and "," in s:
        decimal = "." if s.rfind(".") > s.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        if s.count(decimal) > 1:
            return None
        s = s.replace(thousands, "").replace(decimal, ".")
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        groups = s.split(sep)
        if len(groups) > 2 or len(groups[-1]) == 3:
            if not all(len(g) == 3 for g in groups[1:]) or len(groups[0]) > 3 and len(groups) > 2:
                return None
            s = "".join(groups)
        else:
            s = s.replace(sep, ".")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


_NUMBER_IN_TEXT = re.compile(r"(?<![\w])\d{1,3}(?:[.,']\d{3})+(?:[.,]\d{1,2})?(?![\d])|(?<![\w.,])\d+(?:[.,]\d+)?(?![\d])")


def numbers_in_text(text: str) -> set[float]:
    """Every number printed in ``text``, under every plausible reading.

    '1.500' yields both 1500 and 1.5: for checking whether a value was printed,
    accepting either reading is the safe choice.
    """
    found: set[float] = set()
    for token in _NUMBER_IN_TEXT.findall(text):
        parsed = parse_amount(token)
        if parsed is not None:
            found.add(parsed)
        plain = token.replace(",", ".") if token.count(",") == 1 and "." not in token else token
        try:
            found.add(float(plain))
        except ValueError:
            pass
    return found


# --- dates -------------------------------------------------------------------------------

_EN_MONTHS = {
    m: i for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
         ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
         ("oct", "october"), ("nov", "november"), ("dec", "december")], start=1)
    for m in names
}
_TH_MONTHS = {
    m: i for i, names in enumerate(
        [("ม.ค.", "มกราคม"), ("ก.พ.", "กุมภาพันธ์"), ("มี.ค.", "มีนาคม"), ("เม.ย.", "เมษายน"),
         ("พ.ค.", "พฤษภาคม"), ("มิ.ย.", "มิถุนายน"), ("ก.ค.", "กรกฎาคม"), ("ส.ค.", "สิงหาคม"),
         ("ก.ย.", "กันยายน"), ("ต.ค.", "ตุลาคม"), ("พ.ย.", "พฤศจิกายน"), ("ธ.ค.", "ธันวาคม")], start=1)
    for m in names
}
MIN_YEAR, MAX_YEAR = 1900, 2100


@dataclass(frozen=True)
class ParsedDate:
    raw: str
    status: Literal["valid", "invalid", "unparseable"]
    value: dt.date | None = None
    note: str = ""  # how it was read, or why it is invalid

    @property
    def iso(self) -> str | None:
        return self.value.isoformat() if self.value else None


def parse_date(raw: str) -> ParsedDate:
    text = normalize_text(raw).rstrip(".,;")
    for parse in (_iso, _numeric_day_first, _textual, _vietnamese):
        result = parse(raw, text)
        if result is not None:
            return result
    return ParsedDate(raw, "unparseable", note="not a recognisable date")


def _build(raw: str, y: int, m: int, d: int, notes: list[str]) -> ParsedDate:
    if y < 100:
        y += 2000
        notes.append("two-digit year read as 20xx")
    if y > 2400:
        y -= 543
        notes.append("Buddhist Era year converted (-543)")
    if not MIN_YEAR <= y <= MAX_YEAR:
        return ParsedDate(raw, "invalid", note=f"year {y} is out of range")
    try:
        return ParsedDate(raw, "valid", dt.date(y, m, d), "; ".join(notes))
    except ValueError as exc:
        return ParsedDate(raw, "invalid", note=f"{exc} (read as day {d}, month {m}, year {y})")


def _iso(raw, text):
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    return _build(raw, int(m[1]), int(m[2]), int(m[3]), []) if m else None


def _numeric_day_first(raw, text):
    m = re.fullmatch(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{2}|\d{4})", text)
    if not m:
        return None
    a, b, y = int(m[1]), int(m[2]), int(m[3])
    if b > 12 >= a:  # 03/25/2024 can only be month-first
        return _build(raw, y, a, b, ["month-first (second number > 12)"])
    return _build(raw, y, b, a, [])


def _textual(raw, text):
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([^\d\s,]+)\.?,?\s+(\d{2,4})", text)  # 15 March 2024
    if m:
        month = _EN_MONTHS.get(m[2].rstrip(".")) or _TH_MONTHS.get(m[2]) or _TH_MONTHS.get(m[2] + ".")
        if month:
            return _build(raw, int(m[3]), month, int(m[1]), [])
        return None
    m = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", text)  # March 15, 2024
    if m and m[1] in _EN_MONTHS:
        return _build(raw, int(m[3]), _EN_MONTHS[m[1]], int(m[2]), [])
    return None


def _vietnamese(raw, text):
    m = re.search(r"ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})", text)
    return _build(raw, int(m[3]), int(m[2]), int(m[1]), []) if m else None
