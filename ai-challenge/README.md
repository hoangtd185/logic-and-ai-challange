# Medical Document Extractor

Papaya Insurtech, AI Challenge 08.

Give it a medical document (PDF or image) and it returns structured JSON:
- the document type (receipt, discharge summary, lab report, prescription, or `unknown`);
- every field as `{value, confidence}`, with `null` for anything not printed;
- validation errors (impossible dates, non-positive amounts, totals that don't add up).

Extraction uses Gemini vision with structured output. Confidence is recalibrated by
checks the model can't influence: the PDF's text layer, two independent readings of a
scan, and the validation rules.

- **Design and results:** [WRITEUP.md](WRITEUP.md): prompt design, how confidence
  works, key decisions, evaluation, what I'd improve, time spent.
- **The 10 test documents and their traps:** [samples/MANIFEST.md](samples/MANIFEST.md).
- **AI conversation log:** [full chat history with the coding agent](https://docs.google.com/document/d/1pawlNyBATXu6OVMBbMFonx0i2eHANyMXZ-wMrhpvgBM/edit?usp=sharing).

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
copy .env.example .env                 # macOS/Linux: cp; then set GEMINI_API_KEY in .env

python -m medextract extract samples/documents/receipt_02.pdf
python -m medextract serve             # web page at http://127.0.0.1:8000
```

## Setup

Requirements:
- Python 3.11 or newer.
- A Gemini API key: [Google AI Studio](https://aistudio.google.com/) has a free tier.

`pip install -e ".[dev]"` installs everything:
- the pipeline: google-genai, pydantic, PyMuPDF;
- the web page: FastAPI, uvicorn;
- pytest.

Settings are read from the environment or `.env`; real environment variables take
precedence:

| Variable | Default | |
|---|---|---|
| `GEMINI_API_KEY` | *(required)* | `GOOGLE_API_KEY` also works |
| `GEMINI_MODEL` | `gemini-3.8-flash` | any Gemini model with vision and structured output |
| `GEMINI_THINKING_LEVEL` | `low` | |
| `GEMINI_TIMEOUT_S` | `90` | per network operation (connect, each upload chunk, waiting for bytes) |
| `GEMINI_DEADLINE_S` | `120` | hard limit per attempt, upload included; a healthy call takes ~12 s |
| `GEMINI_MAX_RETRIES` | `4` | network errors back off 2 / 4 / 8 / 16 s; overload (429 / 503 "high demand") 15 / 30 / 60 s, or as long as the server asks |
| `MEDEXTRACT_NO_CACHE` | *(off)* | `1` forces fresh model calls |

Thresholds such as the 5% total tolerance, the confidence caps and the 0.6 "review"
line are constants in [`src/medextract/config.py`](src/medextract/config.py), each
documented.

## How to run

### Command line

```bash
# One document: prints the result JSON (add --trace for the raw model replies)
python -m medextract extract path/to/document.pdf

# A batch: every PDF / image in the given folders
python -m medextract batch samples/documents samples/negative/documents --out results

# Finish an interrupted batch: reruns only documents without a successful result
python -m medextract batch samples/documents samples/negative/documents --out results --only-failed
```

`batch` writes into `--out`:

| File | |
|---|---|
| `<doc>.json` | the result, in the brief's output format |
| `raw/<doc>.json` | trace: raw model replies, deterministic adjustments, validation, confidence changes, timing, token usage |
| `summary.md` | one line per document: type, confidence, nulls, check used, fields lowered, timings, validation |
| `confidence_report.md` | every field whose confidence was lowered, and why |
| `runs.json` | latest status of every document (used by `--only-failed`) |

**Caching and failures:**
- Model replies are cached in `.cache/llm/`, keyed by model, prompt, images and schema.
  Re-runs cost nothing, and a cached call keeps the timing of the original.
- Each attempt has a hard 120 s deadline, which covers the upload and the SDK's own
  resend. A request that stalls is aborted, and network errors are retried.
- When Gemini is overloaded (503 "high demand", 429), retries wait 15, 30 or 60 s, or
  as long as the server asks, instead of adding to the load every 2 s.
- Large uploaded images (over 300 KB, e.g. scans stored as PNG) are sent as JPEG
  quality 85: about 160 KB instead of about 1.2 MB, with no visible loss.
- After 3 documents in a row fail with network errors, the batch stops and prints the
  `--only-failed` command to resume.
- The batch prints each step as it finishes (`classify`, `extract`, `re-read scan`). A
  call that takes a while reports "waiting for Gemini…", then "still waiting (N s)…",
  and retry warnings name the step they belong to.
- The scan double-check (second reading) is optional and gets one retry. If it still
  fails, the document is saved as **partial** (model confidence only), and
  `--only-failed` retries it later.

### Web page

```bash
python -m medextract serve             # http://127.0.0.1:8000   (--port, --host, --reload)
```

What the page does:
- **Upload:** choose or drop a file. A preview appears beside the result.
- **Result:**
  - the document type, with the model's reason;
  - validation errors at the top;
  - the JSON, with values below 0.6 confidence highlighted. Each highlighted value has
    a note saying why its confidence was lowered.
- **Copy / Download** the JSON.

The page is plain HTML/CSS/JS ([`web/static/index.html`](src/medextract/web/static/index.html))
served by a small FastAPI app.

| Endpoint | |
|---|---|
| `POST /api/extract` | multipart `file` → `{"result": …, "meta": …}`. Errors: 400 unsupported file, 413 over 15 MB, 500 no API key, 502 Gemini error, 503 Gemini unreachable |
| `GET /api/health` | `{"ok": true, "model": …}` |
| `GET /api/docs` | interactive API docs |

### Evaluation

```bash
python -m medextract.evaluate          # results/ vs samples/ground_truth
python -m medextract.evaluate --results other_results --samples samples
```

It prints a summary table and writes two files: `results/eval_report.md`, and
`results/eval.json` with every field's expected value, actual value, confidence and
outcome.

What it scores:
- **Classification:** all 11 documents, including the non-medical one, which must come
  out `unknown`.
- **Fields:** per document type and per field. Every outcome counts, including the
  correct ones:
  - null expected and null returned: correct;
  - a value where null was expected: **hallucinated**;
  - null where a value was expected: **missed**;
  - a different value: **wrong**.
- **Dates** are compared as ISO dates.
- **Validation:** expected vs actual `validation_errors`.
- **Confidence:** whether wrong values score lower than correct ones, for Gemini's raw
  score and for the calibrated one.

Results from before validation existed are marked as such, not counted as misses.

### Tests

```bash
pytest                 # ~375 tests, no network, no API key needed
```

Gemini is mocked in every unit test (`tests/conftest.py`). A guard that runs
automatically in every test makes any outside network connection fail, so no test can
call the API by accident.

### Regenerating the sample documents

Only needed if you change the sample data or templates. The generated files are
committed.

```bash
pip install -e ".[samples]"
playwright install chromium
python samples/generator/build.py      # or one document: build.py receipt_02
```

Each document is built from one data file (`samples/generator/data/*.json`) through a
Jinja2 HTML template, rendered to PDF or to a degraded "scanned" PNG. Its ground truth
comes from the same data. The build refuses to write a sample if:
- an expected value isn't actually printed on the page; or
- a planted trap doesn't hold (e.g. the 8% total mismatch).

## Output format

```json
{
  "document_type": "receipt",
  "confidence": 0.97,
  "fields": {
    "hospital_name": { "value": "Lotus Bay International Hospital", "confidence": 0.95 },
    "date": { "value": "05/04/2024", "confidence": 0.95 },
    "grand_total": { "value": 24950.0, "confidence": 0.3 },
    "payment_method": { "value": null, "confidence": 0.0 }
  },
  "validation_errors": [
    { "field": "grand_total", "code": "TOTAL_MISMATCH", "severity": "error",
      "message": "line items sum to 22,950.00 but the grand total is 24,950.00 (8.02% apart, more than 5%)" }
  ]
}
```

- **Every field is `{value, confidence}`**, including each row of `items`, `tests` and
  `medications`, and diagnosis `primary` / `secondary`.
- **Not printed → `null` with confidence ≤ 0.2.** The schema rejects a confident null.
- **Dates are returned as printed.** Checking that a date is real is a validation rule,
  so an impossible date is still returned as printed, and flagged.
- **`unknown` documents** return every field name from the four types, all null.

## Project layout

```
src/medextract/
  schemas/        output models: one per document type + the envelope (pydantic)
  llm/            Gemini client (timeouts, retries, cache), prompts (*.md), schema conversion
  ingest.py       PDF → page images + text layer (PyMuPDF); images passed through
  classify.py     step 1: document type
  extract.py      step 2: fields for that type; amount parsing; null rule
  validation.py   date / amount / total rules → validation_errors
  confidence.py   calibration: validation, text layer, two readings
  parsing.py      locale-aware amounts and dates (VND, THB, Buddhist Era, Vietnamese)
  pipeline.py     ingest → classify → extract → validate → calibrate
  batch.py        batch runs, run state, reports
  evaluate.py     scoring against ground truth
  web/            FastAPI app + the upload page
samples/          10 test documents + 1 negative, ground truth, generator
tests/unit/       unit tests (Gemini mocked)
results/          batch output and evaluation report
```
