---
plan: 01-02
phase: 01-fmas-site-auto-detection
status: complete
completed: 2026-04-16
commits:
  - af7cf82
  - eb3ef48
key-files:
  modified:
    - bookout.py
    - server.py
    - static/index.html
    - utils/telegram_bot.py
    - tests/test_cli.py
requirements-covered:
  - DETECT-03
  - DETECT-04
  - DETECT-05
---

## Summary

Wired `is_fmas_site()` into all three interfaces, replacing the manual "FMAS or Direct ATEC?" selection prompt across the entire codebase.

## What Was Built

**CLI (`bookout.py`):**
- Removed `_ask_site_type()` function entirely
- `cmd_bookout()` and `cmd_add_photos()` now call `is_fmas_site(details["site_name"])` after ticket extraction
- Prints "FMAS (auto-detected)" or "Direct ATEC (auto-detected)" instead of prompting

**Web app (`server.py` + `static/index.html`):**
- `/api/extract-ticket` now includes `is_fmas` boolean in response
- New `/api/check-site-type?site_name=...` endpoint for add-photos flow
- Frontend hides manual FMAS/ATEC toggle; shows auto-detected label instead
- Add-photos flow calls `/api/check-site-type` before folder browse

**Telegram bot (`utils/telegram_bot.py`):**
- Replaced `lookup_site_type()` Drive API call with `is_fmas_site()` in bookout flow
- Replaced `lookup_site_type()` in add-photos flow
- Removed `infer_is_fmas()` legacy function
- No user is ever asked "FMAS or Direct ATEC?" in any interface

## Test Results

109/109 tests passing. Updated `tests/test_cli.py`:
- Removed 3 `_ask_site_type` tests (function deleted)
- Added `test_bookout_auto_detects_fmas_site`
- Added `test_bookout_auto_detects_atec_site`
- Updated 5 existing tests to remove `"1"` input and patch `is_fmas_site`

## Deviations

None. Implementation matches plan exactly.

## Self-Check: PASSED
