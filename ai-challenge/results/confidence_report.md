# Confidence report

Final confidence = min(model score, validation cap, text-layer cap for PDFs, two-reading agreement cap for scans). Only fields whose confidence was lowered are listed.

## discharge_01.pdf (discharge_summary, check: text layer)

No confidence lowered.

## discharge_02.pdf (discharge_summary, check: text layer)

Validation: `INVALID_DATE` on `discharge_date` (error): '30/02/2024' is not a valid date: day is out of range for month (read as day 30, month 2, year 2024)

| Field | Model | Final | Why |
|---|---|---|---|
| `discharge_date` | 0.99 | 0.30 | validation INVALID_DATE: '30/02/2024' is not a valid date: day is out of range for month (read as day 30, month 2, year 2024) |

## discharge_03.pdf (discharge_summary, check: text layer)

No confidence lowered.

## electricity_bill_01.pdf

Not processed (failed).

## lab_01.pdf (lab_report, check: text layer)

No confidence lowered.

## lab_02.png

Not processed (failed).

## prescription_01.pdf (prescription, check: text layer)

No confidence lowered.

## prescription_02.png (prescription, check: 2 readings)

No confidence lowered.

## receipt_01.pdf (receipt, check: text layer)

No confidence lowered.

## receipt_02.pdf

Not processed (failed).

## receipt_03.png

Not processed (failed).

