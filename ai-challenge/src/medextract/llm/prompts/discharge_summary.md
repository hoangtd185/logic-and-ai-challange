This document is a hospital DISCHARGE SUMMARY. It may span several pages and may be in Vietnamese or Thai; read every page.

- `hospital_name`: the hospital that issued the summary (letterhead).
- `patient_name`: as printed.
- `admission_date`, `discharge_date`: exactly as printed next to those labels ("Ngày vào viện" / "Ngày ra viện" in Vietnamese).
- `diagnosis`:
  - `primary`: the main / principal diagnosis ("Bệnh chính").
  - `secondary`: a list of comorbidities or secondary diagnoses ("Bệnh kèm theo"). If the document prints no secondary diagnoses, the whole `secondary` value is null. Do not move complications from the narrative into this list.
- `procedures_performed`: procedures, operations and interventions listed as performed during the stay, one entry per item as printed.
- `attending_physician`: the attending or treating physician named on the document (often in the signature block). If the signature block is blank and no physician is named, null. Never use the name of a nurse, technician or another staff member.
- `discharge_instructions`: the advice given to the patient on discharge (activity, diet, warning signs), as printed. Do not include the medication list or the follow-up appointment unless they are printed inside the instructions section.
