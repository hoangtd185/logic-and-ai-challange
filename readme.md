# Papaya Insurtech — Full-Stack Engineer Take-home

**Candidate:** Trần Định Hoàng

This repository contains both parts of the take-home:

| Part | Where | What |
|---|---|---|
| 1. Logical Questions | [`logical-questions/ANSWERS.md`](logical-questions/ANSWERS.md) | Q1 and Q5 (required), plus Q8, Q9, Q10 |
| 2. AI Engineering Challenge | [`ai-challenge/`](ai-challenge/) | **Challenge 08 — Medical Document Extractor** (Advanced, 3 points) |

---

## Part 1 — Logical Questions

Answered in Vietnamese: [`logical-questions/ANSWERS.md`](logical-questions/ANSWERS.md).

| Question | Tags |
|---|---|
| Q1 — First memory *(required)* | `M` |
| Q5 — The last time I forgot something *(required)* | `M, O` |
| Q8 — Fastest way to refuel at a gas station | `Cr, Q` |
| Q9 — Designing the door of a public men's restroom | `O, D` |
| Q10 — Picking a "good, healthy, cheap" fruit stall without asking anyone | `O, Cr, P` |

---

## Part 2 — AI Challenge 08: Medical Document Extractor

A pipeline that takes a medical document (PDF or image) and returns structured JSON:
the document type, every field as `{value, confidence}`, and validation errors.

- **AI conversation log (required):** [Full chat history with the coding agent](https://docs.google.com/document/d/1pawlNyBATXu6OVMBbMFonx0i2eHANyMXZ-wMrhpvgBM/edit?usp=sharing).
  These are the verbatim messages I sent to the agent, phase by phase.
- **How to run:** [`ai-challenge/README.md`](ai-challenge/README.md)
- **Approach, decisions, prompt engineering, time spent:** [`ai-challenge/WRITEUP.md`](ai-challenge/WRITEUP.md)
- **The 10 test documents and their deliberate traps:** [`ai-challenge/samples/MANIFEST.md`](ai-challenge/samples/MANIFEST.md)
- **Extraction results:** [`ai-challenge/results/`](ai-challenge/results/)
- **Evaluation against ground truth:** [`ai-challenge/results/eval_report.md`](ai-challenge/results/eval_report.md)

### Stack

Python 3.11+, Gemini (vision + structured output) via `google-genai`, Pydantic,
PyMuPDF, FastAPI (upload page), pytest (Gemini mocked, no network in tests).

### Quick start

```bash
cd ai-challenge
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env                 # macOS/Linux: cp — then set GEMINI_API_KEY

python -m medextract extract samples/documents/receipt_02.pdf
python -m medextract serve             # web page at http://127.0.0.1:8000
pytest
```

### Key decisions

- **Ground truth first.** Every sample document is rendered from its own data file,
  so the expected answer always matches what is printed.
- **The schema checks shape; validation checks business rules.** An impossible date or
  a total mismatch goes into `validation_errors`; it never discards the rest of the
  document.
- **Never guess.** A field not printed on the document is `null` with low confidence.
  Lab flags are not derived from the reference range when none is printed.
- **Confidence is recalibrated** by checks the model can't influence: the PDF text
  layer, two independent readings of a scan, and the validation rules.
- **`unknown` document type** for anything that isn't one of the four medical types
  (tested with a non-medical electricity bill).

### Timeline

| | |
|---|---|
| Estimate | 6–7 hours |
| Actual | `<fill in>` |

Details per phase in [`WRITEUP.md`](ai-challenge/WRITEUP.md#7-time-spent).
