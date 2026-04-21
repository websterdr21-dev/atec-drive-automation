---
phase: 02-direct-atec-folder-id-cache
verified: 2026-04-21T00:00:00Z
status: passed
score: 7/7 must-haves verified
overrides_applied: 0
---

# Phase 2: Direct ATEC Folder ID Cache Verification Report

**Phase Goal:** Direct ATEC top-level site folder IDs are persisted locally so every interface resolves them in a single cache lookup on repeat bookouts
**Verified:** 2026-04-21
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `data/atec_folder_cache.json` is created automatically on first write and contains a flat `{site_name: folder_id}` map that persists across restarts (CACHE-06) | VERIFIED | `_AtecFolderCache._save()` creates parent dir + writes atomically via `.tmp` + `os.replace()`; `_load()` reads on `__init__`; `data/` present in `.gitignore` |
| 2 | A repeat bookout for a known Direct ATEC site resolves the top-level folder without any Drive API calls | VERIFIED | `get_atec_site_folder()` lines 56-59: checks `cache.get(site_name)` first; returns `(cached_id, False)` immediately; `test_get_atec_site_folder_cache_hit_skips_drive` asserts `list_queries` and `create_calls` counts unchanged |
| 3 | On cache miss, Drive is checked for an existing folder before creating one; ID is written to cache immediately after resolution | VERIFIED | `get_atec_site_folder()` lines 61-81: calls `_find_or_create_folder` (which does list-before-create), then `cache.set(site_name, folder_id)`; `test_get_atec_site_folder_cache_miss_finds_existing_before_create` asserts no new `create_calls` when folder pre-exists |
| 4 | Cache logic lives entirely in `utils/drive_folders.py`; all three interfaces benefit without interface-specific changes | VERIFIED | Only `utils/drive_folders.py` and `tests/test_drive.py` were modified; `bookout.py`, `server.py`, `utils/telegram_bot.py` unchanged; full 114-test suite (including CLI tests) passes |
| 5 | Tests cover cache hit, cache miss + write, and missing-file initialisation — all passing offline | VERIFIED | 5 cache tests in `tests/test_drive.py`: `cache_hit_skips_drive`, `cache_miss_writes_to_cache`, `cache_miss_finds_existing_before_create`, `creates_cache_file_on_first_write`, `stale_recovery_after_delete`; all pass (114/114 suite green) |
| 6 | Test isolation fixture prevents singleton leakage across tests | VERIFIED | `@pytest.fixture(autouse=True)` `_isolate_cache` at line 11 in `tests/test_drive.py`; monkeypatches `_CACHE` with a `tmp_path`-backed instance before each test in the module; satisfies CACHE-07 from task spec |
| 7 | Return signature `(folder_id, created)` is unchanged; all existing callers still unpack two values | VERIFIED | `get_atec_site_folder()` returns `(cached_id, False)` on hit and `(folder_id, created)` on miss; pre-existing tests `test_get_atec_site_folder_creates_if_missing` and `test_get_atec_site_folder_reuses_existing` still pass |

**Score:** 7/7 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `utils/drive_folders.py` | `_AtecFolderCache` class + `_CACHE` singleton + `_get_cache()` factory; modified `get_atec_site_folder()` with cache-first lookup | VERIFIED | Class at lines 213-270; singleton at line 273; factory at lines 276-280; `get_atec_site_folder()` wired at lines 56-82 |
| `tests/test_drive.py` | `_isolate_cache` autouse fixture + `tmp_cache` fixture + 5 cache-specific test functions | VERIFIED | Autouse fixture at lines 11-15; `tmp_cache` fixture at lines 193-205; 5 new test functions at lines 208-316 |
| `.gitignore` | `data/` entry present | VERIFIED | `data/` listed under "Runtime data" section |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `get_atec_site_folder` | `_get_cache` | `cache = _get_cache()` at line 56 | WIRED | First statement in function body |
| `get_atec_site_folder` | `_AtecFolderCache.set` | `cache.set(site_name, folder_id)` at line 81 | WIRED | Called after find-or-create resolves the ID |
| `_AtecFolderCache.__init__` | `_AtecFolderCache._load` | `self._load()` at line 232 | WIRED | Constructor calls `_load()` so new instance picks up persisted entries |
| `_AtecFolderCache.set` | `_AtecFolderCache._save` | `self._save()` at line 265 | WIRED | Every `set()` mutates dict and persists immediately |
| `tests/test_drive.py:_isolate_cache` | `drive_folders._CACHE` | `monkeypatch.setattr(drive_folders, "_CACHE", cache)` at line 15 | WIRED | Autouse fixture injects tmp_path-backed singleton before each test |
| `tests/test_drive.py:tmp_cache` | `drive_folders._CACHE` | `monkeypatch.setattr(drive_folders, "_CACHE", cache)` at line 203 | WIRED | Explicit fixture for cache-specific tests; yields the cache instance for pre-population |

---

### Data-Flow Trace (Level 4)

Cache stores and retrieves string IDs — no rendering of dynamic data in the traditional sense. The data flow is: `get_atec_site_folder()` calls `cache.get(site_name)` → returns cached string ID → caller uses ID to navigate Drive. On miss: `_find_or_create_folder()` returns `(folder_id, created)` → `cache.set(site_name, folder_id)` persists to JSON → `os.replace()` atomically commits the write.

The value entering `cache.set()` is the actual Drive-resolved `folder_id` (not a placeholder), and the same value is returned from `cache.get()` on subsequent calls. `test_get_atec_site_folder_cache_miss_writes_to_cache` verifies the on-disk JSON matches the in-memory value.

Cross-instance persistence (create `_AtecFolderCache(same_path)` twice and assert `_load()` picks up the earlier write) is not exercised by an automated test. The plan (02-02 Task 2, "Do NOT" list) explicitly deferred this as "optional future enhancement, not a CACHE-0X requirement." The write side is proven by the disk-assertion in `test_get_atec_site_folder_cache_miss_writes_to_cache`; the `_load()` path is covered by the two-line constructor calling `_load()` immediately. This deferral does not block PASSED.

---

### Behavioral Spot-Checks

All 114 tests pass (`python -m pytest tests/ -x -q` — `114 passed in 1.88s`). The test suite is fully offline (FakeDriveService) and exercises all cache paths.

| Behavior | Method | Result | Status |
|----------|--------|--------|--------|
| Cache hit returns correct ID with zero Drive calls | `test_get_atec_site_folder_cache_hit_skips_drive` | Asserts `list_queries` and `create_calls` counts unchanged | PASS |
| Cache miss creates folder and writes to disk | `test_get_atec_site_folder_cache_miss_writes_to_cache` | Asserts on-disk JSON equals `{site_name: folder_id}` | PASS |
| Cache miss finds existing Drive folder before creating | `test_get_atec_site_folder_cache_miss_finds_existing_before_create` | Asserts `create_calls` count unchanged; cache populated | PASS |
| Cache file + parent dirs created automatically on first write | `test_get_atec_site_folder_creates_cache_file_on_first_write` | Uses nested tmp_path dirs; asserts file and parent exist | PASS |
| Stale recovery via delete + retry | `test_get_atec_site_folder_stale_recovery_after_delete` | Delete clears cache; retry resolves real ID and repopulates | PASS |
| Full suite regression | All 114 tests | 114 passed | PASS |

---

### Requirements Coverage

| Requirement | Plans | Description | Status | Evidence |
|-------------|-------|-------------|--------|----------|
| CACHE-01 | 02-01, 02-02 | Cache in `data/atec_folder_cache.json` as flat `{site_name: folder_id}` | SATISFIED | `DEFAULT_PATH = "data/atec_folder_cache.json"`; `dict[str, str]` type on `_data` |
| CACHE-02 | 02-02 | Cache hit skips Drive API traversal entirely | SATISFIED | `cache.get(site_name)` checked first; returns immediately if truthy |
| CACHE-03 | 02-02 | On miss, find-before-create then write to cache immediately | SATISFIED | `_find_or_create_folder` list-before-create; `cache.set()` called after resolution |
| CACHE-04 | 02-01, 02-02 | Logic in `utils/drive_folders.py`; all interfaces benefit | SATISFIED | No changes to `bookout.py`, `server.py`, `utils/telegram_bot.py` |
| CACHE-05 | 02-01 | Cache file auto-created on first write | SATISFIED | `_save()` calls `self.path.parent.mkdir(parents=True, exist_ok=True)` |
| CACHE-06 | 02-01 | Cache persists across restarts | SATISFIED | Atomic JSON write; `_load()` reads from file on `__init__` |

---

### Anti-Patterns Found

No blockers or stubs found.

One out-of-scope addition noted: `get_atec_site_folder()` includes a `_fuzzy_match_subfolder()` call at lines 67-76. This was introduced by commit `06e0934` ("feat: fuzzy site name matching to prevent spurious folder creation"), which lands after the phase-2-complete commit `90c0b7f`. It is post-phase-2 scope, not a phase-2 deviation. The cache-first early-return path (lines 56-59) is unaffected; fuzzy matching only runs on cache miss after the `Sites` lookup. All tests pass with it in place.

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `utils/drive_folders.py` lines 67-76 | `_fuzzy_match_subfolder()` call — post-phase-2 addition (commit `06e0934`, after `90c0b7f`) | Info | Superset behaviour; cache-hit path unaffected; all tests pass |

---

### Human Verification Required

None. All must-haves are verifiable programmatically. The cache is a backend-only persistence layer with no UI rendering.

---

### Gaps Summary

No gaps. All 7 observable truths are verified. All 6 CACHE requirements (CACHE-01 through CACHE-06) are satisfied. All artifacts exist, are substantive, and are wired. The full 114-test suite passes offline.

---

_Verified: 2026-04-21_
_Verifier: Claude (gsd-verifier)_
