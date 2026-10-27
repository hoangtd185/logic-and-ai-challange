This document is a LABORATORY REPORT.

- `lab_name`: the laboratory that issued the report (letterhead).
- `patient_name`: as printed.
- `date`: the report date. If only a collection date is printed, use it.
- `tests`: one entry per test row, across all panels and pages:
  - `test_name`: as printed.
  - `result`: a number if the result is numeric (126, 6.9); otherwise the printed text ("Negative", "<0.5", "Reactive").
  - `unit`: as printed, or null if the row has no unit.
  - `reference_range`: exactly as printed ("70-99", "< 200", "Negative").
  - `flag`: ONLY from a flag or marker printed on that row: H, High, ↑ or * mean `high`; L, Low or ↓ mean `low`; N or Normal mean `normal`. If nothing is printed in the flag position, the flag is null. Never work out a flag by comparing the result with the reference range yourself.
