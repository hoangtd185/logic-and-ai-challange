# Batch results (gemini-3.6-flash, gemini-3.6-flash# GEMINI_THINKING_LEVEL=low, gemini-3.8-flash)

Model time is the real API time; for cached calls it is the time of the call that filled the cache.

| Document | Status | Type | Confidence | Null fields | Check | Lowered | Classify | Extract | Re-read | Tokens in / out / thinking | Validation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| discharge_01.pdf | ok | discharge_summary | 1.00 | 0/9 | text layer | 0 | cached | cached | - | - | - |
| discharge_02.pdf | ok | discharge_summary | 1.00 | 1/9 | text layer | 1 | cached | cached | - | - | INVALID_DATE |
| discharge_03.pdf | ok | discharge_summary | 0.99 | 1/9 | text layer | 0 | cached | cached | - | - | - |
| electricity_bill_01.pdf | **failed** | - | - | - | - | - | - | - | - | - | error: Gemini call failed after 1 attempt(s): Error code: 404 - {'error': {'message': "Model 'gemini-3.6-flash# GEMINI_THINKING_LEVEL=low' not found. Did you mean 'gemini-3.1-flash-image-preview'? Please verify the model name against the supported list: https://ai.google.dev/gemini-api/docs/models", 'code' |
| lab_01.pdf | ok | lab_report | 1.00 | 0/43 | text layer | 0 | cached | cached | - | - | - |
| lab_02.png | **failed** | - | - | - | - | - | - | - | - | - | error: Gemini call failed after 1 attempt(s): Error code: 400 - {'error': {'message': 'Thinking level THINKING_LEVEL_MINIMAL is not supported for this model. Please retry with other thinking level.', 'code': 'invalid_request'}} |
| prescription_01.pdf | ok | prescription | 0.99 | 0/18 | text layer | 0 | 17.7s | 11.2s | - | 3,399 / 700 / 0 | - |
| prescription_02.png | ok | prescription | 0.99 | 2/18 | 2 readings | 0 | 47.1s | 31.9s | 89.6s | 5,319 / 1,261 / 0 | - |
| receipt_01.pdf | ok | receipt | 1.00 | 0/21 | text layer | 0 | 22.2s | 95.7s | - | 3,456 / 804 / 0 | - |
| receipt_02.pdf | **failed** | - | - | - | - | - | - | - | - | - | network: Gemini call failed after 5 attempt(s): no reply within 120 s (upload + server time, including the SDK's own resend) |
| receipt_03.png | **failed** | - | - | - | - | - | - | - | - | - | error: Gemini call failed after 1 attempt(s): Error code: 404 - {'error': {'message': "Model 'gemini-3.6-flash# GEMINI_THINKING_LEVEL=low' not found. Did you mean 'gemini-3.1-flash-tts-preview'? Please verify the model name against the supported list: https://ai.google.dev/gemini-api/docs/models", 'code':  |
