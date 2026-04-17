# Codebase Structure

**Analysis Date:** 2026-04-15

## Directory Layout

```
Drive automation/
├── bookout.py              # CLI entry point (bookout / add-photos / check-stock)
├── server.py               # FastAPI app + Telegram bot lifecycle
├── requirements.txt        # pip dependencies (no lockfile)
├── service_account.json    # Google service account creds (local only; not committed)
├── server.log              # uvicorn output log
├── static/
│   └── index.html          # Single-file SPA (dashboard, bookout, add-photos, check-stock)
├── utils/
│   ├── __init__.py
│   ├── auth.py             # Service-account auth; builds Drive/Sheets/Docs/Gmail clients
│   ├── env.py              # Central .env loader
│   ├── drive_folders.py    # Folder navigation + idempotent find-or-create
│   ├── sheets.py           # .xlsx download/search/update, red-fill on bookout
│   ├── photos.py           # Photo naming convention + conflict-safe upload
│   ├── extract.py          # Claude-powered ticket + serial-label extraction
│   ├── gmail.py            # Formats accounts email as copy-ready text (no sending)
│   ├── telegram_bot.py     # Telegram bot Application and command handlers
│   └── telegram_state.py   # In-memory StateManager + persistent SiteStructureStore
├── tests/
│   ├── __init__.py
│   ├── conftest.py         # FakeDriveService, shared fixtures, env guards
│   ├── test_cli.py         # CLI flow tests (bookout.py commands)
│   ├── test_drive.py       # Drive folder logic (dedup, Sites/FMAS invariants)
│   ├── test_email.py       # Email composition
│   ├── test_photos.py      # Photo naming and suffix logic
│   ├── test_stock.py       # Sheet search + red-fill update
│   └── test_ticket_parser.py  # Ticket and serial extraction (mocked Anthropic)
├── automations/
│   └── __init__.py         # Empty — reserved for future automation scripts
├── data/                   # Created at runtime by SiteStructureStore (not committed)
│   └── atec_site_structures.json  # Learned ATEC folder path templates
└── test_connection.py      # Manual smoke test (real Drive) — not in pytest suite
    test_serial_photo.py    # Manual smoke test (real photo extraction)
    test_sheets.py          # Manual smoke test (real sheets)
```

## Directory Purposes

**`utils/`:**
- Purpose: All reusable business logic shared across CLI, web app, and Telegram bot
- Contains: One module per concern — auth, env, Drive folders, sheets, photos, extraction, email, Telegram bot + state
- Key files: `utils/auth.py`, `utils/sheets.py`, `utils/drive_folders.py`, `utils/photos.py`, `utils/extract.py`

**`tests/`:**
- Purpose: Offline pytest suite — no live Google or Anthropic calls
- Contains: Per-concern test files + `conftest.py` with `FakeDriveService` and shared fixtures
- Key files: `tests/conftest.py` (read first when adding tests)

**`static/`:**
- Purpose: Frontend assets served by FastAPI for all non-API routes
- Contains: Single file `static/index.html` — the entire SPA
- Generated: No — hand-authored single file
- Committed: Yes

**`automations/`:**
- Purpose: Reserved — currently empty (`__init__.py` only)
- Generated: No

**`data/`:**
- Purpose: Runtime persistence for `SiteStructureStore` (learned ATEC folder paths)
- Created by: `utils/telegram_state.SiteStructureStore._save()` on first write
- Committed: No (created at runtime)

## Key File Locations

**Entry Points:**
- `bookout.py` — CLI; run as `python bookout.py bookout|add-photos|check-stock`
- `server.py` — FastAPI app; run as `uvicorn server:app --reload --port 8000`
- `utils/telegram_bot.py` — Telegram bot; instantiated by `server.py` lifespan, not run standalone

**Configuration:**
- `.env` — all environment variables (not committed)
- `service_account.json` — Google service account credentials (not committed)
- `utils/env.py` — single place to call `load_dotenv`; always import this instead of calling `load_dotenv` directly

**Core Business Logic:**
- `utils/sheets.py` — stock sheet search (`find_serial_number`) and update (`update_stock_row`)
- `utils/drive_folders.py` — folder resolution (`get_unit_folder`, `get_atec_site_folder`, `_find_or_create_folder`)
- `utils/photos.py` — upload functions (`upload_bookout_photos`, `upload_post_install_photos`)
- `utils/extract.py` — Claude calls (`extract_client_details`, `extract_serial_from_photo`)

**Testing Infrastructure:**
- `tests/conftest.py` — `FakeDriveService`, `seeded_drive` fixture, `mock_anthropic` fixture, `_safe_env` autouse guard

## Naming Conventions

**Files:**
- `snake_case.py` for all Python modules
- `test_<concern>.py` for test files in `tests/`
- `index.html` for the single frontend file

**Directories:**
- lowercase, short names (`utils/`, `tests/`, `static/`, `data/`)

**Functions:**
- `snake_case` throughout
- Private helpers prefixed with `_` (e.g., `_find_or_create_folder`, `_download_xlsx`, `_next_index`)
- Public API functions have no prefix (e.g., `find_serial_number`, `upload_bookout_photos`)

## Where to Add New Code

**New util / shared business logic:**
- Add a new module at `utils/<concern>.py`
- Call `utils/env.py:load()` is already handled by entry points — do not call it inside utils modules

**New CLI command:**
- Add a `def cmd_<name>()` function in `bookout.py` and dispatch it in the `if __name__ == "__main__"` block

**New API endpoint:**
- Add the route function in `server.py`; import utils lazily inside the function body (matching existing pattern)
- Whitelist from `PasswordMiddleware` if needed (add path to the `if request.url.path in (...)` check)

**New test:**
- Add `tests/test_<concern>.py`
- Use `seeded_drive` fixture for tests needing the standard Drive tree
- Use `mock_anthropic` fixture for tests touching `utils/extract.py`
- The `_safe_env` autouse fixture runs automatically — no need to set env vars manually

**New photo type:**
- Add entry to `PHOTO_TYPES` dict in `utils/photos.py`
- Implement naming/conflict logic following the `_next_index` pattern

**Frontend changes:**
- Edit `static/index.html` directly — there is no build step

## Special Directories

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes
- Committed: No

**`data/`:**
- Purpose: Runtime JSON persistence (`atec_site_structures.json`)
- Generated: Yes (created on first Telegram bot navigation)
- Committed: No

---

*Structure analysis: 2026-04-15*
