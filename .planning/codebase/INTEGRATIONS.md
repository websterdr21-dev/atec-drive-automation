# External Integrations

**Analysis Date:** 2026-04-15

## APIs & External Services

**Google Workspace (via service account):**
- Google Drive v3 — folder navigation, file listing, photo upload, `.xlsx` download/upload
  - SDK/Client: `googleapiclient.discovery.build("drive", "v3", ...)` (`utils/auth.py`, `utils/drive_folders.py`, `utils/sheets.py`, `utils/photos.py`)
  - Auth: `SERVICE_ACCOUNT_PATH` (path to `service_account.json`)
- Google Sheets v4 — not used directly for reads/writes; Sheets files are downloaded as `.xlsx` via Drive and manipulated with openpyxl, then re-uploaded
  - SDK/Client: `googleapiclient.discovery.build("sheets", "v4", ...)` — client built but not actively called in current code
  - Auth: same service account
- Google Docs v1 — client built in `utils/auth.py`, not currently invoked
  - Auth: same service account
- Gmail v1 — client built with domain-wide delegation in `utils/auth.py`; NOT currently used to send or draft mail
  - Auth: service account with DWD; impersonates a user email passed at call time
  - Note: `utils/gmail.py` only formats email text as a string; no API calls are made

**Anthropic Claude:**
- Purpose: ticket text parsing and serial number label photo extraction
  - `extract_client_details()` — text-only prompt, model `claude-opus-4-6`
  - `extract_serial_from_photo()` — multimodal prompt (base64 image + text), model `claude-opus-4-6`
- SDK/Client: `anthropic.Anthropic` (module-level singleton cached in `utils/extract.py`)
- Auth: `ANTHROPIC_API_KEY` env var

**Telegram Bot API:**
- Purpose: conversational mobile interface for bookout, add-photos, check-stock
- SDK/Client: `python-telegram-bot` Application built in `utils/telegram_bot.py`
- Auth: `TELEGRAM_BOT_TOKEN` env var
- Webhook secret: `TELEGRAM_WEBHOOK_SECRET` (validated on `X-Telegram-Bot-Api-Secret-Token` header at `POST /telegram/webhook`)
- Production mode: webhook registered against `https://{RAILWAY_PUBLIC_DOMAIN}/telegram/webhook` on FastAPI startup
- Dev mode: long-polling started inside the FastAPI event loop when `TELEGRAM_USE_POLLING=true`

## Data Storage

**Databases:**
- None — no relational or NoSQL database

**Google Drive (primary data store):**
- All operational data lives in the `Atec Cape Town` Shared Drive
- Stock sheets: `.xlsx` files under `Stock Sheets/[Currently in use folder]/`
- Install photos: organised under `Sites/FMAS/[site]/Unit [N]/` or `Sites/[site]/...`
- Connection: `SHARED_DRIVE_ID` env var identifies the Shared Drive root

**File Storage (local, ephemeral):**
- Uploaded photos are written to `tempfile.NamedTemporaryFile` in `server.py` and deleted immediately after upload to Drive
- `data/atec_site_structures.json` — written by `utils/telegram_state.SiteStructureStore` to persist learned ATEC folder path templates; this is the only file the bot writes to disk

**Caching:**
- No external cache — Telegram conversation state held in-memory via `utils/telegram_state.StateManager` (dict keyed by `chat_id`, 30-minute TTL)
- A commented-out Redis stub exists in `utils/telegram_state.py` for future multi-instance use

## Authentication & Identity

**Auth Provider:**
- Google service account — all Google API calls authenticate via service account credentials from `service_account.json`
- Implementation: `utils/auth.py` — `get_credentials()` loads the file, `get_drive_service()` / `get_sheets_service()` / `get_docs_service()` / `get_gmail_service()` build scoped clients

**Web App Auth:**
- Shared password cookie — `PasswordMiddleware` in `server.py` checks `atec_auth` cookie against `APP_PASSWORD` env var
- `POST /api/login` sets the cookie; `POST /api/logout` clears it
- `/health` and `/telegram/webhook` are whitelisted (no auth required)

**Google API Scopes:**
```python
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.compose",
]
```

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry or equivalent)

**Logs:**
- stdlib `logging` module used in `server.py` and `utils/telegram_state.py`
- FastAPI global exception handler serialises uncaught exceptions as JSON with last 1000 chars of traceback (never returns HTML error pages)
- `server.log` file present at project root (uvicorn output)

## CI/CD & Deployment

**Hosting:**
- Railway — detected via `RAILWAY_PUBLIC_DOMAIN` env var; triggers webhook registration on startup

**CI Pipeline:**
- None detected — no `.github/workflows/` or equivalent

## Environment Configuration

**Required env vars:**
- `SERVICE_ACCOUNT_PATH` — path to `service_account.json`
- `SHARED_DRIVE_ID` — Shared Drive ID for `Atec Cape Town`
- `ANTHROPIC_API_KEY` — Claude API key
- `APP_PASSWORD` — shared password for the web app

**Optional env vars:**
- `TELEGRAM_BOT_TOKEN` — enables Telegram bot (bot disabled if absent)
- `TELEGRAM_WEBHOOK_SECRET` — validates incoming webhook requests
- `TELEGRAM_USE_POLLING` — set `true` for local dev polling
- `RAILWAY_PUBLIC_DOMAIN` — production hostname; triggers webhook registration

**Secrets location:**
- `.env` file at project root (not committed)
- `service_account.json` at project root (not committed; path configurable)

## Webhooks & Callbacks

**Incoming:**
- `POST /telegram/webhook` — receives Telegram `Update` objects; validated via `X-Telegram-Bot-Api-Secret-Token` header

**Outgoing:**
- Telegram webhook registration: bot sets its own webhook URL by calling `bot.set_webhook()` during FastAPI startup when `RAILWAY_PUBLIC_DOMAIN` is set

---

*Integration audit: 2026-04-15*
