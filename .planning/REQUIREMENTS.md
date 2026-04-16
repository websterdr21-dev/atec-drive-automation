# Requirements — ATEC Bookout Automation v1.0

## v1 Requirements

### Site Type Auto-Detection

- [ ] **DETECT-01**: System loads `sites.txt` at startup — a plain text file with one FMAS site name per line, located at a configurable path (default: `data/fmas_sites.txt`)
- [ ] **DETECT-02**: After ticket extraction, the extracted `site_name` is automatically checked against the FMAS site list using case-insensitive comparison
- [ ] **DETECT-03**: If `site_name` matches any entry in the list → FMAS flow proceeds automatically (no user prompt)
- [ ] **DETECT-04**: If `site_name` does not match any entry → Direct ATEC flow proceeds automatically (no user prompt)
- [ ] **DETECT-05**: The manual "FMAS or Direct ATEC?" selection prompt is removed from all three interfaces (CLI, web, Telegram)
- [ ] **DETECT-06**: Site name matching is case-insensitive and strips leading/trailing whitespace

### Folder ID Cache

- [ ] **CACHE-01**: Direct ATEC top-level site folder IDs are cached in a local JSON file (default: `data/atec_folder_cache.json`) as `{site_name: folder_id}`
- [ ] **CACHE-02**: On folder lookup, cache is checked first; if a hit is found, the Drive API traversal is skipped entirely
- [ ] **CACHE-03**: On cache miss, Drive is checked for an existing folder before creating one — folder ID is resolved (found or created) then written to cache immediately
- [ ] **CACHE-04**: Cache is implemented in `utils/drive_folders.py` — all three interfaces (CLI, web, Telegram) benefit automatically without interface-specific changes
- [ ] **CACHE-05**: Cache file is created automatically on first write if it doesn't exist
- [ ] **CACHE-06**: Cache survives server restarts (persisted to disk, not in-memory)

## v2 Requirements (Deferred)

- Fuzzy/partial site name matching — for cases where ticket extraction returns abbreviated or slightly varied site names
- Admin endpoint or CLI command to rebuild/invalidate the cache
- FMAS unit folder ID caching (in addition to site folders)

## Out of Scope

- Pre-creating all FMAS site/unit folders upfront — unnecessary; `find_or_create` handles missing folders at bookout time
- Google Sheet or Drive-based cache storage — local JSON is sufficient, avoids extra API overhead
- Autocomplete or dropdown UI for site name entry — site name comes from Claude ticket extraction, not manual input
- In-memory-only caching — must survive server restarts
- Sending accounts email via Gmail API — email is formatted for copy-paste only (existing behavior preserved)

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DETECT-01 | Phase 1 | Pending |
| DETECT-02 | Phase 1 | Pending |
| DETECT-03 | Phase 1 | Pending |
| DETECT-04 | Phase 1 | Pending |
| DETECT-05 | Phase 1 | Pending |
| DETECT-06 | Phase 1 | Pending |
| CACHE-01 | Phase 2 | Pending |
| CACHE-02 | Phase 2 | Pending |
| CACHE-03 | Phase 2 | Pending |
| CACHE-04 | Phase 2 | Pending |
| CACHE-05 | Phase 2 | Pending |
| CACHE-06 | Phase 2 | Pending |
