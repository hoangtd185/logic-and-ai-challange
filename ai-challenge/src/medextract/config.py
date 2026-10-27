"""Pipeline-wide constants and runtime settings.

Constants live at module level. Runtime settings (API key, model) are read from
the environment, with a ``.env`` file in the project root as a fallback, and are
only loaded when a pipeline actually needs them. Importing this module never
requires an API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- schema rules ----------------------------------------------------------------

# Highest confidence a null value may carry.
#
# Why this exists: the brief requires that a field not visible in the document is
# null with LOW confidence, never fabricated. Confidence is the signal a claims
# reviewer uses to decide what to double-check, so "not found" must never look
# like a confident answer. Anything above this ceiling on a null is rejected by
# the schema instead of being silently accepted.
#
# Why 0.2: it sits clearly below any value we would call "trustworthy" and leaves
# room to tell "definitely absent" (0.0) apart from "possibly there but
# unreadable" (e.g. 0.1-0.2, a smudged total). Tune it together with the
# confidence calibration step, not on its own.
NULL_CONFIDENCE_MAX = 0.2

# Receipt check from the brief: flag when the line items and the grand total
# differ by more than this fraction of the grand total. The sample generator
# uses the same constant, so the planted traps (8% flagged, 3% not flagged)
# stay on the correct side of the line if the threshold ever changes.
TOTAL_MISMATCH_TOLERANCE = 0.05

# A line item's quantity x unit price may differ from its printed total by this much
# (rounding on the printed unit price) before ITEM_TOTAL_MISMATCH is raised.
ITEM_TOTAL_TOLERANCE = 0.01

# --- confidence calibration ------------------------------------------------------------
# Final field confidence = min(Gemini's own score, every cap below that applies).
# Caps rather than multipliers: one failed check should put a field below the
# "needs review" line however confident the model was.

# The field fails a validation rule (error severity / warning severity).
CONF_CAP_VALIDATION_ERROR = 0.3
CONF_CAP_VALIDATION_WARNING = 0.6
# PDF with a text layer: the value does not appear in it, so it may be made up...
CONF_CAP_NOT_IN_TEXT = 0.3
# ...or only part of it appears (long text the model reworded or truncated).
CONF_CAP_PARTLY_IN_TEXT = 0.6
# Fraction of a text value's words that must appear in the text layer to count as found.
TEXT_FOUND_COVERAGE = 0.9
TEXT_PARTLY_COVERAGE = 0.6
# Scans: two independent readings disagree on the field.
CONF_CAP_READINGS_DISAGREE = 0.4
# Two long texts count as the same reading at this similarity (0-1).
READINGS_TEXT_SIMILARITY = 0.9

# The web page highlights returned values below this confidence for review.
LOW_CONFIDENCE_THRESHOLD = 0.6
# Largest file the web page accepts.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# Confidence the pipeline assigns when it marks a field as missing on its own
# (e.g. the fallback when extraction fails, or every field of an "unknown"
# document). Zero: we have no evidence at all.
MISSING_CONFIDENCE = 0.0

# --- ingestion -------------------------------------------------------------------

# PDF pages are rendered to PNG at this resolution. 150 dpi keeps 8-pt print
# legible while an A4 page stays around 1240 x 1750 px.
PDF_RENDER_DPI = 150
# Refuse longer documents instead of silently truncating them.
MAX_PAGES = 10
# Gemini caps a whole inline request at 20 MB; leave room for prompts and base64.
MAX_INPUT_BYTES = 14 * 1024 * 1024
# Uploaded images (scans, phone photos) larger than this are re-encoded as JPEG before
# being sent. A grainy scan stored as PNG is ~1 MB; the same image as JPEG quality 85
# is ~160 KB with no visible loss, and every scan is sent 3 times (classify, extract,
# second reading), so on a slow uplink this is most of the upload time.
IMAGE_REENCODE_ABOVE_BYTES = 300 * 1024
IMAGE_JPEG_QUALITY = 85
# Larger photos are scaled down to this long side (still ~250 dpi on an A4 page).
IMAGE_MAX_SIDE = 2400

# --- runtime settings --------------------------------------------------------------

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING_LEVEL = "low"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "llm"
# httpx's per-operation timeout (connect, each upload chunk, waiting for bytes).
# It does not cap a whole request; DEFAULT_DEADLINE_S does.
DEFAULT_TIMEOUT_S = 90.0
# Hard wall-clock limit for one attempt: upload + server time + the SDK's own resend.
# A healthy call takes ~12 s; slow but successful calls on an overloaded day took 36-74 s.
DEFAULT_DEADLINE_S = 120.0
# Retries on network errors, rate limits and 5xx, with 2/4/8/16 s backoff (~30 s total).
DEFAULT_MAX_RETRIES = 4
# The scan's second reading is optional: if it fails, the document is still returned
# with the model's own confidence. It gets a small retry budget instead of the full one,
# so a bad connection can't stall a batch for ~8 minutes on a nice-to-have call.
SECOND_READING_MAX_RETRIES = 1
# While a model call is in flight, the CLI says so after this many seconds and
# then every PROGRESS_HEARTBEAT_S, so a slow call doesn't look like a hang.
PROGRESS_FIRST_NOTICE_S = 3.0
PROGRESS_HEARTBEAT_S = 30.0
# A batch stops after this many documents in a row fail with network errors.
DEFAULT_MAX_CONSECUTIVE_NETWORK_FAILURES = 3


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str = DEFAULT_MODEL
    thinking_level: str = DEFAULT_THINKING_LEVEL
    cache_dir: Path | None = DEFAULT_CACHE_DIR
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    deadline_s: float = DEFAULT_DEADLINE_S

    def __repr__(self) -> str:  # never print the key
        return (
            f"Settings(model={self.model!r}, thinking_level={self.thinking_level!r}, "
            f"timeout_s={self.timeout_s}, deadline_s={self.deadline_s}, max_retries={self.max_retries}, "
            f"cache_dir={self.cache_dir!r})"
        )


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser: comments, blank lines, optional quotes, `export`."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value or "\t#" in value:  # inline comment after an unquoted value
            value = value.split("#", 1)[0].rstrip()
        values[key] = value
    return values


def load_settings(env_file: Path | None = None) -> Settings:
    """Real environment variables win over the .env file."""
    file_values = read_env_file(env_file or PROJECT_ROOT / ".env")

    def get(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name) or file_values.get(name) or default

    api_key = get("GEMINI_API_KEY") or get("GOOGLE_API_KEY")
    if not api_key:
        raise ConfigError(
            "GEMINI_API_KEY is not set. Add it to the environment or to a .env file "
            f"in {PROJECT_ROOT} (see .env.example)."
        )
    no_cache = (get("MEDEXTRACT_NO_CACHE", "") or "").lower() in {"1", "true", "yes"}
    try:
        timeout_s = float(get("GEMINI_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))
        max_retries = int(get("GEMINI_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))
        deadline_s = float(get("GEMINI_DEADLINE_S", str(DEFAULT_DEADLINE_S)))
    except ValueError as exc:
        raise ConfigError(f"GEMINI_TIMEOUT_S / GEMINI_DEADLINE_S / GEMINI_MAX_RETRIES must be numbers: {exc}") from exc
    return Settings(
        api_key=api_key,
        model=get("GEMINI_MODEL", DEFAULT_MODEL),
        thinking_level=get("GEMINI_THINKING_LEVEL", DEFAULT_THINKING_LEVEL),
        cache_dir=None if no_cache else DEFAULT_CACHE_DIR,
        timeout_s=timeout_s,
        max_retries=max_retries,
        deadline_s=deadline_s,
    )
