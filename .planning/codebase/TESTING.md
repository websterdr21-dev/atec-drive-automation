# Testing

Test framework, structure, and patterns for the ATEC Stock Bookout codebase.

---

## Framework

- **pytest** — sole test runner
- Run: `python -m pytest tests/`
- Tests live in `tests/`; legacy `test_*.py` at the repo root are **manual smoke scripts**, not pytest targets

---

## Organisation

```
tests/
├── conftest.py                — FakeDriveService + autouse env override
├── test_drive_folders.py      — Folder dedup, Sites/FMAS invariants, browser logic
├── test_sheets.py             — Serial search, header detection, red-fill update
├── test_photos.py             — Filename conventions, conflict suffix logic
├── test_gmail.py              — Email body formatting (with/without account number, FMAS phrasing)
├── test_extract.py            — Ticket + serial extraction (Anthropic mocked)
└── test_bookout.py            — End-to-end CLI flow (FMAS happy path, swap, abort)
```

One test module per `utils/` module it exercises, plus `test_bookout.py` for the CLI integration.

---

## Offline-by-default

The suite never talks to Google, Anthropic, or Telegram. Two mechanisms enforce this:

### `FakeDriveService` (in `tests/conftest.py`)
- Drop-in fake for the Google Drive v3 service client
- Implements the subset of the v3 query grammar actually used:
  - `name = '...'`, `name contains '...'`
  - `'<parent>' in parents`
  - `mimeType = 'application/vnd.google-apps.folder'` / `!=` negation
  - `trashed = false`
- Supports `files().list()`, `files().create()`, `files().get_media()` (for xlsx download)
- Stores files + folders in in-memory dicts keyed by synthetic ids
- Exposes helpers for test setup (seed the fake with folders, attach xlsx bytes for a given file id)

### Autouse env override fixture
- A single autouse fixture in `conftest.py` overrides `ANTHROPIC_API_KEY`, `SHARED_DRIVE_ID`, and `SERVICE_ACCOUNT_PATH` with dummy values for every test
- Prevents any test from accidentally reading the real `.env` or hitting a live API

---

## Mocking patterns

| External                  | How it's mocked                                                 |
|---------------------------|-----------------------------------------------------------------|
| Google Drive/Sheets/Docs  | `FakeDriveService` injected wherever `service` is a parameter   |
| Anthropic SDK             | `monkeypatch` on `utils.extract.CLIENT` or on `Anthropic(...)`  |
| xlsx file I/O             | openpyxl `Workbook` built in-memory, bytes fed to `FakeDriveService.get_media` |
| CLI input                 | `monkeypatch.setattr('builtins.input', ...)` with a scripted queue |
| File paths for photos     | `tmp_path` pytest fixture                                       |
| Environment variables     | Autouse fixture + `monkeypatch.setenv`                          |

---

## Test patterns

### Workbook factories
Tests build xlsx bytes via `openpyxl.Workbook`, write headers + rows, save to a `BytesIO`, and attach the bytes to a FakeDrive file. This lets sheet-logic tests exercise the real `openpyxl` parsing path without network I/O.

### Input scripting for CLI flow
`test_bookout.py` scripts `input()` responses in order (ticket text, confirmations, Y/N, browser keystrokes) and asserts on printed output + FakeDrive state + sheet mutations.

### Assertions on Drive state, not return values
Tests after `update_stock_row` open the xlsx the fake now holds and verify:
- Correct cell contains the expected `current account` string
- Date column has today's ISO date
- Red fill is applied from col 1 to `max_column` on the target row
- Untouched rows remain unchanged

### Idempotency assertions
Folder tests call `get_unit_folder` / `_find_or_create_folder` twice with the same inputs and assert the returned id is identical and no new folder was created on the second call.

---

## Coverage

### Well-covered
- Folder find-or-create idempotency and `Sites` / `Sites/FMAS` invariants
- Serial search across multiple sheets, case-insensitive + numeric-stored-cell fallback
- Red-fill `update_stock_row` including `ValueError` when serial absent
- Photo filename conventions and conflict suffix (`_02`, `_03`; `03_Installation_NN` counter)
- Email body composition with/without account number, FMAS vs direct wording
- Ticket JSON extraction with markdown-fence stripping
- CLI `bookout` FMAS happy path, swap-mode abort, user-abort at confirmation step
- Env-guard behaviour when required vars are missing

### Gaps (see CONCERNS.md)
- No tests for `server.py` FastAPI endpoints or `PasswordMiddleware`
- No tests for `utils/telegram_bot.py` commands or `StateManager` transitions
- Telegram webhook secret validation untested
- Swap-mode assertions could be tighter — verify `update_stock_row` and `format_bookout_email` are NOT invoked, not just that the flow completes
- Multi-conflict photo suffix chains (`_04`, `_05`) not explicitly tested
- No integration test for `/api/dashboard` red-fill detection logic

---

## Running a subset

```bash
# Single module
python -m pytest tests/test_sheets.py

# Single test
python -m pytest tests/test_bookout.py::test_fmas_happy_path

# Verbose with stdout
python -m pytest tests/ -vv -s
```

---

## What's NOT tested here

- **Live Drive operations** — the root-level `test_connection.py`, `test_serial_photo.py`, `test_sheets.py` are manual scripts that DO hit the real Shared Drive. Never run them against production creds without intention. The project convention is to swap `SHARED_DRIVE_ID` to a TEST drive when running these.
- **Frontend** — no JS test harness; `static/index.html` is exercised manually.
- **Anthropic prompt quality** — extraction tests mock the API response; prompt changes need manual verification against real tickets.
