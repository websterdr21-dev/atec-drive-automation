---
phase: 00-review
reviewed: 2026-04-17T00:00:00Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - bookout.py
  - server.py
  - utils/auth.py
  - utils/drive_folders.py
  - utils/env.py
  - utils/extract.py
  - utils/gmail.py
  - utils/photos.py
  - utils/sheets.py
  - utils/site_detection.py
  - utils/telegram_bot.py
  - utils/telegram_state.py
findings:
  critical: 4
  warning: 8
  info: 5
  total: 17
status: issues_found
---

# Code Review Report

**Reviewed:** 2026-04-17
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

The codebase is well-structured with clear separation of concerns across CLI, FastAPI, and Telegram interfaces all sharing a `utils/` core. The review focused on logic gaps — incorrect assumptions, missing error handling at system boundaries, state machine bugs, and edge cases that could silently fail.

Four critical issues were found: the `get_drive_service` signature contract is broken (passes a path string as `scopes`, silently misbehaves), `update_stock_row` double-downloads the XLSX and introduces a TOCTOU race, Drive folder name injection is possible via unescaped single quotes in folder names, and login grants a valid session when `APP_PASSWORD` is empty. Eight warnings cover logic gaps in the Telegram flow (orphaned temp files, lost state, nav-to-upload-and-reply dispatch issues) and smaller data-consistency gaps across all three interfaces.

---

## Critical Issues

### CR-01: `get_drive_service` signature mismatch — path silently passed as `scopes`

**File:** `utils/auth.py:51-53`

`get_drive_service` accepts `service_account_path: str | None = None` and immediately passes that argument straight to `get_credentials(service_account_path)`. But `get_credentials` treats its first positional parameter as `scopes`, not a path. When the CLI calls `get_drive_service(sa_path)` (e.g. `bookout.py:121`), the service-account file path string is silently passed as the `scopes` list. The credential resolution inside `get_credentials` then falls through to the hardcoded `service_account.json` lookup (because `scopes` is a non-None truthy string) and the `sa_path` argument is completely ignored. This means:

1. The CLI `_get_env()` guard (checking that the file at `SERVICE_ACCOUNT_PATH` exists) provides false safety — a different credentials file could be silently used in production.
2. The `get_sheets_service`, `get_docs_service`, and `get_gmail_service` wrappers have the same signature problem.

**Fix:**
```python
# utils/auth.py — rename the argument and thread it correctly
def get_drive_service(service_account_path: str | None = None):
    creds = get_credentials()           # ignore the dead param for now, OR:
    # Preferred: pass path into get_credentials properly
    # get_credentials currently ignores file-path args entirely;
    # add a 'sa_path' param and use it before the env-var fallback chain.
    return build("drive", "v3", credentials=creds)
```

The real fix is to wire `service_account_path` into `get_credentials` as a first-priority lookup before `service_account.json` and `GCP_SERVICE_ACCOUNT_B64`. Until that is done, passing `sa_path` to `get_drive_service` does nothing.

---

### CR-02: Double XLSX download + TOCTOU race in `update_stock_row`

**File:** `utils/sheets.py:207-233`

`update_stock_row` calls `find_serial_number` (which downloads every matching sheet), then immediately calls `_download_xlsx` again on the same file. Between these two downloads a second concurrent request (e.g. two technicians booking out simultaneously) could modify the file. The second download re-reads the live state correctly, but the `row_index` used for writing (`result["row_index"]`) was computed from the first download. If any row has been inserted or deleted in between, the write lands on the wrong row.

Beyond the race: the double download is purely wasteful — the workbook is already in memory from `find_serial_number`'s loop and discarded. The `find_serial_number` result does not include the workbook object, so the caller must fetch it again.

**Fix:**

Return the workbook from `find_serial_number` (or refactor into a `_load_sheet_for_serial` helper that returns `(result, wb, ws)` so `update_stock_row` reuses the already-downloaded workbook for its write pass):

```python
def find_serial_number(service, drive_id, serial_number):
    ...
    for file_info in sheets:
        wb = _download_xlsx(service, file_info["id"])
        for ws in wb.worksheets:
            ...
            for row_idx, row in ...:
                if _matches(cell_value):
                    return {
                        ...,
                        "_wb": wb,   # include workbook so update can reuse it
                    }
    return None
```

At a minimum, document that `update_stock_row` performs two downloads so operators are aware of the race window.

---

### CR-03: Drive query injection via unescaped single quotes in folder/site names

**File:** `utils/drive_folders.py:47-58`, `utils/drive_folders.py:79-91`, `utils/sheets.py:31-44`

All Drive API query strings are built with f-strings using raw user-supplied values:

```python
f"name='{name}' and '{parent_id}' in parents"
```

If `name` contains a single quote (e.g. site name `"O'Brien Court"`), the query becomes malformed (`name='O'Brien Court'`). The Google Drive API will return an error or, depending on the client library's behaviour, silently return zero results — causing `_find_or_create_folder` to **create a duplicate folder** with the apostrophe-containing name each time it is called.

This is not arbitrary code execution but it is a data-integrity issue (duplicate folders) and a bug surface for any technician whose site name includes a common character.

**Fix:**
```python
def _escape_drive_query(s: str) -> str:
    """Escape single quotes for use in Drive API query strings."""
    return s.replace("'", "\\'")

# Then in every query:
f"name='{_escape_drive_query(name)}' and ..."
```

Apply the same escaping in `_find_folder` in `sheets.py` where `name_exact` and `name_contains` are interpolated.

---

### CR-04: Login bypasses auth when `APP_PASSWORD` is empty

**File:** `server.py:155-162`

```python
if not APP_PASSWORD or password == APP_PASSWORD:
    resp = JSONResponse({"ok": True})
    resp.set_cookie("atec_auth", APP_PASSWORD, ...)
    return resp
```

When `APP_PASSWORD` is an empty string (e.g. the env var is not set), `not APP_PASSWORD` is `True` and **any** password (including an empty string) grants a valid session cookie. The middleware also skips the token check when `APP_PASSWORD` is falsy (`if APP_PASSWORD:` on line 78), so the app is fully unprotected.

This is the intended "dev mode" behaviour per the doc comment, but it silently fires in production too if the env var is accidentally unset. A misconfigured Railway deploy would be world-accessible.

**Fix:**
```python
@app.post("/api/login")
async def login(request: Request):
    if not APP_PASSWORD:
        raise HTTPException(status_code=500, detail="APP_PASSWORD is not configured")
    body = await request.json()
    if body.get("password", "") != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("atec_auth", APP_PASSWORD, httponly=True, samesite="strict")
    return resp
```

If open-access dev mode is needed, make it opt-in via an explicit `AUTH_DISABLED=true` env var checked at startup, rather than silently triggered by an empty password.

---

## Warnings

### WR-01: Temp files leaked when `_process_bookout` exits via `_start_guided_nav`

**File:** `utils/telegram_bot.py:767-770`

When an unknown ATEC site triggers guided navigation, `_continue_after_swap_confirm` calls `_start_guided_nav` and returns without cleaning up `paths` (the downloaded temp files). The paths are stored in `state["_tmp_paths"]`, but `_cleanup_paths` is not called. The state is still live (step=STEP_NAV), so `/cancel` will eventually clean them via `cmd_cancel`. However, if the user simply abandons the session and the 30-minute expiry fires, the state dict is silently evicted from `StateManager` without any temp-file cleanup — the files are orphaned forever.

**Fix:** In `StateManager.get`, when expiry fires, call a cleanup hook:

```python
def get(self, chat_id: int) -> Optional[dict]:
    s = self._store.get(chat_id)
    if s is None:
        return None
    if time.time() - s.get("last_activity", 0) > EXPIRY_SECONDS:
        _cleanup_paths(s.get("_tmp_paths", []))  # import from telegram_bot
        del self._store[chat_id]
        return None
    return s
```

Alternatively, move cleanup responsibility into the expiry path of the bot itself (e.g. a periodic task).

---

### WR-02: `_handle_add_photos` only handles the triggering message — media group photos beyond the first are silently dropped

**File:** `utils/telegram_bot.py:1137-1199`

`_handle_add_photos` is invoked from `on_message` before the media-group buffering logic. For a multi-photo media group captioned `"add photos Site Unit"`, the first photo in the group triggers the handler and uploads a single file. Subsequent photos in the same group each re-enter `on_message`, hit the `low.startswith("add photos")` branch again, and each re-download the same Drive listing, each uploading exactly one file independently. While the end result is that all photos get uploaded, each upload call does a fresh `list_existing_filenames` round-trip and the naming logic (`02_ONT → 05_Speed → 03_Install_NN`) may fire inconsistently depending on Telegram message delivery order, potentially naming two photos `02_ONT_Router_Placement.jpg` or skipping `05_Speed_Test.jpg`.

**Fix:** Route `add photos` captions through the same media-group buffering path (`_ingest_bookout_message` / `_flush_media_group_after_delay`), passing all photos to a dedicated add-photos processor once the group is complete.

---

### WR-03: `find_serial_number` matches on ANY column, not just Serial Number column — can produce wrong row

**File:** `utils/sheets.py:181-193`

The inner loop iterates over all cells in each row:

```python
for cell_value in row:
    if _matches(cell_value):
        return {...}
```

If a serial-like string (or a number matching the serial's numeric form) appears in a non-serial column — e.g. in a date column, a notes column, or in the header row itself — it will be returned as a match. The header row is explicitly skipped, but rows before `header_row_idx` (title rows, summary rows) are also searched for all their cells. A mismatch here causes `update_stock_row` to write the "Current Account" and red-fill to the wrong row.

**Fix:** After finding `header_row_idx`, determine `serial_col` (the column index where column-A header == "Serial Number") and only match against `row[serial_col]`:

```python
header_row_idx = None
serial_col = 0  # column A is always serial, but confirm from headers
for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
    if row and str(row[0]).strip().lower() == "serial number":
        header_row_idx = r_idx
        headers = list(row)
        break

for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
    if header_row_idx and row_idx <= header_row_idx:
        continue
    if _matches(row[0]):   # only check the serial column (col A)
        return { ... }
```

---

### WR-04: `_handle_serial_correction` — correcting one serial does not re-search the remainder, leaving prior `is_swap=True` items unchanged

**File:** `utils/telegram_bot.py:839-882`

`_advance_serial_correction` looks for the next swap item with `i > state.get("_correction_serial_index", 0)`. But once the user enters a corrected serial that is found (line 826), the item at `idx` has `is_swap=False`. However, items that were `is_swap=True` and had an index *before* the current one (already prompted and answered "swap") are never reconsidered. This is correct behaviour for earlier items, but the scan `i > _correction_serial_index` also means that if the current item is the last one in the list and it was just resolved, `next_swap` returns `None` and the flow continues — which is also correct.

The real gap: after `_advance_serial_correction` updates state and finds no more swaps, it calls `_continue_after_swap_confirm`. Inside that function, the stock update loop iterates `state["items"]` and calls `update_stock_row` for non-swap items. But the stock update uses a newly obtained `service = _get_drive()` — a fresh auth call — while the serial lookup used a different service instance obtained earlier in `_process_bookout`. Both should succeed, but if the service credentials expire between the two calls (unlikely but possible in long sessions), the update will fail while the lookup succeeded.

More critically: `update_stock_row` internally calls `find_serial_number` again (CR-02 above). For a corrected serial (the user typed a new string), this second `find_serial_number` uses the corrected value correctly. But the *item_code* in `state["items"][idx]` still holds the original (potentially wrong) Claude-extracted value, since only `item["serial"]` is updated on correction (line 831). The item code in the accounts email and the success message will therefore be stale.

**Fix:** When the user provides a corrected serial and `find_serial_number` succeeds, also update the item code from the returned result if the sheet row contains it:

```python
if result is not None:
    item["serial"] = corrected
    item["is_swap"] = False
    # Attempt to refresh item_code from the sheet row
    headers_lower = [str(h).lower() if h else "" for h in result.get("headers", [])]
    item_col = next((i for i, h in enumerate(headers_lower) if "item" in h), None)
    if item_col is not None and item_col < len(result["row_values"]):
        item["item_code"] = str(result["row_values"][item_col] or item["item_code"])
```

---

### WR-05: `bookout.py` `cmd_add_photos` increments `i` only on success but uses `i` in the prompt on failure — counter desync

**File:** `bookout.py:354-365`

```python
i = 1
while True:
    p = input(f"  03_Installation_{i:02d} photo (Enter to stop): ").strip().strip('"')
    if not p:
        break
    if os.path.exists(p):
        install_paths.append(p)
        i += 1
    else:
        print(f"  [WARN] Not found, skipping: {p}")
```

When a path is entered but does not exist, `i` is not incremented. The next prompt still shows `03_Installation_01` (or whichever number was current). This is correct behaviour — the slot wasn't filled — but the prompt is confusing: the user sees `03_Installation_01` again after being told the file was not found, implying they should retry. If they enter a second wrong path, the same slot label shows again. If they then press Enter to stop, they have zero installation photos despite trying to provide two. The logic is technically correct but the UX creates a misleading prompt loop.

This is a borderline logic gap: the counter is consistent, but the prompt number does not communicate "this slot was skipped." A simple fix is to always increment `i` (treating a missing file as consuming the slot) or to explicitly tell the user "Slot 01 skipped."

**Fix:**
```python
    else:
        print(f"  [WARN] Not found, skipping slot {i:02d}: {p}")
        i += 1  # consume the slot even on skip so the prompt advances
```

---

### WR-06: `PasswordMiddleware` reads `static/index.html` from a relative path — breaks when the server is not started from the project root

**File:** `server.py:87`

```python
html = Path("static/index.html").read_text(encoding="utf-8")
```

This is a relative path. If uvicorn is launched from any directory other than the project root (e.g. `uvicorn atec.server:app` from a parent directory), the file read will raise `FileNotFoundError` inside the middleware, which is not caught. The middleware would then propagate the exception as a 500, serving neither the login page nor a useful error. The `serve_frontend` endpoint at the bottom of `server.py` handles the missing-file case gracefully, but the middleware does not reach that handler.

**Fix:**
```python
_STATIC_DIR = Path(__file__).parent / "static"

class PasswordMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ...
        html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
```

---

### WR-07: `_find_or_create_folder` exposed as a public API endpoint — allows arbitrary folder creation anywhere in the Shared Drive

**File:** `server.py:327-344`

`POST /api/create-folder` accepts a `parent_id` from the request body and calls `_find_or_create_folder(get_drive(), name, parent_id, drive_id())`. An authenticated user can pass any `parent_id` — including the Shared Drive root itself, the `Stock Sheets` folder, or any other sensitive location — and create folders with any name. Combined with the cookie-based auth being the only guard (and CR-04 above showing the cookie can be empty-string), this is a privilege escalation path for misuse.

**Fix:** Restrict `parent_id` to only IDs that the authenticated session has previously been given (e.g. from `/api/site-folder` or `/api/browse`). At minimum, validate that `parent_id` is a child of `Sites/` before creating.

---

### WR-08: `update_stock_row` silently succeeds even when neither `account_col` nor `date_col` is found

**File:** `utils/sheets.py:220-226`

```python
account_col = next((i + 1 for i, h in enumerate(headers_lower) if "current account" in h), None)
date_col    = next((i + 1 for i, h in enumerate(headers_lower) if "date last move" in h), None)

if account_col:
    ws.cell(row=row_idx, column=account_col).value = full_address
if date_col:
    ws.cell(row=row_idx, column=date_col).value = ...
```

If both column lookups return `None` (e.g. the sheet has been reformatted), the function still applies the red fill and re-uploads the workbook. From the caller's perspective the update "succeeded" (no exception), but neither the `Current Account` nor `Date Last Move` columns were written. The stock record is now highlighted red with no meaningful data written — silent partial corruption.

**Fix:**
```python
if account_col is None and date_col is None:
    raise ValueError(
        f"Sheet '{result['sheet_name']}' in '{result['file_name']}' is missing "
        f"expected columns 'Current Account' and 'Date Last Move'. "
        f"Check the sheet header row."
    )
```

---

## Info

### IN-01: `_find_folder` in `sheets.py` has no `parent_id` guard — could match folders outside `Stock Sheets`

**File:** `utils/sheets.py:27-47`

`_find_folder` is called with `name_exact="Stock Sheets"` and no `parent_id`. Since `corpora="drive"` searches the entire Shared Drive, any folder named `Stock Sheets` anywhere in the drive will match. If a technician creates a folder with that name elsewhere, the first result wins. The drive API does not guarantee ordering, so the match is non-deterministic.

**Suggestion:** Pass the Shared Drive root ID as `parent_id` for the top-level `Stock Sheets` lookup to pin it to the drive root.

---

### IN-02: `classify_photo_names` in `telegram_bot.py` assigns roles by fixed positional order regardless of actual content

**File:** `utils/telegram_bot.py:160-180`

After the leading serial-label photos, remaining photos are assigned "device", "ont", "speed" strictly in position order. A technician sending `serial_label + speed_test` (no device, no ONT) will have the speed test named `04_Device_Photo.jpg`. The function has no way to override this without the user knowing the exact required photo order.

This is by design (the docstring documents the order), but worth noting since real-world usage where photos are sent in a different order will produce misleading file names with no error or warning.

**Suggestion:** Document the required send-order in the Telegram bot's help text so technicians know the expected sequence.

---

### IN-03: `SiteStructureStore.learn` — partial match on `unit_number` inside arbitrary segment names can over-tokenize

**File:** `utils/telegram_state.py:191-193`

```python
elif unit_number and unit_number in seg and seg.startswith("Unit "):
    template.append(seg.replace(unit_number, self.UNIT_TOKEN))
```

If `unit_number` is `"1"` and a segment is `"Unit 12"`, `"1" in "Unit 12"` is `True` and `seg.startswith("Unit ")` is `True`, so the template becomes `"Unit {unit}2"`. On replay with unit `"1"`, this resolves back to `"Unit 12"` — which happens to be correct by accident. But with unit `"2"`, it resolves to `"Unit {unit}2"` → `"Unit 22"`, which is wrong. The fix in the comment ("Partial match only within Unit X-style names") does not fully prevent this for single-digit unit numbers that appear as a substring.

**Suggestion:** Use a whole-word match: `seg == f"Unit {unit_number}"` rather than the `in` substring check.

---

### IN-04: `extract_serial_from_photo` defaults unknown extensions to `image/png` — will be wrong for other image types

**File:** `utils/extract.py:79`

```python
media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
```

Any non-JPEG extension (e.g. `.heic`, `.webp`, `.bmp`) silently gets `image/png`. The Claude API may reject the request or interpret the image incorrectly. This primarily affects the Telegram upload path where `_download_photo` always saves as `.jpg` (correct), but the FastAPI `/api/extract-serial` endpoint uses the original filename suffix, which could be anything.

**Suggestion:** Raise a `ValueError` for unsupported extensions rather than silently defaulting to PNG, so failures are visible.

---

### IN-05: `dashboard` endpoint in `server.py` downloads every sheet on every request — no caching

**File:** `server.py:176-254`

The `/api/dashboard` endpoint downloads all active Serial Number Listing XLSX files on every HTTP request with no caching. Each download is a full Drive API round-trip plus openpyxl parse. For a dashboard loaded on a mobile device this will be slow and consumes Drive API quota on every page refresh.

**Suggestion:** Add a simple time-based cache (e.g. `functools.lru_cache` with a `maxsize=1` + a manual expiry timestamp) that holds the dashboard rows for 60 seconds between fetches.

---

_Reviewed: 2026-04-17_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
