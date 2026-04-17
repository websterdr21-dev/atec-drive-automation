# Technology Stack

**Analysis Date:** 2026-04-15

## Languages

**Primary:**
- Python 3.14 — All application code (CLI, FastAPI backend, Telegram bot, utils)

**Secondary:**
- HTML/CSS/JavaScript — Single-file SPA at `static/index.html` (no build step)

## Runtime

**Environment:**
- Python 3.14 (local dev: Windows; production: Railway)

**Package Manager:**
- pip
- Lockfile: Not present — `requirements.txt` specifies minimum versions only

## Frameworks

**Core:**
- FastAPI >=0.115.0 — HTTP API server and static file serving (`server.py`)
- Starlette (via FastAPI) — Middleware (`PasswordMiddleware`), response types

**CLI:**
- No third-party CLI framework — plain `sys.argv` dispatch in `bookout.py`

**Telegram:**
- python-telegram-bot >=20.0 — Async bot framework; supports webhook and polling modes

**Build/Dev:**
- uvicorn >=0.30.0 — ASGI server for FastAPI (`uvicorn server:app --reload --port 8000`)

## Key Dependencies

**Critical:**
- `google-api-python-client >=2.100.0` — Drive v3, Sheets v4, Docs v1, Gmail v1 API clients
- `google-auth >=2.23.0` — Service account credential loading (`utils/auth.py`)
- `google-auth-httplib2 >=0.1.1` — HTTP transport for google-auth
- `anthropic >=0.34.0` — Claude API client used in `utils/extract.py` (model: `claude-opus-4-6`)
- `openpyxl >=3.1.0` — Downloads, parses, modifies, and re-uploads `.xlsx` stock sheets (`utils/sheets.py`)
- `python-dotenv >=1.0.0` — `.env` loading via `utils/env.py`
- `python-multipart >=0.0.9` — Required by FastAPI for `multipart/form-data` (photo uploads)

## Configuration

**Environment:**
- All configuration sourced from `.env` at project root
- Loaded once via `utils/env.py:load()` at the top of each entry point (`bookout.py`, `server.py`)
- Required vars: `SERVICE_ACCOUNT_PATH`, `SHARED_DRIVE_ID`, `ANTHROPIC_API_KEY`, `APP_PASSWORD`
- Optional vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_USE_POLLING`, `RAILWAY_PUBLIC_DOMAIN`

**Build:**
- No build step — Python runs directly
- No `pyproject.toml` or `setup.py` — `requirements.txt` only
- No Docker configuration present

## Testing

**Framework:**
- pytest — test suite in `tests/` directory
- stdlib `unittest.mock` — mocking Anthropic and Drive API calls
- Custom `FakeDriveService` in `tests/conftest.py` — in-memory Drive v3 stand-in

## Platform Requirements

**Development:**
- Python 3.14
- `service_account.json` present at path referenced by `SERVICE_ACCOUNT_PATH` (not committed)
- `.env` file present with required vars
- Set `TELEGRAM_USE_POLLING=true` for local Telegram bot dev (no public URL needed)

**Production:**
- Hosted on Railway
- Single-instance deployment (Telegram conversation state is in-memory only — not Redis-backed)
- `RAILWAY_PUBLIC_DOMAIN` triggers automatic Telegram webhook registration on startup

---

*Stack analysis: 2026-04-15*
