# Trust and configuration polish

Base: `424d1ab`.

## Changes

- Added `DEEPSEEK_ENDPOINT` (HTTPS URL without embedded credentials, query, or fragment) and `DEEPSEEK_MAX_TOKENS` (integer 128–4096) backend settings. Defaults remain the official chat completions endpoint and 2048 tokens. The proposal route passes both settings to the bounded client. API keys remain in the Authorization header and provider errors remain sanitized.
- Saved scheme narrative explicitly labels its evidence as save-time verification. When inputs have changed, the scheme header identifies its displayed scores as current recomputation. Candidate preview wording remains unchanged.
- Documented the variables in `.env.example` and the root README.

## TDD and verification

- Client/settings RED: 14 expected failures from missing `max_tokens` argument and settings/validation fields; 24 existing tests passed.
- Route RED: configured endpoint/token test returned 409 because the route did not pass those arguments.
- Node RED: missing save-time verification label.
- Focused GREEN: `97 passed` for `test_deepseek_client.py` and `test_assistant_proposals.py`; `test_console_assistant.cjs` passed with real DOM stale-view assertions.
- Safe broad suite: `593 passed, 1 skipped, 1 warning` in 125.28 seconds. The warning reports a duplicate archive member in `test_scan46_delivery.py::test_bad_checksum_writes_no_data_or_database`.
- All pytest runs used isolated `TEST_DATABASE_URL`, disabled cache writes, and set `tmp_path_retention_policy=all` / `tmp_path_retention_count=1000`. No provider request or real API key was used.

## Notes

- The broad suite excluded `test_preview_frame_calibration.py` per task instruction.
