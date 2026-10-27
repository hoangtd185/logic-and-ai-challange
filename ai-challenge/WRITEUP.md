# Writeup: Medical Document Extractor

> Draft. Sections marked **TODO** need numbers from the full batch run or your own figures.

## 1. Approach

I split the work into phases, each testable on its own before the next started:

1. **Output schemas.** The contract every other part produces or consumes.
2. **Sample documents and ground truth, built before the pipeline.** I had a way to
   measure accuracy from the very first extraction.
3. **Pipeline.** Ingest → classify → extract, using Gemini structured output.
4. **Validation and confidence calibration.**
5. **Evaluation** against the ground truth.
6. **Web page.**
7. **Test gaps.** Filling them found four real bugs (§4).

The 10 test documents are synthetic: HTML templates rendered to PDF, three of them
degraded into "phone scans" (rotated, noisy, no text layer). Each has planted traps:
- fields that are not printed and must come back null: unpaid invoice → no payment
  method, blank signature → no physician, no secondary diagnosis, a lab test with no
  unit or flag, a prescription with no duration or quantity;
- an 8% total mismatch (must be flagged) and a 3% one (must not);
- an impossible date (30/02/2024);
- a Vietnamese "Giấy ra viện";
- a 2-page document.

An eleventh, non-medical document (an electricity bill with a name, a date, charges, a
total and a payment method) must come out `unknown`. The ground truth is generated from
the same data as the document, and the build checks that every expected value is
actually printed, so the answers key can't be wrong.

## 2. Prompt design

**Two calls: classify, then extract with a type-specific prompt and schema.** A single
call with one large schema for all four types is cheaper. Two calls give me:
- a smaller, unambiguous schema per type;
- a classification confidence that doesn't depend on extraction;
- a cheap exit for `unknown` documents, which skip extraction entirely.

The cost is a second call: about 12 s each when the network is healthy.

**Classification** ([`classify.md`](src/medextract/llm/prompts/classify.md)):
- **Definitions per type,** with the regional names for discharge summaries ("Giấy ra
  viện", "ใบสรุปการจำหน่าย").
- **An `unknown` class with concrete negatives:** utility bills, ID cards, insurance
  cards, referral letters.
- **The rule "decide by what the document *is*, not by words in it".** A discharge
  summary that lists medications is not a prescription.
- **Evidence first.** The schema orders the reply `evidence` → `document_type` →
  `confidence`, so the model states the title and features it saw before committing to
  a label.
- **A confidence rubric** with anchored bands (explicit title → 0.95+; content only →
  0.75–0.94, …) instead of "give a confidence".

**Extraction** has a shared rules prompt
([`extract_common.md`](src/medextract/llm/prompts/extract_common.md)) plus one prompt
per type. The shared rules come down to three:
1. **Extract only what is printed.** Never infer, compute, translate or complete. Not
   visible or illegible → `null`. It also names the tempting substitutes outright: a
   due date is not an invoice date, a nurse is not the attending physician, a
   reference range is not a flag.
2. **Keep values as printed.**
   - Text keeps its own language, diacritics and honorifics.
   - Dates are copied exactly; no reformatting, no calendar conversion, no "fixing"
     30/02.
   - Amounts are returned as plain numbers, read with the document's own locale
     separators.
3. **Confidence means "how likely is this exact value right",** with bands. It
   explicitly asks the model not to give every field the same score.

The per-type prompts define each field and its traps:
- the invoice date vs the due date;
- don't assume quantity 1; don't compute a line total; don't "fix" a grand total;
- the lab flag only from a printed H / L / N, ↑ or *;
- no duration assumed for an "as needed" drug.

They're written for the document type, not for my samples. The Vietnamese labels they
mention ("Bệnh kèm theo") are standard form vocabulary, not text copied from a test
document.

**Structured output, not free-text JSON.**
- **One source for the schema.** The JSON Schema sent to Gemini is generated from the
  same pydantic models that define the output ([`llm/schema.py`](src/medextract/llm/schema.py)),
  converted to the subset Gemini supports (no `$ref`, nullable types as `[T, "null"]`).
  The two can't drift apart.
- **Every key is required and extra keys are forbidden,** so the model must return an
  explicit `null` rather than omit a field, and can't add fields of its own.
- **A converter bug found by inspecting the generated schema:** a nullable enum needs
  `null` listed among its allowed values, otherwise the model can't return a null lab
  flag at all.

**The model reads; code decides.** The model is never asked to:
- validate a date,
- convert a Buddhist Era year,
- add up line items, or
- derive a lab flag.

Those are deterministic jobs, done in code where they can be tested.

## 3. How confidence works

Gemini's own scores are not usable as they are. In the first run, all 94 values it
returned scored ≥ 0.95 (mean 0.99). So the model's score is only the starting point:

```
final = min( Gemini's score,
             0.3  if the field fails a validation error   (0.6 for a warning),
             0.3  if a PDF's text layer doesn't contain the value   (0.6 if only partly),
             0.4  if a scan's two independent readings disagree )
```

| Check | Applies to | What it catches |
|---|---|---|
| **Validation** | all documents | Impossible dates, discharge before admission, non-positive amounts, items vs total > 5%. A mismatch lowers *both* sides (`grand_total` and `items`; admission and discharge dates), because either could be the misread one. |
| **Text layer** | PDFs with a text layer | Values the model made up or misread. A text value must appear in the page text: short values (≤ 3 words) fully, longer ones ≥ 90% of their words. Numbers are compared after parsing every number on the page, so `24,950.00` matches `24950`. The lab flag is skipped, because "high" is printed as "H". |
| **Two readings** | scans (no text layer) | Misreads on noisy images. The scan is extracted a second time with an "independent second reading" instruction, and every field is compared: names and dates exactly, numbers within 0.5%, long text at ≥ 90% similarity, and the number of rows in each list. |

Design choices:
- **Caps, not multipliers.** One failed check puts a field below the 0.6 review line
  however confident the model was.
- **Checks only lower, never raise.** Agreement between two readings doesn't make a
  value more certain than the model said.
- **Null fields are left alone.** The schema already forces a null to confidence ≤ 0.2,
  and a confident null is rejected.
- **Everything is explained.** Every lowered field is recorded with its reasons in the
  trace and in `results/confidence_report.md`, and the web page shows them inline.
- All caps live in `config.py`.

Known limits:
- **The two readings come from the same model on the same image,** so their errors
  are correlated. Agreement is weaker evidence than the text-layer check.
- **The text layer proves a value is printed, not that it's in the right field.** A
  correct hospital name put in `patient_name` would pass.

## 4. Key decisions

| Decision | Why |
|---|---|
| **A lab flag that isn't printed stays `null`.** It is never derived from the reference range, even when the range makes it obvious. | The brief says no fabricated data. A derived flag is an inference presented as a reading. If a computed flag is wanted, it belongs in a separate, labelled field computed in code. |
| **Not visible → `null` with confidence ≤ 0.2, enforced by the schema.** Blank strings and empty lists also count as null. | "Not found" can never look like a confident answer, and `""` or `[]` can't be used to get around it. |
| **Dates are stored as printed; validity is a validation rule.** | An invalid date like 30/02/2024 is flagged while the rest of the document is still returned. Rejecting it at the schema would have lost the whole document. |
| **Dates are read day-first;** Thai Buddhist Era years are converted (2567 → 2024); Vietnamese long-form dates are understood. | The region these claims come from. A date that can only be month-first (03/25/2024) is read that way. |
| **Validation never fails a document.** | Problems go into `validation_errors`. Even a crashing rule becomes a `VALIDATION_FAILED` warning. |
| **Exactly 5% is not flagged; more than 5% is.** Compared in cents. | The brief says "mismatch > 5%". Comparing in cents was a bug fix: 5.35 + 19.08 + 70.57 adds up to exactly 95.00, but in floating point the sum is 94.99999999999999, which flagged an exactly-5% receipt. |
| **Amounts are parsed in both styles:** `15,750.00`, `1.500.000 đ`, `1.500.000,50 VND`, `1,234.50 บาท`. A single 3-digit group (`1.500`) reads as thousands. | VND rarely has decimals, and a 3-decimal THB amount doesn't occur. The text-layer check accepts both readings, so the ambiguity can't cause a false alarm. |
| **An `unknown` document returns every field name from the four types, all null.** | The output always has the same shape, and nothing is guessed from a non-medical document. |
| **If the model's reply doesn't fit the schema, every field comes back null,** with `EXTRACTION_FAILED`. | No half-trusted partial data reaches a claim. |
| **Added `ITEM_TOTAL_MISMATCH`** (quantity × unit price ≠ line total) as a warning. | Not in the brief, but it points at the exact line with a misread digit, where `TOTAL_MISMATCH` only says the sum is off. |
| **Ground truth first, generated from the same data as the documents.** | Every phase could be measured from day one, and the ground truth can't disagree with what's printed. |
| **Explicit 90 s timeouts, our own logged retries, a disk cache, `--only-failed`.** | The first real run took 200 s for one document. Almost all of that was stalled connections, not model time: a normal call is ~12 s. The SDK had no timeout and retried silently. |
| **The scan double-check gets one retry, not four. If it fails, the document is saved as `partial` and `--only-failed` retries it.** | On a bad connection the second reading of `lab_02` timed out five times in a row, about 8 minutes on a call whose failure only means "keep the model's confidence". The batch also now prints each step as it finishes and announces a slow call, so a slow network doesn't look like a hang. |
| **A hard 120 s deadline per attempt, longer waits on overload, and large images sent as JPEG.** | The 90 s timeout turned out to apply to each network operation, not to a whole request. One attempt ran for more than 390 s, because a slow upload never trips it and the SDK quietly resends once. Gemini was also returning 503 "high demand", and retrying after 2 s only added to the load. Scans stored as PNG uploaded 1.2 MB per call (3 calls per scan). As JPEG quality 85 they are 164 KB, with no visible difference on the lab_02 table. |

## 5. Evaluation results

**TODO, after the full run:**

```bash
python -m medextract batch samples/documents samples/negative/documents --out results
python -m medextract.evaluate
```

Then copy the headline numbers from `results/eval_report.md` into this table:

| | Result |
|---|---|
| Classification (11 documents incl. the electricity bill → `unknown`) | TODO / 11 |
| Fields correct (incl. correct nulls) | TODO |
| Hallucinated / missed / wrong values | TODO / TODO / TODO |
| Validation errors: expected `TOTAL_MISMATCH` (receipt_02), `INVALID_DATE` (discharge_02); none elsewhere | TODO matched, TODO missing, TODO unexpected |
| Mean confidence, correct vs wrong values (Gemini's score) | TODO vs TODO |
| Mean confidence, correct vs wrong values (calibrated) | TODO vs TODO |
| Fields lowered by calibration (see `results/confidence_report.md`) | TODO |

**What is already measured:**
- **First live run (5 of 11 documents: 3 discharge summaries, 2 lab reports).** Run
  before validation existed:
  - classification 5/5, fields 98/98;
  - the planted nulls were all returned as null: the blank physician signature, the
    missing secondary diagnosis, the lab test with no printed unit or flag;
  - the Vietnamese document was extracted exactly;
  - every one of the 94 values Gemini returned scored ≥ 0.95, which is why
    calibration exists.
- **Validation on perfect extractions.** Fed the ground truth of each document as if
  it were the model's reply, validation raises exactly the planted errors: the 8%
  mismatch on receipt_02, the impossible date on discharge_02. It raises nothing on
  receipt_03 (3% off) or anywhere else.
- **Text-layer check on correct values.** On all 7 PDFs it lowered nothing, so
  correct values aren't penalised. That includes the 2-page document and the
  Vietnamese one.
- **Pipeline with a scripted model.** A made-up payment method on the unpaid invoice
  dropped from 0.90 to 0.30 ("value not found in the PDF text layer"), and a misread
  lab result on a scan dropped from 0.97 to 0.40 ("two readings disagree").
- **Unit tests:** about 375, with Gemini mocked and the network blocked.

**Caveats:**
- The set is 11 synthetic, cleanly printed documents that I generated myself, so
  accuracy here is an upper bound.
- If the model makes no mistakes, confidence calibration can't be measured on this set
  at all: there's nothing to separate.

## 6. What I'd improve

1. **A harder, real evaluation set.**
   - Real scans and phone photos, handwriting (prescriptions especially), low
     resolution, folded pages, multi-document PDFs, Thai-language documents.
   - Enough errors to measure whether confidence separates right from wrong (AUROC),
     and to fit the caps from data (e.g. isotonic calibration) instead of choosing
     them by hand.
2. **OCR as the scan's independent check.** Running OCR on scans and applying the same
   text check as for PDFs would be cheaper and more independent than a second reading
   by the same model.
3. **Per-field evidence.** Ask the model for the printed snippet or bounding box behind
   each value. That would catch a right value in the wrong field, which the text check
   can't.
4. **Speed and cost.** Classify on a downscaled thumbnail, or merge classify and
   extract into one call for confident types. Process documents concurrently.
5. **Richer receipts.** Currency as a field; tax, discount and insurance lines in the
   total check; flag ambiguous DD/MM dates (05/04) for review.
6. **Production concerns.**
   - Medical documents are sensitive personal data: use a paid / Vertex endpoint with
     no training on inputs, never log documents, and set retention limits.
   - Put authentication on the web page.
   - Use a job queue instead of one upload at a time.
   - Version prompts, and tie cache entries and evaluation runs to a prompt version.

## 7. Time spent

**TODO: fill in the actual hours.** The original estimate given at the start was
6–7 hours:

| Phase | Estimate | Actual |
|---|---|---|
| Planning, scaffold | 0.25 h | TODO |
| Sample documents + ground-truth generator | 1 h | TODO |
| Evaluator | 0.75 h | TODO |
| Output schemas + validation rules | 0.75 h | TODO |
| Pipeline: ingestion, classification, extraction, CLI | 2.25 h | TODO |
| Confidence calibration | 0.75 h | TODO |
| Network / timing investigation, retries, `--only-failed` | not planned | TODO |
| Web page | 0.5 h | TODO |
| Test gaps (+ 4 bugs found) | not planned | TODO |
| README + writeup | 0.5 h | TODO |
| **Total** | **6.75 h** | **TODO** |

Calendar span: 21–22 Oct for the build, 23 Oct for the documentation (from this session's timeline; your own hours may differ).

## 8. Working with the AI coding tool *(optional)*

- **Small phases, tests as the definition of done.** Each phase ended with something
  runnable and tested, and I reviewed the output before moving on.
- **Decisions stayed mine:**
  - samples before pipeline;
  - invalid dates go to validation, not the schema;
  - an `unknown` type;
  - flags never derived;
  - the confidence formula;
  - `--only-failed`.
- **Investigate before fixing.** For the 200 s document I asked where the time went
  before allowing any change. The answer (no SDK timeout, silent retries) changed
  what was fixed.
- **Probe edge cases before writing tests for them.** Filling the test gaps started by
  probing those areas directly, and found four bugs:
  - the floating-point 5% boundary;
  - the Thai currency word (`บาท`);
  - a mismatch message showing 5.0% for a 5.01% gap;
  - a crash on an out-of-schema document type.
- **Look at the generated artefacts, not only the tests.** I looked at rendered
  samples, the generated JSON Schema and screenshots of the page. That's how I found
  the nullable-enum bug, a phone-width layout bug, and a highlight that drowned the
  one uncertain receipt line.
