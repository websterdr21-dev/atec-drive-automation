# Phase 2: Direct ATEC Folder ID Cache - Context

**Gathered:** 2026-04-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Cache Direct ATEC top-level site folder IDs (`Sites/[site_name]` → `folder_id`) in a local JSON file so repeat bookouts resolve the folder in one cache lookup, skipping the Drive API traversal entirely. All three interfaces (CLI, web, Telegram) benefit automatically because the cache lives in `utils/drive_folders.py`.

This phase does NOT change the FMAS flow, the unit-folder resolution, or any interface-specific logic.

</domain>

<decisions>
## Implementation Decisions

### Cache Structure and Location
- **D-01:** Cache file path is `data/atec_folder_cache.json` — flat `{site_name: folder_id}` map. Hardcoded default (same pattern as `SiteStructureStore`'s `DEFAULT_STORE_PATH`). Not configurable via env var.
- **D-02:** Cache logic lives entirely in `utils/drive_folders.py`. No new module needed.
- **D-03:** Cache is a module-level singleton, initialized lazily on first use of `get_atec_site_folder()`. Auto-created on first write if the file doesn't exist.

### Cache Hit Flow
- **D-04:** On a cache hit, use the cached folder ID directly without a pre-validation Drive call. No extra API round-trip on the happy path.

### Stale Cache Recovery
- **D-05:** Stale entries are detected on first use: if Drive returns a 404/error when the cached ID is actually used (e.g., during folder browsing or photo upload), treat it as a stale hit.
- **D-06:** Recovery is silent — no error surfaced to the caller. Fall through to the find-or-create flow (same as a cache miss), then overwrite the cache entry with the new resolved ID.
- **D-07:** Find-or-create on recovery: check Drive for an existing folder before creating, same as cache-miss. Prevents duplicate folders if the original still exists under a different ID.

### Cache Miss Flow
- **D-08:** On cache miss: check Drive for an existing `Sites/[site_name]` folder before creating one, resolve the ID, write it to cache immediately, then return.

### Key Normalization
- **D-09:** Cache keys stored and looked up as-is (original casing from ticket extraction). Lookup uses the same case-insensitive strip that `get_atec_site_folder()` receives — upstream normalization handles this. No internal key transformation.

### SiteStructureStore Coexistence
- **D-10:** The new flat cache coexists independently with `SiteStructureStore.folder_id_cache`. The Telegram bot's `SiteStructureStore` is unchanged. The new cache is the shared, interface-agnostic layer; `SiteStructureStore` remains Telegram-only path template memory.

### Atomic Writes
- **D-11:** Cache writes use the same atomic pattern as `SiteStructureStore._save()`: write to `.tmp` file, then `os.replace()` to prevent corrupt JSON on crash/interrupt.

### Tests
- **D-12:** Tests must be fully offline. Use `tmp_path` pytest fixture for the JSON file (inject path into the cache module). Cover: cache hit (no Drive call), cache miss + write, missing-file initialisation, stale recovery (Drive error → re-resolve → cache updated).

### Claude's Discretion
- Internal helper naming (`_AtecFolderCache`, `_load`, `_save`, etc.)
- Whether to expose `invalidate(site_name)` helper for future use — include if trivial, skip if adds complexity

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above.

### Requirements
- `.planning/REQUIREMENTS.md` §Folder ID Cache (CACHE-01 through CACHE-06) — acceptance criteria

### Existing Code to Read
- `utils/drive_folders.py` — function to enhance: `get_atec_site_folder()`; atomic helper `_find_or_create_folder()`
- `utils/telegram_state.py` — `SiteStructureStore` class (lines ~110–240) — atomic write pattern and JSON persistence pattern to follow
- `tests/test_drive.py` — existing drive test patterns using `FakeDriveService`
- `tests/conftest.py` — `FakeDriveService` and `seeded_drive` fixtures

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `utils/drive_folders.py:_find_or_create_folder()` — already handles find-or-create with dedup; cache miss and stale-recovery flows call this directly
- `utils/drive_folders.py:_find_folder_exact()` — used inside find-or-create; no changes needed
- `utils/telegram_state.py:SiteStructureStore._save()` — atomic write pattern with `.tmp` + `os.replace()` to replicate verbatim

### Established Patterns
- Module-level singleton: `CLIENT = None` / `_get_client()` lazy init in `utils/extract.py` — same approach for the cache instance
- JSON persistence with corrupt-file guard: `SiteStructureStore._load()` catches `JSONDecodeError`/`ValueError`/`OSError` and starts empty — apply same guard
- `path.parent.mkdir(parents=True, exist_ok=True)` before write — ensures `data/` dir exists automatically

### Integration Points
- `get_atec_site_folder(service, drive_id, site_name)` in `drive_folders.py` — the single function to enhance; all three interfaces call this, so no interface-specific changes needed
- `server.py` and `bookout.py` and `utils/telegram_bot.py` call `get_atec_site_folder()` — they are unaffected
- `tests/test_drive.py` — new test class or functions added here; `tmp_path` fixture injects the cache file path

</code_context>

<specifics>
## Specific Ideas

- Stale detection happens at the point of actual use (e.g., listing subfolders of the cached ID returns an error), not at a dedicated validation step. The caller already handles Drive errors at that level — the cache layer just needs to catch the specific "not found" error from `get_atec_site_folder()` and retry via the miss path.
- No specific reference implementations requested — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

- Fuzzy/partial site name matching — v2 backlog (already in REQUIREMENTS.md §v2)
- Admin endpoint or CLI to rebuild/invalidate the full cache — v2 backlog
- FMAS unit folder ID caching — v2 backlog
- Cache configurability via env var — not needed for v1; hardcoded path is sufficient

</deferred>

---

*Phase: 02-direct-atec-folder-id-cache*
*Context gathered: 2026-04-17*
