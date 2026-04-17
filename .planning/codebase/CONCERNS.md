# Concerns

Technical debt, known issues, security, performance, and fragile areas in the ATEC Stock Bookout codebase.

---

## Security

### APP_PASSWORD is a shared plaintext secret
- Location: `server.py` (`PasswordMiddleware`, `/api/login`)
- All users share a single `APP_PASSWORD` from `.env`, compared in plaintext and set as the `atec_auth` cookie value.
- No per-user auth, no rotation, no rate limiting on `/api/login`. A leaked password grants full Drive + stock sheet write access until rotated manually.
- Cookie is set without explicit `Secure` / `HttpOnly` / `SameSite` hardening unless that logic has been added (verify on review).

### Telegram webhook secret is optional
- `utils/telegram_bot.py` / `server.py` `/telegram/webhook` validates `X-Telegram-Bot-Api-Secret-Token` only if `TELEGRAM_WEBHOOK_SECRET` is set.
- If the env var is missing in production, the endpoint accepts any POST — an attacker who guesses the path can drive the bot.

### Service account JSON has broad scopes
- `utils/auth.py` requests Drive, Sheets, Docs, and Gmail compose scopes.
- `service_account.json` lives on disk; anyone with filesystem access can impersonate the service account across the entire Shared Drive. No key rotation schedule documented.
- Gmail compose scope is reserved but unused (see Tech Debt) — currently over-scoped.

### No CSRF protection on state-changing endpoints
- `/api/bookout`, `/api/add-photos`, `/api/create-folder` are cookie-authenticated POSTs. A malicious page the tech visits could forge requests. SameSite cookie attribute mitigates most browsers but should be verified.

---

## Tech Debt

### Gmail scope reserved but never used
- `utils/gmail.py` only formats an email string; nothing is sent. `CLAUDE.md` notes the Gmail compose scope is for a future drafting feature.
- Carrying an unused scope widens the service account blast radius for no benefit. Either drop the scope or ship the draft feature.

### Legacy root-level smoke-test scripts
- `test_connection.py`, `test_serial_photo.py`, `test_sheets.py` at the repo root are manual scripts against the live Drive, not pytest tests.
- Easy to run by accident from the real creds. Move under `scripts/` or `smoke/` and document clearly; `CLAUDE.md` already flags this but the files remain in place.

### In-memory Telegram state machine
- `utils/telegram_state.py` stores per-chat state in a process-local dict.
- A restart or redeploy drops every in-flight conversation. Works today because deploys are single-instance; blocks horizontal scaling and any blue/green deploy strategy.

### Global Anthropic client cache
- `utils/extract.py` holds `CLIENT` as a module-level singleton initialised lazily on first use.
- Fine for current single-process use, but there's no re-init path if the key rotates without a restart, and no retries/backoff around the call.

---

## Fragile Areas

### "Currently in use" folder discovery
- `utils/sheets.py` finds the active stock folder by scanning subfolders of `Stock Sheets` for a name containing `Currently in use`.
- If the folder is renamed, two folders match, or the phrase drifts (`currently-in-use`, trailing whitespace), serial lookups silently fail or hit the wrong folder. Matching is substring-based with no uniqueness guard — consider asserting exactly one match and logging on mismatch.

### Header-row detection in xlsx sheets
- `find_serial_number` locates the header row by scanning column A for `Serial Number`.
- Any sheet that reformats the header, adds a title row in column A, or renames the column breaks the lookup. Errors present as "serial not found" → user is prompted to proceed in swap mode, which masks the real failure (sheet was not updated when it should have been).

### Numeric-vs-string serial storage
- `sheets.py` falls back to `int()` comparison when the serial is all digits and the cell is numeric — this handles `0200254233608`-as-number but assumes leading zeros don't matter for uniqueness.
- If a real serial differs only in leading zeros, this collapses them into the same match.

### Red-fill mutates unrelated columns
- `update_stock_row` applies solid `FF0000` fill from column 1 to `ws.max_column`.
- `max_column` includes any trailing columns with formatting but no data. If a sheet has a phantom column (e.g., column Z used once then cleared), the red fill extends past visible data. Cosmetic, but surprising.

### Direct ATEC folder browser has no path validation
- `bookout.py` `_browse_to_folder` and `/api/browse` let the user navigate freely under `Sites/`. No guardrail prevents uploading into `Sites/FMAS/...` via the ATEC path, which would bypass the automated unit-folder logic.

### Photo suffix counter is O(n) per upload
- `utils/photos.py` lists the folder and increments until free. Fine for a dozen files; pathological if a folder accumulates hundreds of installation photos.

---

## Performance

### `/api/dashboard` downloads every active stock sheet
- Every dashboard hit pulls each `Serial Number Listing *.xlsx` via Drive export. With 5 sheets and modest size this is tolerable but scales linearly with sheet count and size. No caching layer.
- Consider caching the last-fetched dashboard per-sheet with a short TTL or switching to a single consolidated summary sheet.

### Serial search scans every row of every sheet
- `find_serial_number` opens every sheet in the active folder and scans until it finds a match. Worst case (miss) touches every row in every sheet on every call.
- Acceptable for the current volume; revisit if row count grows past ~10k or if dashboard + check-stock + bookout pile up concurrently.

### Drive API pagination not implemented
- `utils/drive_folders.py` list calls rely on default page size. If a parent folder ever exceeds the default (~100 children), results silently truncate. Not an issue today but latent.

---

## Testing Gaps

### No Telegram bot tests
- `utils/telegram_bot.py` and `utils/telegram_state.py` have no pytest coverage. State transitions, cancel flow, and webhook secret validation are uncovered.

### No FastAPI endpoint tests
- `server.py` routes (`/api/bookout`, `/api/dashboard`, `/api/browse`, middleware behaviour) are not tested. The CLI flow is covered via `tests/` but the web layer is not.

### Swap-mode coverage is thin
- `CLAUDE.md` claims swap mode is covered in the CLI happy-path test. Verify the swap-mode branch also asserts that `update_stock_row` and the email formatter are NOT invoked.

### No integration smoke against FakeDriveService for photo conflict suffixing at scale
- Single-conflict case is tested; multi-conflict (`_02`, `_03`, installation `03_Installation_04`) branches may be under-exercised.

---

## Operational / Missing Features

### No structured logging
- No `logging.basicConfig`, no request/response middleware, no trace ids. Debugging a bad bookout relies on the ad-hoc exception JSON returned by the global handler.

### Global exception handler leaks tracebacks
- `server.py` returns the last 1000 chars of any uncaught exception's traceback as JSON. Useful in dev, risky in prod — stack frames can include file paths, env-var names, or library internals useful for an attacker. Gate on `DEBUG`/env.

### No rate limiting
- `/api/login`, `/api/extract-ticket`, `/api/extract-serial` all call paid APIs (Anthropic) or gate auth. A loose credential + a loop can burn API credits or brute-force the password.

### Single-instance deploy assumption
- In-memory Telegram state + no external session store ⇒ app is not safe behind a load balancer. Document this in deploy notes or add Redis-backed state before scaling.

---

## Dependency Risks

### Loose pins on key packages
- `requirements.txt` should be reviewed for pinned-vs-floating versions on `python-telegram-bot` (breaking changes between major versions), `anthropic` (SDK still evolving), and `google-api-python-client` / `google-auth`.

### Anthropic model hardcoded to `claude-opus-4-6`
- `utils/extract.py` hardcodes the model string. Model deprecations or a desire to use a cheaper Haiku for serial extraction require a code change. Consider moving to env var.

---

## Data Integrity

### Stock sheet writes are not transactional across sheets
- Bookout writes to exactly one sheet, which is fine. But there's no cross-sheet dedup check — if the same serial somehow appears in two sheets, only one is updated and the other still shows in-stock. Low likelihood but worth a one-off audit script.

### No audit trail for who booked out what
- Red fill + date + account are written, but the service account is always the actor. The tech's identity (CLI user, web session, Telegram chat id) is not recorded in the sheet. Disputes ("who booked this?") have no trail.
