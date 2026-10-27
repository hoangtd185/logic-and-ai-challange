# Evaluation against ground truth

Documents: 5 evaluated of 11 (not run: prescription_01, prescription_02, receipt_01, receipt_02, receipt_03, electricity_bill_01)

> **5 result(s) predate validation and confidence calibration** (discharge_01, discharge_02, discharge_03, lab_01, lab_02). Their fields are scored; their validation errors and calibrated confidence are not. Rerun the batch to include them.

## Headline

- Classification: 5/5 (100%)
- Fields: 98/98 (100%) (hallucinated 0, missed 0, wrong value 0)
- Validation errors (0 documents checked): 0 matched, 0 missing, 0 unexpected

## Per document

| Document | Expected | Predicted | Fields correct | Hallucinated | Missed | Wrong | Validation |
|---|---|---|---|---|---|---|---|
| discharge_01 | discharge_summary | discharge_summary ✓ | 9/9 (100%) | 0 | 0 | 0 | not checked (predates validation) |
| discharge_02 | discharge_summary | discharge_summary ✓ | 9/9 (100%) | 0 | 0 | 0 | not checked (predates validation) |
| discharge_03 | discharge_summary | discharge_summary ✓ | 9/9 (100%) | 0 | 0 | 0 | not checked (predates validation) |
| lab_01 | lab_report | lab_report ✓ | 43/43 (100%) | 0 | 0 | 0 | not checked (predates validation) |
| lab_02 | lab_report | lab_report ✓ | 28/28 (100%) | 0 | 0 | 0 | not checked (predates validation) |
| prescription_01 | prescription | NOT RUN | - | - | - | - | - |
| prescription_02 | prescription | NOT RUN | - | - | - | - | - |
| receipt_01 | receipt | NOT RUN | - | - | - | - | - |
| receipt_02 | receipt | NOT RUN | - | - | - | - | - |
| receipt_03 | receipt | NOT RUN | - | - | - | - | - |
| electricity_bill_01 | unknown | NOT RUN | - | - | - | - | - |

## Per document type

| Type | Fields correct |
|---|---|
| discharge_summary | 27/27 (100%) |
| lab_report | 71/71 (100%) |

## Per field

| Type | Field | Correct |
|---|---|---|
| discharge_summary | `admission_date` | 3/3 (100%) |
| discharge_summary | `attending_physician` | 3/3 (100%) |
| discharge_summary | `diagnosis.primary` | 3/3 (100%) |
| discharge_summary | `diagnosis.secondary` | 3/3 (100%) |
| discharge_summary | `discharge_date` | 3/3 (100%) |
| discharge_summary | `discharge_instructions` | 3/3 (100%) |
| discharge_summary | `hospital_name` | 3/3 (100%) |
| discharge_summary | `patient_name` | 3/3 (100%) |
| discharge_summary | `procedures_performed` | 3/3 (100%) |
| lab_report | `date` | 2/2 (100%) |
| lab_report | `lab_name` | 2/2 (100%) |
| lab_report | `patient_name` | 2/2 (100%) |
| lab_report | `tests[].flag` | 13/13 (100%) |
| lab_report | `tests[].reference_range` | 13/13 (100%) |
| lab_report | `tests[].result` | 13/13 (100%) |
| lab_report | `tests[].test_name` | 13/13 (100%) |
| lab_report | `tests[].unit` | 13/13 (100%) |

## Does confidence mean something?

Among values the pipeline returned (non-null). *Gemini's score* is the model's own confidence (from the trace); *calibrated* is the final confidence after validation, text-layer and two-reading checks (only results that went through calibration).

|  | Gemini's score | Calibrated |
|---|---|---|
| Correct values: count | 94 | 0 |
| Correct values: mean confidence | 0.99 | None |
| Wrong values (incl. hallucinated): count | 0 | 0 |
| Wrong values: mean confidence | None | None |
| P(correct value scores above wrong) | None | None |

With no wrong values there is nothing to separate: confidence can't be judged on this run.

- P(correct value scores above wrong): the chance a random correct value has higher confidence than a random wrong one. 0.5 = confidence carries no information, 1.0 = perfect separation.
- Correct nulls: mean confidence None (low by design: 'not found')
- Missed values (returned null): mean confidence None

| Confidence | Values | Accuracy |
|---|---|---|

## Every wrong field

| Document | Field | Outcome | Expected | Got | Confidence |
|---|---|---|---|---|---|
| - | - | - | - | - | - |

## Validation errors

| Document | Expected | Actual |
|---|---|---|
| discharge_01 | - | - |
| discharge_02 | INVALID_DATE on discharge_date | - |
| discharge_03 | - | - |
| lab_01 | - | - |
| lab_02 | - | - |
