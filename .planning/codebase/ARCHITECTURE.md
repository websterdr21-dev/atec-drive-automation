# Architecture

**Analysis Date:** 2026-04-15

## Pattern Overview

**Overall:** Shared-core multi-interface CLI + web service

**Key Characteristics:**
- Three independent entry points (CLI, FastAPI web app, Telegram bot) all delegate to the same `utils/` module layer
- No ORM, no database — Google Drive is the data store; `.xlsx` files are the stock ledger
- Stateless HTTP API with a single shared-password session cookie for auth; Telegram conversation state is in-memory only
- All Google API interactions are synchronous (google-api-python-client); FastAPI endpoints call them on the main thread (no background tasks or thread pool)

## Layers

**Entry Points:**
- Purpose: Accept user input, orchestrate calls to utils, return results
- Locations: `bookout.py` (CLI), `server.py` (FastAPI), `utils/telegram_bot.py` (Telegram)
- Contains: Input handling, flow control, error display, response formatting
- Depends on: All `utils/` modules
- Used by: End users directly

**Utils Core (`utils/`):**
- Purpose: Reusable business logic shared by all three entry points
- Location: `utils/`
- Contains:
  - `auth.py` — service account credential + Google API client factories
  - `env.py` — `.env` loader
  - `drive_folders.py` — folder navigation and idempotent find-or-create
  - `sheets.py` — stock sheet discovery, serial search, row update + red fill
  - `photos.py` — photo naming, conflict-safe upload
  - `extract.py` — Anthropic Claude ticket and serial label extraction
  - `gmail.py` — email body formatter (no sending)
  - `telegram_bot.py` — python-telegram-bot Application and command handlers
  - `telegram_state.py` — in-memory StateManager and persistent SiteStructureStore
- Depends on: Google APIs, Anthropic API, openpyxl, python-telegram-bot
- Used by: `bookout.py`, `server.py`, `utils/telegram_bot.py`

**Static Frontend:**
- Purpose: Single-page web UI served by FastAPI
- Location: `static/index.html`
- Contains: Dashboard, bookout form, add-photos form, check-stock form; drag-and-drop photo upload
- Depends on: FastAPI API endpoints
- Used by: Web browser clients

## Data Flow

**Bookout (happy path — non-swap):**

1. User submits ticket text → `extract_client_details()` calls Claude (`utils/extract.py`) → returns structured dict
2. User supplies serial label photo → `extract_serial_from_photo()` calls Claude vision → returns `{serial_number, item_code}`
3. `find_serial_number()` downloads each `.xlsx` from Drive, searches with openpyxl, returns row metadata (`utils/sheets.py`)
4. `update_stock_row()` re-downloads the matching file, writes `current_account` + today's date, applies red fill to the row, re-uploads (`utils/sheets.py`)
5. `get_unit_folder()` / `get_atec_site_folder()` resolve or create the destination folder via Drive API (`utils/drive_folders.py`)
6. `upload_bookout_photos()` uploads `01_Serial_Number.jpg` (and optionally `04_Device_Photo.jpg`) to the folder; numeric suffix appended on name collision (`utils/photos.py`)
7. `format_bookout_email()` returns the formatted accounts email string for copy-paste (`utils/gmail.py`)

**Bookout (swap mode — serial not found):**
- Steps 4 (sheet update) and 7 (email) are skipped
- Steps 5 and 6 (folder + photo upload) still execute
- Detected automatically in `server.py` (`is_swap = result is None`); requires explicit confirmation in the CLI

**Stock sheet read/write pattern:**
- Download `.xlsx` via `MediaIoBaseDownload` → openpyxl in-memory → modify → re-upload via `MediaIoBaseUpload`
- No Google Sheets API calls for data; all manipulation is openpyxl on the binary file

**State Management (Telegram):**
- `utils/telegram_state.StateManager` — dict keyed by `chat_id`; each value is a state dict with a `step` field driving the conversation FSM
- State expires after 30 minutes of inactivity
- `utils/telegram_state.SiteStructureStore` — JSON file at `data/atec_site_structures.json`; persists learned folder path templates for direct ATEC sites across bot restarts

## Key Abstractions

**`FakeDriveService` (tests only):**
- Purpose: In-memory stand-in for the Drive v3 API client; interprets a subset of Drive query syntax
- Location: `tests/conftest.py`
- Pattern: Tracks `records` dict, `create_calls` list, `update_calls` list; `_match_query()` parses `mimeType`, `name`, `name contains`, `in parents`, `trashed` clauses

**`StateManager`:**
- Purpose: Per-chat conversation state for the Telegram bot
- Location: `utils/telegram_state.py`
- Pattern: `get(chat_id)` returns `None` on miss or expiry; `set()` stamps `last_activity`; cleared on `/cancel` or completion

**`SiteStructureStore`:**
- Purpose: Persist folder path templates for direct ATEC sites so repeat bookouts don't require re-navigation
- Location: `utils/telegram_state.py`
- Pattern: `learn()` records segments with `{unit}` token substituted for the unit number; `resolve_template()` substitutes it back at runtime; saves atomically via `.tmp` rename

## Entry Points

**CLI (`bookout.py`):**
- Location: `bookout.py`
- Triggers: `python bookout.py bookout | add-photos | check-stock`
- Responsibilities: Interactive prompts, FMAS/ATEC site-type selection, inline field correction, folder browser (`_browse_to_folder`), swap confirmation

**FastAPI App (`server.py`):**
- Location: `server.py`
- Triggers: `uvicorn server:app --reload --port 8000`
- Responsibilities: HTTP API for all three workflows, `PasswordMiddleware` auth, Telegram bot lifecycle (`asynccontextmanager lifespan`), global JSON exception handler, SPA serving

**Telegram Bot (`utils/telegram_bot.py`):**
- Location: `utils/telegram_bot.py`
- Triggers: Built and started by `server.py` lifespan if `TELEGRAM_BOT_TOKEN` is set; receives updates via webhook (`POST /telegram/webhook`) or polling
- Responsibilities: Command handling (`/bookout`, `/addphotos`, `/checkstock`, `/cancel`, `/start`), FSM-driven multi-step conversation, photo buffering

## Error Handling

**Strategy:** Fail fast with explicit errors; no silent fallbacks

**Patterns:**
- `FileNotFoundError` raised by `drive_folders.py` and `sheets.py` when required Drive folders (`Sites`, `Sites/FMAS`, `Stock Sheets`) are absent — callers propagate or catch
- `ValueError` raised by `update_stock_row()` if serial not found — no partial writes occur
- FastAPI global exception handler at `server.py:_global_exc` catches all unhandled exceptions and returns `{"detail": ..., "trace": ...}` JSON with status 500
- CLI prints `[ERROR]` prefix messages and calls `sys.exit(1)` on env misconfiguration

## Cross-Cutting Concerns

**Logging:** stdlib `logging`; logger named per module (`logging.getLogger(__name__)`); used in `server.py` and `utils/telegram_state.py`

**Validation:** Minimal — form fields validated for presence via FastAPI `Form(...)` required fields; serial match logic handles case-insensitive string and numeric comparisons in `sheets.py`

**Authentication:** Single shared password cookie for web app; service account JSON for all Google API calls; Telegram webhook secret header for bot ingress

**Idempotency:** All folder creation operations are idempotent (`_find_or_create_folder` always checks before creating); photo uploads never overwrite (suffix appended on conflict); stock sheet writes are not idempotent — running twice will apply red fill and update dates again

---

*Architecture analysis: 2026-04-15*
