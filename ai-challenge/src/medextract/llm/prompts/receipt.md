This document is a hospital RECEIPT or INVOICE.

- `hospital_name`: the facility that issued the document (its letterhead), not a department.
- `patient_name`: the patient billed, as printed.
- `date`: the issue date of the receipt or invoice. Ignore due dates, admission dates and printing timestamps unless the issue date is the only date printed.
- `items`: every billable line. For each line:
  - `description`: as printed.
  - `quantity`: the printed quantity. If the line has no quantity column or value, null. Do not assume 1.
  - `unit_price`: the printed unit price, or null if none is printed.
  - `total`: the printed line amount. Do not compute it from quantity x unit price.
- `grand_total`: the final amount paid or payable, as printed (e.g. "Grand total", "Total amount due", "Total paid"). Do not add up the lines yourself, and do not "fix" a total that seems wrong.
- `payment_method`: how it was paid (cash, credit card, transfer...), only if printed. An unpaid invoice usually has no payment method, so null.
