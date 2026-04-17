# Phase 02: Direct ATEC Folder ID Cache — Research

**Status:** RESEARCH COMPLETE
**Confidence:** HIGH
**Approach:** Copy-adapt-wire — all patterns already exist in codebase

---

## Summary

This phase is additive-only. No new dependencies, no new modules required. The full implementation is a ~80-line class added to `utils/drive_folders.py` plus test functions added to `tests/test_drive.py`.

---

## Key Findings

### 1. Single Integration Point

`get_atec_site_folder(service, drive_id, site_name)` in `utils/drive_folders.py` (line 30) is the sole function to modify. All three interfaces route through it:

- `utils/telegram_bot.py` lines 376, 384, 997, 1002
- `bookout.py` lines 265–267, 335–336
- `server.py` line 364–366

No interface-specific changes needed.

### 2. Persistence Pattern — Copy from SiteStructureStore

`utils/telegram_state.py` `SiteStructureStore` provides the exact pattern to replicate:

- `_load()`: reads JSON, handles `JSONDecodeError`/`ValueError`/`OSError` → starts empty on corruption
- `_save()`: atomic write via `.tmp` + `os.replace()`
- `path.parent.mkdir(parents=True, exist_ok=True)` before every write
- Module-level singleton with lazy init (`CLIENT = None` pattern from `utils/extract.py`)

### 3. Cache Class Design

`_AtecFolderCache` at module level in `utils/drive_folders.py`:

```python
class _AtecFolderCache:
    DEFAULT_PATH = "data/atec_folder_cache.json"  # flat {site_name: folder_id}

    def __init__(self, path: str = DEFAULT_PATH): ...
    def _load(self) -> None: ...        # JSON load with corrupt-file guard
    def _save(self) -> None: ...        # atomic .tmp + os.replace()
    def get(self, site_name: str) -> str | None: ...
    def set(self, site_name: str, folder_id: str) -> None: ...
    def delete(self, site_name: str) -> None: ...  # stale recovery
```

Module-level singleton:

```python
_CACHE: _AtecFolderCache | None = None

def _get_cache() -> _AtecFolderCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = _AtecFolderCache()
    return _CACHE
```

### 4. Integration into get_atec_site_folder()

Current:
```python
def get_atec_site_folder(service, drive_id, site_name):
    sites_id = _find_folder_exact(service, "Sites", drive_id, drive_id)
    if not sites_id:
        raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")
    return _find_or_create_folder(service, site_name, sites_id, drive_id)
```

After:
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

Return type unchanged: `(folder_id: str, created: bool)`.

### 5. Stale Recovery

Per D-05/D-06: stale detection happens at the point of actual use. Primary surface is `list_subfolders()` — called when the folder browser loads a cached ID.

Pattern: wrap `list_subfolders()` call sites (or the function itself) to catch Drive 404/error on the cached ID → call `cache.delete(site_name)` → fall through to the find-or-create path → update cache with new ID.

Scope for this phase: add stale recovery to `get_atec_site_folder()` callers or expose a `cache.delete()` for callers to invoke on Drive error. Simplest approach: document that callers should call `_get_cache().delete(site_name)` on error and retry — keeps `get_atec_site_folder()` itself clean.

### 6. Test Injection Pattern

Monkeypatch `_CACHE` with a `tmp_path`-backed instance per test. No changes to `conftest.py` needed.

```python
@pytest.fixture(autouse=True)
def reset_cache(tmp_path, monkeypatch):
    from utils import drive_folders
    cache = drive_folders._AtecFolderCache(str(tmp_path / "atec_folder_cache.json"))
    monkeypatch.setattr(drive_folders, "_CACHE", cache)
    yield
    monkeypatch.setattr(drive_folders, "_CACHE", None)
```

Required test cases (maps to CACHE-05/CACHE-06):
- Cache hit: `get()` returns ID → `get_atec_site_folder()` called → Drive NOT called
- Cache miss: ID not cached → Drive called → ID written to cache → returned
- Missing file init: `data/atec_folder_cache.json` doesn't exist → `set()` creates it atomically
- Stale recovery: cache has ID → Drive returns error → delete + re-resolve → cache updated

---

## Validation Architecture

Not applicable — no network calls, no async, no new endpoints. All verification via pytest offline tests.

---

## Risk Assessment

| Risk | Level | Mitigation |
|------|-------|-----------|
| Key casing mismatch | LOW | D-09: upstream normalization; no internal transform needed |
| Corrupt JSON on crash | LOW | Atomic `.tmp` + `os.replace()` pattern from SiteStructureStore |
| Stale entry on folder delete | LOW | D-05/D-06: silent recovery via find-or-create on Drive error |
| `data/` dir missing | LOW | `mkdir(parents=True, exist_ok=True)` before every write |
| Tests hitting real Drive | NONE | `FakeDriveService` + monkeypatched `_CACHE`; autouse fixture blocks live calls |

---

## No Open Questions

CONTEXT.md decisions (D-01 through D-12) resolve all design choices. Ready for planning.
