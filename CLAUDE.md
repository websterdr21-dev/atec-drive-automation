## Context Navigation

When understanding the codebase, docs, or any files in this project:
1. ALWAYS query the knowledge graph first: `/graphify query "Your question"`
2. Only read raw files if explicitly told "read the file" or "look at the raw file"
3. Use `graphify-out/GRAPH_REPORT.md` as navigation entrypoint

## Graphify Auto-Update

- After creating or editing any file: run `python -m graphify update .` to add it to the graph (code files only — free, no LLM)
- After completing a task with doc/config changes: run `/graphify . --update` to re-extract semantic content

---

# ATEC Stock Bookout Automation

Python tooling that automates the stock bookout + install-photo workflow for ATEC, a fiber network company. All Drive content lives in the `Atec Cape Town` Shared Drive, accessed via a service account.

Three interfaces exist on top of a shared `utils/` core:

1. **`bookout.py` — CLI** (interactive, run on a technician's machine)
2. **`server.py` — FastAPI web app** (hosted, mobile-friendly UI + Telegram webhook)
3. **Telegram bot** (`utils/telegram_bot.py`, wired into the FastAPI lifespan)

All three reuse the same functions in `utils/`.

---

## Project layout

```
bookout.py                — CLI entry point (bookout / add-photos / check-stock)
server.py                 — FastAPI app + Telegram webhook lifecycle
static/index.html         — single-page frontend served by FastAPI
utils/
  auth.py                 — service-account auth, builds Drive/Sheets/Docs/Gmail services
  env.py                  — central .env loader
  drive_folders.py        — folder nav + find-or-create (never duplicates)
  sheets.py               — xlsx download/search/update, red-fill on bookout
  photos.py               — photo naming convention + upload (auto-suffixes on conflict)
  extract.py              — Claude-powered ticket + serial-label extraction
  gmail.py                — formats the accounts email as copy-ready text (no sending)
  telegram_bot.py         — Telegram conversational bot
  telegram_state.py       — in-memory per-chat state machine for the bot
tests/                    — pytest suite (FakeDriveService, mocked Anthropic)
service_account.json      — service-account credentials (local only; not committed)
.env                      — SERVICE_ACCOUNT_PATH, SHARED_DRIVE_ID, ANTHROPIC_API_KEY, APP_PASSWORD, TELEGRAM_*
requirements.txt
```

---

## Environment

`.env` (loaded by `utils/env.py`):

| Variable                   | Purpose                                             |
|----------------------------|-----------------------------------------------------|
| `SERVICE_ACCOUNT_PATH`     | Path to `service_account.json`                      |
| `SHARED_DRIVE_ID`          | Shared Drive ID for `Atec Cape Town`                |
| `ANTHROPIC_API_KEY`        | Claude API key — used by `utils/extract.py`         |
| `APP_PASSWORD`             | Shared password for the FastAPI app                 |
| `TELEGRAM_BOT_TOKEN`       | Optional; enables the Telegram bot                  |
| `TELEGRAM_WEBHOOK_SECRET`  | Shared secret the Telegram webhook validates        |
| `TELEGRAM_USE_POLLING`     | `true` in local dev (no public URL needed)          |
| `RAILWAY_PUBLIC_DOMAIN`    | Production host — triggers webhook registration     |

Scopes requested by the service account (`utils/auth.py`): Drive, Sheets, Docs, Gmail compose (Gmail compose is scoped in anticipation of future drafting; the current code does not send mail — see *Email* below).

---

## Shared Drive structure

```
Atec Cape Town (Shared Drive root)
├── Stock Sheets/
│   └── [active folder — name contains "Currently in use"]/
│       ├── Serial Number Listing CPT FMAS.xlsx
│       ├── Serial Number Listing CPT.xlsx
│       ├── Serial Number Listing_ FMAS Digital Trio.xlsx
│       ├── Serial Number Listing_ PLAT.xlsx
│       └── Serial Number Listing_ VER.xlsx
└── Sites/
    ├── FMAS/
    │   └── [Site Name]/
    │       └── Unit [Unit Number]/
    │           └── photos...
    └── [Direct ATEC Site Name]/
        └── ... (arbitrary sub-structure — user browses to destination)
```

`Sites` and `Sites/FMAS` always exist and are never (re)created by the code; if either is missing the relevant call raises `FileNotFoundError`.

---

## Stock sheet logic (`utils/sheets.py`)

- Active folder is discovered dynamically: the single subfolder of `Stock Sheets` whose name contains `Currently in use`. `Inventory Levels` files are excluded by the name filter on `Serial Number Listing`.
- `find_serial_number(service, drive_id, serial)` downloads every matching `.xlsx`, locates the header row (first row where column A == `Serial Number`), then scans data rows.
  - Matches are **case-insensitive** on string comparison.
  - If the serial is all digits, numeric-stored cells (`int`/`float`) are also compared via `int()` — this handles spreadsheets where a serial like `0200254233608` is stored as a number.
  - Returns `{file_id, file_name, sheet_name, row_index, row_values, headers}` or `None`.
- `update_stock_row(service, drive_id, serial, current_account)`
  - Writes `current_account` into the column whose header contains `current account`.
  - Writes today's date (ISO `YYYY-MM-DD`) into the column containing `date last move`.
  - Applies solid red fill (`FF0000`) to every cell in that row, from column 1 to `ws.max_column`.
  - Raises `ValueError` if the serial is not found — no writes occur in that case.

### Swap mode

If the scanned serial is **not** found in any sheet, the workflow treats the unit as a replacement/swap:
- Stock sheet is **not** updated.
- Accounts email is **not** generated.
- Drive folder and photo upload still proceed.

The CLI prompts the user to confirm swap mode before proceeding; the FastAPI endpoint infers it automatically and reports `is_swap: true` in the response.

---

## Drive folders (`utils/drive_folders.py`)

Two paths depending on site type, chosen interactively in the CLI (`1. FMAS / 2. Direct ATEC`) or via an `is_fmas` flag in the API.

**FMAS — automated**

`get_unit_folder(..., is_fmas=True)` resolves `Sites → FMAS → [site] → Unit [unit]`, creating the site and unit folders if absent. The unit folder is always named `Unit X` — any pre-existing `Unit ` prefix on the input is stripped first so you never end up with `Unit Unit 42`. Returns `(folder_id, folder_url, site_created, unit_created)`.

**Direct ATEC — interactive**

`get_atec_site_folder(...)` creates (or opens) `Sites/[site_name]` only. The technician then uses the folder browser to navigate into the correct sub-path:
- CLI: `_browse_to_folder()` in `bookout.py` — numeric subfolder navigation with `u` to upload here and `b` to go back.
- Web / Telegram: `/api/browse` and `/api/site-folder` endpoints drive a clickable browser; the UI submits a `target_folder_id` when the user confirms destination.

**No duplicates.** `_find_or_create_folder` always performs a name-exact + parent-scoped lookup before creating anything. Repeat calls are idempotent.

---

## Ticket + serial extraction (`utils/extract.py`)

Uses the Anthropic SDK, model `claude-opus-4-6`.

- `extract_client_details(ticket_text)` → JSON dict with keys `full_name`, `phone`, `site_name`, `unit_number`, `address`, `isp`, `speed`, `account_number`. Markdown code fences from the model output are stripped before `json.loads`.
- `extract_serial_from_photo(image_path)` → `{serial_number, item_code}`. Sends the image as base64 (JPEG or PNG media type inferred from extension) plus a strict prompt that demands JSON only.

A module-level `CLIENT` caches the Anthropic client on first use.

---

## Photo upload (`utils/photos.py`)

Filenames, in order:

| Type                   | Filename                        | Stage         |
|------------------------|---------------------------------|---------------|
| Serial number label    | `01_Serial_Number.jpg`          | Bookout       |
| ONT / router placement | `02_ONT_Router_Placement.jpg`   | Post-install  |
| Installation photos    | `03_Installation_01.jpg`, `_02`, `_03` … | Post-install |
| Device photo           | `04_Device_Photo.jpg`           | Bookout (optional) |
| Speed test             | `05_Speed_Test.jpg`             | Post-install  |

**Conflict policy (deviation from original spec).** If a file with the exact expected name already exists in the target folder, the uploader appends a numeric suffix (`01_Serial_Number_02.jpg`, `_03`, …) instead of overwriting. Installation photos independently track the next free `03_Installation_NN` index. This means re-running a bookout against an existing unit folder adds new files rather than destroying history.

---

## Email (`utils/gmail.py`)

`format_bookout_email(details)` returns the ready-to-copy email body as a string. `print_bookout_email(details)` prints it bracketed by dividers. **Nothing is sent** — the user copies the body into Gmail, where the account's own signature is appended. (The Gmail API scope is reserved for a future drafting feature but is not currently invoked.)

Current format:

```
To: accounts@atec.co.za
Subject: Book out Request | [Unit Number] [Site Name]

Good day,

Please book out the following item for the FMAS client below.    ← or "for the client below." if not FMAS

Item: [Item Code]
Serial Number: [Serial Number]
Date: [YYYY-MM-DD]

Client Details:
Name: [Full Name]
Contact: [Phone]
Site: [Site Name]
Unit: [Unit Number]
Address: [Full Address]
ISP: [ISP]
Speed: [Speed]
Account: [Account Number]      ← omitted entirely if not provided
```

`Current Account` written into the stock sheet is `"{unit_number} {site_name}"` (not the full street address, despite what older drafts of this doc said).

---

## CLI (`bookout.py`)

```bash
python bookout.py bookout       # full flow
python bookout.py add-photos    # post-install photos only
python bookout.py check-stock   # serial number lookup across all sheets
```

### `bookout` — step-by-step

1. Paste ticket text, end with a blank line + Enter.
2. Claude extracts client details; user confirms or edits each field inline.
3. Choose site type: FMAS or direct ATEC.
4. Provide path to the device-label photo → Claude vision reads serial + item code → user confirms or corrects.
5. Stock sheet search. If found, normal flow. If not found, prompt to proceed as **swap** (sheet + email skipped).
6. For non-swaps, confirm and run `update_stock_row`.
7. Drive folder:
   - FMAS → auto-create via `get_unit_folder`.
   - ATEC → `get_atec_site_folder` + interactive browser to pick destination.
8. Upload `01_Serial_Number.jpg` (from the label photo already provided) and, optionally, `04_Device_Photo.jpg`.
9. Print the accounts email for copy-paste (skipped in swap mode).

### `add-photos`

Site name + unit number → FMAS or ATEC selector → folder resolved (or browsed) → upload any subset of ONT / installation (looped until blank) / speed test photos. Existing files are suffixed, never overwritten.

### `check-stock`

Prompts for a serial, searches every `Serial Number Listing` sheet in the active folder, prints the file name, sheet tab, row index, and every non-empty column with its header.

---

## FastAPI web app (`server.py`)

Run locally:

```bash
uvicorn server:app --reload --port 8000
```

Auth: `PasswordMiddleware` checks an `atec_auth` cookie against `APP_PASSWORD`. `/health` and `/telegram/webhook` are whitelisted. `/api/login` sets the cookie, `/api/logout` clears it.

### API surface

| Method | Path                   | Purpose                                                       |
|--------|------------------------|---------------------------------------------------------------|
| GET    | `/health`              | Liveness                                                      |
| POST   | `/api/login`           | Set session cookie                                            |
| POST   | `/api/logout`          | Clear cookie                                                  |
| GET    | `/api/dashboard`       | List recent bookouts across all active sheets; flags red-fill rows as `booked` |
| POST   | `/api/extract-ticket`  | Claude ticket → client details                                |
| POST   | `/api/extract-serial`  | Claude vision photo → serial + item code                      |
| GET    | `/api/check-stock`     | Serial lookup                                                 |
| GET    | `/api/browse`          | List subfolders of a given folder id (drives the web browser) |
| GET    | `/api/site-folder`     | Find-or-create `Sites/[name]` for a direct ATEC site          |
| POST   | `/api/create-folder`   | Generic find-or-create under a parent                         |
| POST   | `/api/bookout`         | Full bookout (multipart: client fields + serial/device photos + `is_fmas` + `target_folder_id` for ATEC) |
| POST   | `/api/add-photos`      | Post-install photo upload (ONT / installs / speed)            |
| POST   | `/telegram/webhook`    | Telegram `Update` ingress, validated via `X-Telegram-Bot-Api-Secret-Token` |
| GET    | `/{full_path:path}`    | Serves `static/index.html` for all non-API routes (SPA)       |

Global exception handler serialises any uncaught error as JSON (including the last 1000 chars of the traceback), so the frontend never has to parse HTML error pages.

### Frontend

Single-file SPA at `static/index.html`. Pages: dashboard, bookout, add-photos, check-stock. Drag-and-drop photo upload with per-photo labelling.

---

## Telegram bot

When `TELEGRAM_BOT_TOKEN` is set, the FastAPI lifespan builds a `python-telegram-bot` `Application` and either:
- Registers a webhook against `RAILWAY_PUBLIC_DOMAIN` (production), or
- Starts long-polling inside the FastAPI event loop when `TELEGRAM_USE_POLLING=true` (local dev).

Commands: `/bookout`, `/addphotos`, `/checkstock`, `/cancel`, `/start`. Conversational state is kept in-memory by `utils/telegram_state.StateManager` — each chat id maps to a small state dict. This is **not persistent across restarts** (single-instance deploys only).

---

## Testing

```bash
python -m pytest tests/
```

- Fully offline: `tests/conftest.py` provides a `FakeDriveService` that interprets the subset of Drive v3 query syntax this codebase uses. No real Google/Anthropic calls.
- `ANTHROPIC_API_KEY`, `SHARED_DRIVE_ID`, and `SERVICE_ACCOUNT_PATH` are overridden with dummy values by an autouse fixture — impossible to hit a live API from the suite.
- Covers folder logic (dedup, Sites/FMAS invariants), sheet search + red-fill update, photo naming/suffix, email composition, ticket/serial extraction, and CLI flow (FMAS happy path, swap mode, abort, env guards).

Legacy root-level scripts (`test_connection.py`, `test_serial_photo.py`, `test_sheets.py`) are manual smoke-test scripts for the real Drive, not part of the pytest suite.

---

## Safety invariants

- Never write to the live Shared Drive during testing — use the TEST Drive (swap the `SHARED_DRIVE_ID` env var).
- `Sites` and `Sites/FMAS` are never auto-created.
- Duplicate folder names are never created under the same parent.
- Photos never overwrite existing files — a numeric suffix is appended on conflict.
- Stock sheet writes are aborted atomically if the serial cannot be found.
- The accounts email is composed, not sent — a human is always in the loop.

<!-- GSD:project-start source:PROJECT.md -->
## Project

**ATEC Stock Bookout Automation**

Python tooling that automates the stock bookout + install-photo workflow for ATEC, a fiber network company. Technicians submit a ticket, the system extracts client details and serial numbers via Claude AI, updates the stock spreadsheet in Google Drive, creates the right folder structure, and generates the accounts email — all without touching Drive manually.

Three interfaces share a common `utils/` core: a CLI (`bookout.py`), a FastAPI web app (`server.py`), and a Telegram bot (`utils/telegram_bot.py`).

**Core Value:** A technician should be able to complete a full bookout — from ticket paste to email copy — in under two minutes, on any device, without knowing the Google Drive folder structure.

### Constraints

- **Tech stack**: Python, Google Drive API (service account), openpyxl, python-telegram-bot, FastAPI — no new runtime dependencies unless clearly justified
- **Drive structure**: `Sites` and `Sites/FMAS` are never auto-created by code; `FileNotFoundError` raised if absent — must remain
- **Backward compatibility**: All three interfaces (CLI, web, Telegram) must work after changes; no interface-specific-only fixes
- **Offline tests**: pytest suite must remain fully offline — `FakeDriveService` and mocked Anthropic; any new cache/store code needs test coverage
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.14 — All application code (CLI, FastAPI backend, Telegram bot, utils)
- HTML/CSS/JavaScript — Single-file SPA at `static/index.html` (no build step)
## Runtime
- Python 3.14 (local dev: Windows; production: Railway)
- pip
- Lockfile: Not present — `requirements.txt` specifies minimum versions only
## Frameworks
- FastAPI >=0.115.0 — HTTP API server and static file serving (`server.py`)
- Starlette (via FastAPI) — Middleware (`PasswordMiddleware`), response types
- No third-party CLI framework — plain `sys.argv` dispatch in `bookout.py`
- python-telegram-bot >=20.0 — Async bot framework; supports webhook and polling modes
- uvicorn >=0.30.0 — ASGI server for FastAPI (`uvicorn server:app --reload --port 8000`)
## Key Dependencies
- `google-api-python-client >=2.100.0` — Drive v3, Sheets v4, Docs v1, Gmail v1 API clients
- `google-auth >=2.23.0` — Service account credential loading (`utils/auth.py`)
- `google-auth-httplib2 >=0.1.1` — HTTP transport for google-auth
- `anthropic >=0.34.0` — Claude API client used in `utils/extract.py` (model: `claude-opus-4-6`)
- `openpyxl >=3.1.0` — Downloads, parses, modifies, and re-uploads `.xlsx` stock sheets (`utils/sheets.py`)
- `python-dotenv >=1.0.0` — `.env` loading via `utils/env.py`
- `python-multipart >=0.0.9` — Required by FastAPI for `multipart/form-data` (photo uploads)
## Configuration
- All configuration sourced from `.env` at project root
- Loaded once via `utils/env.py:load()` at the top of each entry point (`bookout.py`, `server.py`)
- Required vars: `SERVICE_ACCOUNT_PATH`, `SHARED_DRIVE_ID`, `ANTHROPIC_API_KEY`, `APP_PASSWORD`
- Optional vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_USE_POLLING`, `RAILWAY_PUBLIC_DOMAIN`
- No build step — Python runs directly
- No `pyproject.toml` or `setup.py` — `requirements.txt` only
- No Docker configuration present
## Testing
- pytest — test suite in `tests/` directory
- stdlib `unittest.mock` — mocking Anthropic and Drive API calls
- Custom `FakeDriveService` in `tests/conftest.py` — in-memory Drive v3 stand-in
## Platform Requirements
- Python 3.14
- `service_account.json` present at path referenced by `SERVICE_ACCOUNT_PATH` (not committed)
- `.env` file present with required vars
- Set `TELEGRAM_USE_POLLING=true` for local Telegram bot dev (no public URL needed)
- Hosted on Railway
- Single-instance deployment (Telegram conversation state is in-memory only — not Redis-backed)
- `RAILWAY_PUBLIC_DOMAIN` triggers automatic Telegram webhook registration on startup
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Lowercase with underscores for modules: `sheets.py`, `drive_folders.py`, `telegram_bot.py`
- Entry points are root-level descriptive names: `bookout.py`, `server.py`
- Test files follow pytest convention: `test_stock.py`, `test_drive.py`, `test_photos.py`
- Lowercase with underscores: `get_unit_folder()`, `find_serial_number()`, `upload_bookout_photos()`
- Private helpers prefixed with underscore: `_download_xlsx()`, `_upload_xlsx()`, `_find_folder_exact()`, `_match_query()`
- CLI command functions: `cmd_bookout()`, `cmd_add_photos()`, `cmd_check_stock()` in `bookout.py`
- Lowercase with underscores for all variable and constant names
- Constants in UPPERCASE where appropriate: `FOLDER_MIME = "application/vnd.google-apps.folder"`, `RED_FILL`, `PHOTO_TYPES`, `SCOPES`
- Module-level singletons capitalized: `STATE`, `SITES`, `CLIENT` (anthropic client cache)
- PascalCase for classes: `FakeDriveService`, `_FakeFilesAPI`, `_Inputs`, `_Req`, `StateManager`, `SiteStructureStore`, `PasswordMiddleware`
## Code Style
- No formatter explicitly configured (no `.prettierrc`, `setup.cfg`, or `pyproject.toml`)
- Code follows PEP 8 style conventions manually
- Line length appears unconstrained in most files
- Consistent spacing: functions separated by blank lines, internal logic grouped by comment headers
- No linting configuration detected in repo
- Code does not import from `__future__` unless needed (e.g., `from __future__ import annotations` in modules with forward references)
- Type hints used selectively (function arguments and returns where clarity needed)
- Triple-quote docstrings at module level explaining purpose and layout: `"""Central .env loader — always use this instead of bare load_dotenv()."""`
- Module-level comment headers in sections: `# ---------------------------------------------------------------------------` demarcation with section title
- Inline comments explain "why" not "what": e.g., `# Numeric comparison: handle int/float stored values`
- No unnecessary comments on obvious code
- Parameters use snake_case
- Return values are documented in docstrings (key dict structures described inline)
- Functions are focused on a single operation (avoid god functions)
- Error handling raises explicit exceptions with context: `ValueError(f"Serial number '{serial_number}' not found...")`
- Each module in `utils/` is single-purpose: `sheets.py` for Sheet operations, `drive_folders.py` for folder navigation, `photos.py` for photo upload logic
- Barrel files: None used; imports are explicit
- Internal helpers prefixed with `_` signal they are not intended for external use
- Helper functions (`_find_folder`, `_download_xlsx`, `_next_index`) are grouped at module level, not scattered
## Import Organization
- No path aliases configured
- Imports always use relative module paths: `from utils.env import load`, `from utils.sheets import find_serial_number`
## Error Handling
- Explicit exceptions raised with descriptive messages: `ValueError`, `FileNotFoundError`
- Google Drive lookup failures surface as `FileNotFoundError` with context: `raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")`
- Serial number not found for update raises atomically: `ValueError(f"Serial number '{serial_number}' not found in any Serial Number Listing sheet.")` — no partial writes
- CLI exit on fatal env errors: `sys.exit(1)` with preceding error message to stdout
- No bare `except` clauses; failures propagate up to be handled by caller
## Logging
- Logger created per module: `logger = logging.getLogger(__name__)`
- Warnings for configuration issues: `logger.warning("ALLOWED_USER_IDS is not a comma-separated integer list")`
- Info for state transitions: implicit (code does not log every step)
- No debug logging visible in production code; CLI uses print() for user feedback
## JSON Handling
- Anthropic API responses are parsed with `json.loads()` after stripping markdown code fences:
- This pattern is repeated in `extract_client_details()` and `extract_serial_from_photo()` (`utils/extract.py`)
- Errors in JSON parsing are not caught — they propagate to caller (tests assert `json.JSONDecodeError`)
## Guard Patterns
- Find-or-create operations always check existence before creating: `_find_or_create_folder()` in `utils/drive_folders.py` queries for exact name match, returns existing ID if found
- Repeat calls to `get_unit_folder()` return same IDs without creating duplicates
- Tests verify this: `test_get_unit_folder_never_creates_duplicates_on_repeat_calls`
- Stock sheet updates abort before any writes if serial is not found
- Photo uploads use conflict suffix strategy (`_02`, `_03`) rather than overwrites — never destroy existing files
- Root folders (`Sites`, `Sites/FMAS`) are never created by code — they must exist or `FileNotFoundError` is raised
- This is enforced by design: only `_find_folder_exact()` is called for these; `_find_or_create_folder()` is not
- Service account file existence checked: `if not os.path.exists(sa_path): ... sys.exit(1)`
- Required env vars checked before proceeding: `SERVICE_ACCOUNT_PATH`, `SHARED_DRIVE_ID`
## Date/Time Handling
- Dates stored as ISO 8601 strings (`YYYY-MM-DD`): `datetime.date.today().strftime("%Y-%m-%d")`
- Used in stock sheet "Date Last Move" column and email body
- No timezone handling; all dates are local
## Anthropic Client Caching
- Global `CLIENT = None` in `utils/extract.py`
- Lazy initialization on first use: `_get_client()` checks `if CLIENT is None` then creates via `anthropic.Anthropic(api_key=...)`
- Avoids repeated client instantiation
- Tests reset this cache: `monkeypatch.setattr(extract_mod, "CLIENT", None, raising=False)`
## FastAPI Patterns
- Middleware-based auth: `PasswordMiddleware` checks `atec_auth` cookie
- Global exception handler serializes uncaught errors as JSON with last 1000 chars of traceback
- Endpoints return JSON dicts (no HTML error pages visible to API consumers)
- SPA routing: `/{full_path:path}` serves `static/index.html` for all non-API routes
## Case-Insensitive Comparisons
- Serial number lookup is case-insensitive: `str(cell_value).strip().lower() == serial_lower`
- Header matching also case-insensitive: `if str(row[0]).strip().lower() == "serial number"`
- This handles both user input variations and spreadsheet quirks
## Numeric Handling in Spreadsheets
- Serials may be stored as int/float in Excel (e.g., `200254233608` stored as number, not text)
- Search handles this: if user enters a numeric string, convert to `int` and compare numeric cells via `int()` cast
- This prevents missing matches due to storage format differences
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- Three independent entry points (CLI, FastAPI web app, Telegram bot) all delegate to the same `utils/` module layer
- No ORM, no database — Google Drive is the data store; `.xlsx` files are the stock ledger
- Stateless HTTP API with a single shared-password session cookie for auth; Telegram conversation state is in-memory only
- All Google API interactions are synchronous (google-api-python-client); FastAPI endpoints call them on the main thread (no background tasks or thread pool)
## Layers
- Purpose: Accept user input, orchestrate calls to utils, return results
- Locations: `bookout.py` (CLI), `server.py` (FastAPI), `utils/telegram_bot.py` (Telegram)
- Contains: Input handling, flow control, error display, response formatting
- Depends on: All `utils/` modules
- Used by: End users directly
- Purpose: Reusable business logic shared by all three entry points
- Location: `utils/`
- Contains:
- Depends on: Google APIs, Anthropic API, openpyxl, python-telegram-bot
- Used by: `bookout.py`, `server.py`, `utils/telegram_bot.py`
- Purpose: Single-page web UI served by FastAPI
- Location: `static/index.html`
- Contains: Dashboard, bookout form, add-photos form, check-stock form; drag-and-drop photo upload
- Depends on: FastAPI API endpoints
- Used by: Web browser clients
## Data Flow
- Steps 4 (sheet update) and 7 (email) are skipped
- Steps 5 and 6 (folder + photo upload) still execute
- Detected automatically in `server.py` (`is_swap = result is None`); requires explicit confirmation in the CLI
- Download `.xlsx` via `MediaIoBaseDownload` → openpyxl in-memory → modify → re-upload via `MediaIoBaseUpload`
- No Google Sheets API calls for data; all manipulation is openpyxl on the binary file
- `utils/telegram_state.StateManager` — dict keyed by `chat_id`; each value is a state dict with a `step` field driving the conversation FSM
- State expires after 30 minutes of inactivity
- `utils/telegram_state.SiteStructureStore` — JSON file at `data/atec_site_structures.json`; persists learned folder path templates for direct ATEC sites across bot restarts
## Key Abstractions
- Purpose: In-memory stand-in for the Drive v3 API client; interprets a subset of Drive query syntax
- Location: `tests/conftest.py`
- Pattern: Tracks `records` dict, `create_calls` list, `update_calls` list; `_match_query()` parses `mimeType`, `name`, `name contains`, `in parents`, `trashed` clauses
- Purpose: Per-chat conversation state for the Telegram bot
- Location: `utils/telegram_state.py`
- Pattern: `get(chat_id)` returns `None` on miss or expiry; `set()` stamps `last_activity`; cleared on `/cancel` or completion
- Purpose: Persist folder path templates for direct ATEC sites so repeat bookouts don't require re-navigation
- Location: `utils/telegram_state.py`
- Pattern: `learn()` records segments with `{unit}` token substituted for the unit number; `resolve_template()` substitutes it back at runtime; saves atomically via `.tmp` rename
## Entry Points
- Location: `bookout.py`
- Triggers: `python bookout.py bookout | add-photos | check-stock`
- Responsibilities: Interactive prompts, FMAS/ATEC site-type selection, inline field correction, folder browser (`_browse_to_folder`), swap confirmation
- Location: `server.py`
- Triggers: `uvicorn server:app --reload --port 8000`
- Responsibilities: HTTP API for all three workflows, `PasswordMiddleware` auth, Telegram bot lifecycle (`asynccontextmanager lifespan`), global JSON exception handler, SPA serving
- Location: `utils/telegram_bot.py`
- Triggers: Built and started by `server.py` lifespan if `TELEGRAM_BOT_TOKEN` is set; receives updates via webhook (`POST /telegram/webhook`) or polling
- Responsibilities: Command handling (`/bookout`, `/addphotos`, `/checkstock`, `/cancel`, `/start`), FSM-driven multi-step conversation, photo buffering
## Error Handling
- `FileNotFoundError` raised by `drive_folders.py` and `sheets.py` when required Drive folders (`Sites`, `Sites/FMAS`, `Stock Sheets`) are absent — callers propagate or catch
- `ValueError` raised by `update_stock_row()` if serial not found — no partial writes occur
- FastAPI global exception handler at `server.py:_global_exc` catches all unhandled exceptions and returns `{"detail": ..., "trace": ...}` JSON with status 500
- CLI prints `[ERROR]` prefix messages and calls `sys.exit(1)` on env misconfiguration
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
