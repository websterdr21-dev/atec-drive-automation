# Coding Conventions

**Analysis Date:** 2026-04-15

## Naming Patterns

**Files:**
- Lowercase with underscores for modules: `sheets.py`, `drive_folders.py`, `telegram_bot.py`
- Entry points are root-level descriptive names: `bookout.py`, `server.py`
- Test files follow pytest convention: `test_stock.py`, `test_drive.py`, `test_photos.py`

**Functions:**
- Lowercase with underscores: `get_unit_folder()`, `find_serial_number()`, `upload_bookout_photos()`
- Private helpers prefixed with underscore: `_download_xlsx()`, `_upload_xlsx()`, `_find_folder_exact()`, `_match_query()`
- CLI command functions: `cmd_bookout()`, `cmd_add_photos()`, `cmd_check_stock()` in `bookout.py`

**Variables:**
- Lowercase with underscores for all variable and constant names
- Constants in UPPERCASE where appropriate: `FOLDER_MIME = "application/vnd.google-apps.folder"`, `RED_FILL`, `PHOTO_TYPES`, `SCOPES`
- Module-level singletons capitalized: `STATE`, `SITES`, `CLIENT` (anthropic client cache)

**Types and Classes:**
- PascalCase for classes: `FakeDriveService`, `_FakeFilesAPI`, `_Inputs`, `_Req`, `StateManager`, `SiteStructureStore`, `PasswordMiddleware`

## Code Style

**Formatting:**
- No formatter explicitly configured (no `.prettierrc`, `setup.cfg`, or `pyproject.toml`)
- Code follows PEP 8 style conventions manually
- Line length appears unconstrained in most files
- Consistent spacing: functions separated by blank lines, internal logic grouped by comment headers

**Linting:**
- No linting configuration detected in repo
- Code does not import from `__future__` unless needed (e.g., `from __future__ import annotations` in modules with forward references)
- Type hints used selectively (function arguments and returns where clarity needed)

**Comments:**
- Triple-quote docstrings at module level explaining purpose and layout: `"""Central .env loader — always use this instead of bare load_dotenv()."""`
- Module-level comment headers in sections: `# ---------------------------------------------------------------------------` demarcation with section title
- Inline comments explain "why" not "what": e.g., `# Numeric comparison: handle int/float stored values`
- No unnecessary comments on obvious code

**Function Design:**
- Parameters use snake_case
- Return values are documented in docstrings (key dict structures described inline)
- Functions are focused on a single operation (avoid god functions)
- Error handling raises explicit exceptions with context: `ValueError(f"Serial number '{serial_number}' not found...")`

**Module Design:**
- Each module in `utils/` is single-purpose: `sheets.py` for Sheet operations, `drive_folders.py` for folder navigation, `photos.py` for photo upload logic
- Barrel files: None used; imports are explicit
- Internal helpers prefixed with `_` signal they are not intended for external use
- Helper functions (`_find_folder`, `_download_xlsx`, `_next_index`) are grouped at module level, not scattered

## Import Organization

**Order:**
1. Standard library imports: `os`, `sys`, `io`, `json`, `datetime`, `base64`, `re`, `tempfile`, etc.
2. Third-party imports: `openpyxl`, `anthropic`, `google.oauth2`, `googleapiclient`, `fastapi`, `telegram`, etc.
3. Local imports: `from utils.auth`, `from utils.sheets`, etc.

**Path Aliases:**
- No path aliases configured
- Imports always use relative module paths: `from utils.env import load`, `from utils.sheets import find_serial_number`

**Example import blocks (from `bookout.py`):**
```python
import os
import sys
from utils.env import load as load_env
```

**Example import blocks (from `utils/sheets.py`):**
```python
import io
import os
import datetime
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import openpyxl
from openpyxl.styles import PatternFill
from utils.auth import get_drive_service
```

## Error Handling

**Patterns:**
- Explicit exceptions raised with descriptive messages: `ValueError`, `FileNotFoundError`
- Google Drive lookup failures surface as `FileNotFoundError` with context: `raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")`
- Serial number not found for update raises atomically: `ValueError(f"Serial number '{serial_number}' not found in any Serial Number Listing sheet.")` — no partial writes
- CLI exit on fatal env errors: `sys.exit(1)` with preceding error message to stdout
- No bare `except` clauses; failures propagate up to be handled by caller

**Example (atomic update, `utils/sheets.py`):**
```python
def update_stock_row(service, drive_id, serial_number, full_address):
    result = find_serial_number(service, drive_id, serial_number)
    if result is None:
        raise ValueError(f"Serial number '{serial_number}' not found...")
    # Only now proceed with download/update — failure before this point = no writes
```

## Logging

**Framework:** `logging` standard library (used in `telegram_bot.py` and `telegram_state.py`)

**Patterns:**
- Logger created per module: `logger = logging.getLogger(__name__)`
- Warnings for configuration issues: `logger.warning("ALLOWED_USER_IDS is not a comma-separated integer list")`
- Info for state transitions: implicit (code does not log every step)
- No debug logging visible in production code; CLI uses print() for user feedback

**Example (from `utils/telegram_state.py`):**
```python
logger = logging.getLogger(__name__)
logger.warning("ALLOWED_USER_IDS is not a comma-separated integer list")
```

## JSON Handling

**Patterns:**
- Anthropic API responses are parsed with `json.loads()` after stripping markdown code fences:
```python
raw = response.content[0].text.strip()
if raw.startswith("```"):
    raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
return json.loads(raw.strip())
```
- This pattern is repeated in `extract_client_details()` and `extract_serial_from_photo()` (`utils/extract.py`)
- Errors in JSON parsing are not caught — they propagate to caller (tests assert `json.JSONDecodeError`)

## Guard Patterns

**Idempotency:**
- Find-or-create operations always check existence before creating: `_find_or_create_folder()` in `utils/drive_folders.py` queries for exact name match, returns existing ID if found
- Repeat calls to `get_unit_folder()` return same IDs without creating duplicates
- Tests verify this: `test_get_unit_folder_never_creates_duplicates_on_repeat_calls`

**Atomic writes:**
- Stock sheet updates abort before any writes if serial is not found
- Photo uploads use conflict suffix strategy (`_02`, `_03`) rather than overwrites — never destroy existing files

**File existence:**
- Root folders (`Sites`, `Sites/FMAS`) are never created by code — they must exist or `FileNotFoundError` is raised
- This is enforced by design: only `_find_folder_exact()` is called for these; `_find_or_create_folder()` is not

**CLI validation:**
- Service account file existence checked: `if not os.path.exists(sa_path): ... sys.exit(1)`
- Required env vars checked before proceeding: `SERVICE_ACCOUNT_PATH`, `SHARED_DRIVE_ID`

## Date/Time Handling

**Pattern:**
- Dates stored as ISO 8601 strings (`YYYY-MM-DD`): `datetime.date.today().strftime("%Y-%m-%d")`
- Used in stock sheet "Date Last Move" column and email body
- No timezone handling; all dates are local

**Example (from `utils/gmail.py`):**
```python
today = datetime.date.today().strftime("%Y-%m-%d")
```

## Anthropic Client Caching

**Pattern:**
- Global `CLIENT = None` in `utils/extract.py`
- Lazy initialization on first use: `_get_client()` checks `if CLIENT is None` then creates via `anthropic.Anthropic(api_key=...)`
- Avoids repeated client instantiation
- Tests reset this cache: `monkeypatch.setattr(extract_mod, "CLIENT", None, raising=False)`

**Example (from `utils/extract.py`):**
```python
CLIENT = None

def _get_client():
    global CLIENT
    if CLIENT is None:
        CLIENT = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return CLIENT
```

## FastAPI Patterns

**Patterns (from `server.py`):**
- Middleware-based auth: `PasswordMiddleware` checks `atec_auth` cookie
- Global exception handler serializes uncaught errors as JSON with last 1000 chars of traceback
- Endpoints return JSON dicts (no HTML error pages visible to API consumers)
- SPA routing: `/{full_path:path}` serves `static/index.html` for all non-API routes

## Case-Insensitive Comparisons

**Pattern:**
- Serial number lookup is case-insensitive: `str(cell_value).strip().lower() == serial_lower`
- Header matching also case-insensitive: `if str(row[0]).strip().lower() == "serial number"`
- This handles both user input variations and spreadsheet quirks

**Example (from `utils/sheets.py`):**
```python
def _matches(cell_value):
    if cell_value is None:
        return False
    # String comparison (case-insensitive)
    if str(cell_value).strip().lower() == serial_lower:
        return True
    # Also handle numeric-stored values
```

## Numeric Handling in Spreadsheets

**Pattern:**
- Serials may be stored as int/float in Excel (e.g., `200254233608` stored as number, not text)
- Search handles this: if user enters a numeric string, convert to `int` and compare numeric cells via `int()` cast
- This prevents missing matches due to storage format differences

**Example (from `utils/sheets.py`):**
```python
try:
    serial_int = int(serial_str)
except ValueError:
    serial_int = None

# Later in comparison:
if serial_int is not None:
    if isinstance(cell_value, (int, float)) and int(cell_value) == serial_int:
        return True
```

---

*Convention analysis: 2026-04-15*
