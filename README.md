# ATEC Stock Bookout Automation

Python tooling that automates the stock bookout and install-photo workflow for ATEC, a fiber network company. All Drive content lives in the `Atec Cape Town` Shared Drive, accessed via a Google service account.

Three interfaces share a common `utils/` core:

| Interface | File | Description |
|---|---|---|
| CLI | `bookout.py` | Interactive terminal tool for technicians |
| Web app | `server.py` | FastAPI app with mobile-friendly UI |
| Telegram bot | `utils/telegram_bot.py` | Conversational bot wired into the FastAPI lifespan |

---

## Features

- **Ticket parsing** — paste a ticket and Claude extracts client name, site, unit, address, ISP, speed, and account number
- **Serial label scanning** — photograph a device label and Claude vision reads the serial number and item code
- **Stock sheet lookup + update** — finds the serial across all active `Serial Number Listing` spreadsheets, fills the current account and date, and applies a red highlight to the row
- **Drive folder management** — auto-creates `Sites/FMAS/[site]/Unit [N]` for FMAS jobs; interactive folder browser for direct ATEC jobs
- **Photo upload** — uploads bookout and post-install photos with a strict naming convention; appends a numeric suffix on conflict (never overwrites)
- **Accounts email** — formats a ready-to-copy book-out email (not sent automatically — the user copies it into Gmail)
- **Swap mode** — if a serial is not found in any sheet, the workflow skips the sheet update and email but still creates the Drive folder and uploads photos

---

## Project layout

```
bookout.py                — CLI entry point (bookout / add-photos / check-stock)
server.py                 — FastAPI app + Telegram webhook lifecycle
static/index.html         — single-page frontend served by FastAPI
utils/
  auth.py                 — service-account auth, builds Drive/Sheets/Docs/Gmail services
  env.py                  — central .env loader
  drive_folders.py        — folder navigation + find-or-create (never duplicates)
  sheets.py               — xlsx download/search/update, red-fill on bookout
  photos.py               — photo naming convention + upload (auto-suffixes on conflict)
  extract.py              — Claude-powered ticket + serial-label extraction
  gmail.py                — formats the accounts email as copy-ready text
  telegram_bot.py         — Telegram conversational bot
  telegram_state.py       — in-memory per-chat state machine for the bot
tests/                    — pytest suite (FakeDriveService, mocked Anthropic)
service_account.json      — service-account credentials (local only; not committed)
.env                      — environment variables (see below)
requirements.txt
```

---

## Setup

### Prerequisites

- Python 3.10+
- A Google Cloud service account with access to the `Atec Cape Town` Shared Drive
- An Anthropic API key

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `SERVICE_ACCOUNT_PATH` | Path to `service_account.json` |
| `SHARED_DRIVE_ID` | Shared Drive ID for `Atec Cape Town` |
| `ANTHROPIC_API_KEY` | Claude API key (used by `utils/extract.py`) |
| `APP_PASSWORD` | Shared password for the FastAPI web UI |
| `TELEGRAM_BOT_TOKEN` | Optional — enables the Telegram bot |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user IDs to whitelist |
| `TELEGRAM_WEBHOOK_SECRET` | Shared secret for webhook verification |
| `TELEGRAM_USE_POLLING` | Set `true` for local dev (no public URL needed) |
| `RAILWAY_PUBLIC_DOMAIN` | Production host — triggers automatic webhook registration |

---

## CLI usage

```bash
python bookout.py bookout       # Full bookout flow
python bookout.py add-photos    # Post-install photos only
python bookout.py check-stock   # Serial number lookup across all sheets
```

### `bookout` — step by step

1. Paste the ticket text and press Enter on a blank line.
2. Claude extracts client details; confirm or edit each field.
3. Choose site type: FMAS or direct ATEC.
4. Provide the path to the device-label photo — Claude reads the serial and item code.
5. The serial is searched across all stock sheets.
   - Found → normal flow (sheet update + email).
   - Not found → prompted to proceed as **swap** (sheet + email skipped).
6. Drive folder created or located.
7. Photos uploaded (`01_Serial_Number.jpg`, optionally `04_Device_Photo.jpg`).
8. Accounts email printed for copy-paste.

---

## Web app

```bash
uvicorn server:app --reload --port 8000
```

Then open `http://localhost:8000`. Log in with your `APP_PASSWORD`.

Pages: dashboard, bookout, add photos, check stock.

---

## Telegram bot

When `TELEGRAM_BOT_TOKEN` is set the bot starts automatically with the FastAPI app.

- **Local dev**: set `TELEGRAM_USE_POLLING=true` — no public URL required.
- **Production (Railway)**: set `RAILWAY_PUBLIC_DOMAIN` — webhook registered automatically.

Commands: `/bookout`, `/addphotos`, `/checkstock`, `/cancel`, `/start`.

---

## Photo naming convention

| File | Stage |
|---|---|
| `01_Serial_Number.jpg` | Bookout |
| `02_ONT_Router_Placement.jpg` | Post-install |
| `03_Installation_01.jpg`, `_02`, … | Post-install |
| `04_Device_Photo.jpg` | Bookout (optional) |
| `05_Speed_Test.jpg` | Post-install |

Existing files are never overwritten — a numeric suffix is appended instead (e.g. `01_Serial_Number_02.jpg`).

---

## Running tests

```bash
python -m pytest tests/
```

The test suite is fully offline — no real Google or Anthropic calls are made. A `FakeDriveService` interprets the subset of Drive v3 query syntax used by the codebase, and the Anthropic client is mocked.

---

## Shared Drive structure

```
Atec Cape Town (root)
├── Stock Sheets/
│   └── [folder with "Currently in use" in name]/
│       ├── Serial Number Listing CPT FMAS.xlsx
│       ├── Serial Number Listing CPT.xlsx
│       └── ...
└── Sites/
    ├── FMAS/
    │   └── [Site Name]/
    │       └── Unit [N]/
    └── [Direct ATEC Site Name]/
        └── ...
```

`Sites` and `Sites/FMAS` must already exist — they are never auto-created.

---

## Safety notes

- Never write to the live Shared Drive during testing — swap `SHARED_DRIVE_ID` to a test drive.
- Duplicate folders are never created under the same parent.
- Photos never overwrite existing files.
- Stock sheet writes abort atomically if the serial is not found.
- The accounts email is formatted but never sent — a human is always in the loop.
- `service_account.json` and `.env` are not committed to version control.
