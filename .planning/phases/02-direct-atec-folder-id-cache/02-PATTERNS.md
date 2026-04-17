# Phase 02: Direct ATEC Folder ID Cache - Pattern Map

**Mapped:** 2026-04-17
**Files analyzed:** 2 (1 modified, 1 extended)
**Analogs found:** 2 / 2

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `utils/drive_folders.py` | utility (cache class + function enhancement) | request-response + file-I/O | `utils/telegram_state.py` (`SiteStructureStore`) | exact — same JSON persistence, same singleton pattern |
| `tests/test_drive.py` | test | CRUD | `tests/test_drive.py` (existing suite) | exact — same file, additive |

---

## Pattern Assignments

### `utils/drive_folders.py` — `_AtecFolderCache` class (new) + `get_atec_site_folder()` (modified)

**Primary analog:** `utils/telegram_state.py` — `SiteStructureStore` class
**Secondary analog:** `utils/extract.py` — module-level singleton pattern

---

#### Imports to add (copy from `utils/telegram_state.py` lines 14-24)

```python
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
```

`json`, `os`, and `Path` are already present in `telegram_state.py` imports. `drive_folders.py` currently has no imports at all — add these at the top of the file.

---

#### Module-level singleton pattern (copy from `utils/extract.py` lines 13-19)

```python
CLIENT = None

def _get_client():
    global CLIENT
    if CLIENT is None:
        CLIENT = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return CLIENT
```

Apply verbatim structure, substituting names:

```python
_CACHE: "_AtecFolderCache | None" = None

def _get_cache() -> "_AtecFolderCache":
    global _CACHE
    if _CACHE is None:
        _CACHE = _AtecFolderCache()
    return _CACHE
```

---

#### `_load()` pattern (copy from `utils/telegram_state.py` lines 137-151)

```python
def _load(self) -> None:
    if not self.path.exists():
        self._data = {}
        return
    try:
        with self.path.open("r", encoding="utf-8") as f:
            self._data = json.load(f)
            if not isinstance(self._data, dict):
                raise ValueError("top-level JSON is not an object")
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning(
            "Site structure store at %s is corrupt (%s) — starting empty",
            self.path, e,
        )
        self._data = {}
```

Apply with adjusted log message wording (e.g., "Folder ID cache at %s is corrupt (%s) — starting empty").

---

#### `_save()` atomic write pattern (copy from `utils/telegram_state.py` lines 153-158)

```python
def _save(self) -> None:
    self.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = self.path.with_suffix(self.path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(self._data, f, indent=2, sort_keys=True)
    os.replace(tmp, self.path)
```

Copy verbatim — this is the exact atomic write required by D-11. The `data/` directory creation guard (`mkdir(parents=True, exist_ok=True)`) is on line 154 and must not be omitted.

---

#### `DEFAULT_STORE_PATH` constant naming (copy from `utils/telegram_state.py` line 107)

```python
DEFAULT_STORE_PATH = "data/atec_site_structures.json"
```

Mirror this as a class attribute (per RESEARCH.md §3):

```python
class _AtecFolderCache:
    DEFAULT_PATH = "data/atec_folder_cache.json"
```

---

#### Constructor pattern (copy from `utils/telegram_state.py` lines 131-134)

```python
def __init__(self, path: str = DEFAULT_STORE_PATH):
    self.path = Path(path)
    self._data: dict[str, dict] = {}
    self._load()
```

Apply substituting `DEFAULT_PATH` and narrowing the type annotation to `dict[str, str]` (flat `{site_name: folder_id}` map):

```python
def __init__(self, path: str = DEFAULT_PATH):
    self.path = Path(path)
    self._data: dict[str, str] = {}
    self._load()
```

---

#### `get_atec_site_folder()` integration (modified — `utils/drive_folders.py` lines 30-39)

Current function body:

```python
def get_atec_site_folder(service, drive_id, site_name):
    sites_id = _find_folder_exact(service, "Sites", drive_id, drive_id)
    if not sites_id:
        raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")
    return _find_or_create_folder(service, site_name, sites_id, drive_id)
```

After modification (per RESEARCH.md §4):

```python
def get_atec_site_folder(service, drive_id, site_name):
    cache = _get_cache()
    cached_id = cache.get(site_name)
    if cached_id:
        return cached_id, False          # cache hit — no Drive call

    # cache miss: find or create, then write to cache
    sites_id = _find_folder_exact(service, "Sites", drive_id, drive_id)
    if not sites_id:
        raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")
    folder_id, created = _find_or_create_folder(service, site_name, sites_id, drive_id)
    cache.set(site_name, folder_id)
    return folder_id, created
```

Return signature `(folder_id: str, created: bool)` is unchanged — callers are unaffected.

---

### `tests/test_drive.py` — new test class/functions (additive)

**Analog:** `tests/test_drive.py` existing suite (lines 1-179) + `utils/telegram_state.py` test patterns implied by `SiteStructureStore`

---

#### Test file header and import pattern (copy from `tests/test_drive.py` lines 1-8)

```python
"""
Drive navigation + folder creation / dedup.
Target: utils/drive_folders.py
"""

import pytest

from utils import drive_folders
```

New tests append to this file. No new imports needed beyond what's already present.

---

#### Cache injection fixture pattern (from RESEARCH.md §6)

```python
@pytest.fixture(autouse=True)
def reset_cache(tmp_path, monkeypatch):
    from utils import drive_folders
    cache = drive_folders._AtecFolderCache(str(tmp_path / "atec_folder_cache.json"))
    monkeypatch.setattr(drive_folders, "_CACHE", cache)
    yield
    monkeypatch.setattr(drive_folders, "_CACHE", None)
```

This fixture uses `tmp_path` (pytest built-in), `monkeypatch` (pytest built-in), and `drive_folders._AtecFolderCache` (new class). Both `tmp_path` and `monkeypatch` are already used in the existing test suite (`conftest.py` line 296, `tests/test_drive.py` implicitly via `seeded_drive`).

---

#### Existing test assertion style (copy from `tests/test_drive.py` lines 157-178)

```python
def test_get_atec_site_folder_creates_if_missing(seeded_drive, drive_id):
    svc = seeded_drive
    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfront"
    )
    assert created is True
    assert svc.records[site_id]["parents"] == [svc.ids["sites"]]
```

New cache tests follow the same pattern: use `seeded_drive` + `drive_id` fixtures, unpack `(site_id, created)` return, assert on `svc.create_calls` count and `svc.list_queries` to verify Drive was or was not called.

---

#### Drive-call count assertion pattern (copy from `tests/test_drive.py` lines 86-95)

```python
calls_before = len(svc.create_calls)

unit_id, _, site_created, unit_created = drive_folders.get_unit_folder(...)

assert len(svc.create_calls) == calls_before  # zero create calls
```

For cache-hit tests, assert `len(svc.list_queries) == 0` (or record the query count before calling) to prove no Drive list call was made.

---

## Shared Patterns

### Atomic JSON write
**Source:** `utils/telegram_state.py` lines 153-158 (`SiteStructureStore._save`)
**Apply to:** `_AtecFolderCache._save()` in `utils/drive_folders.py`

```python
def _save(self) -> None:
    self.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = self.path.with_suffix(self.path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(self._data, f, indent=2, sort_keys=True)
    os.replace(tmp, self.path)
```

### Corrupt-file guard on JSON load
**Source:** `utils/telegram_state.py` lines 137-151 (`SiteStructureStore._load`)
**Apply to:** `_AtecFolderCache._load()` in `utils/drive_folders.py`

```python
except (json.JSONDecodeError, ValueError, OSError) as e:
    logger.warning(
        "Folder ID cache at %s is corrupt (%s) — starting empty",
        self.path, e,
    )
    self._data = {}
```

### Module-level lazy singleton
**Source:** `utils/extract.py` lines 13-19
**Apply to:** `_CACHE` / `_get_cache()` in `utils/drive_folders.py`

```python
_CACHE: "_AtecFolderCache | None" = None

def _get_cache() -> "_AtecFolderCache":
    global _CACHE
    if _CACHE is None:
        _CACHE = _AtecFolderCache()
    return _CACHE
```

### Cache monkeypatch in tests
**Source:** `tests/conftest.py` lines 296-298 (Anthropic CLIENT reset pattern)
**Apply to:** `reset_cache` fixture in `tests/test_drive.py`

```python
# conftest.py reference:
monkeypatch.setattr(extract_mod, "CLIENT", None, raising=False)

# Apply as:
monkeypatch.setattr(drive_folders, "_CACHE", cache)   # inject tmp_path-backed instance
# ...teardown:
monkeypatch.setattr(drive_folders, "_CACHE", None)
```

### `FileNotFoundError` for missing root folders
**Source:** `utils/drive_folders.py` lines 37-38
**Apply to:** `get_atec_site_folder()` — this guard is preserved unchanged in the modified version

```python
if not sites_id:
    raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")
```

---

## No Analog Found

None. All patterns needed for this phase already exist verbatim in the codebase.

---

## Metadata

**Analog search scope:** `utils/`, `tests/`
**Files read:** `utils/drive_folders.py`, `utils/telegram_state.py`, `utils/extract.py`, `tests/test_drive.py`, `tests/conftest.py`
**Pattern extraction date:** 2026-04-17
