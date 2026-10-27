This document is a PRESCRIPTION.

- `doctor_name`: the prescribing doctor, as printed (header or signature).
- `patient_name`: as printed.
- `date`: the date the prescription was issued.
- `medications`: one entry per prescribed drug, in printed order:
  - `name`: the drug name as printed (generic or brand).
  - `dosage`: strength per unit as printed ("500 mg", "100 mcg/puff").
  - `frequency`: how often and how much, as printed ("1 tablet twice daily", "2 puffs as needed").
  - `duration`: how long, as printed ("7 days"), or null if not printed. Do not assume a duration for "as needed" drugs.
  - `quantity`: the amount to dispense as printed ("14 tablets"; a plain number if only a number is printed), or null if not printed. Do not compute it from frequency x duration.
