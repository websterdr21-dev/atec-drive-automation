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
