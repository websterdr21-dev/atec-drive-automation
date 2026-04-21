# Roadmap: ATEC Bookout Automation

## Overview

Two targeted enhancements to the existing bookout workflow. Phase 1 eliminates the manual "FMAS or Direct ATEC?" selection prompt across all three interfaces by loading a `sites.txt` file and auto-detecting site type from the extracted site name. Phase 2 adds a local JSON cache so Direct ATEC top-level folder IDs are resolved instantly on repeat bookouts without a Drive API traversal. Both phases are backend-only changes that benefit all three interfaces automatically.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: FMAS Site Auto-Detection** - Load `sites.txt` and automatically determine site type from the extracted site name, eliminating the manual prompt
- [x] **Phase 2: Direct ATEC Folder ID Cache** - Cache Direct ATEC top-level folder IDs to disk so repeat bookouts skip the Drive API traversal entirely

## Phase Details

### Phase 1: FMAS Site Auto-Detection
**Goal**: Site type is determined automatically from the extracted ticket site name — technicians are never asked "FMAS or Direct ATEC?"
**Depends on**: Nothing (first phase)
**Requirements**: DETECT-01, DETECT-02, DETECT-03, DETECT-04, DETECT-05, DETECT-06
**Success Criteria** (what must be TRUE):
  1. A `data/fmas_sites.txt` file is loaded at startup and its entries are available to the site-type check without any Drive API calls
  2. After ticket extraction, a site name matching an entry in `sites.txt` (case-insensitive, whitespace-stripped) automatically triggers the FMAS flow with no user prompt
  3. After ticket extraction, a site name not found in `sites.txt` automatically triggers the Direct ATEC flow with no user prompt
  4. The manual "FMAS or Direct ATEC?" selection step is absent from the CLI, web app, and Telegram bot
  5. Tests cover FMAS match, Direct ATEC fallback, and case/whitespace edge cases — all passing offline
**Plans:** 2 plans
Plans:
- [x] 01-01-PLAN.md — Create site detection module (data/fmas_sites.txt + utils/site_detection.py + tests)
- [x] 01-02-PLAN.md — Wire auto-detection into CLI, web app, and Telegram bot

### Phase 01.1: Telegram Bot Bug Fixes: Unit Folder + Serial Correction (INSERTED)

**Goal:** [Urgent work - to be planned]
**Requirements**: TBD
**Depends on:** Phase 1
**Plans:** 2/2 plans complete

Plans:
- [x] TBD (run /gsd-plan-phase 01.1 to break down) (completed 2026-04-16)

### Phase 2: Direct ATEC Folder ID Cache
**Goal**: Direct ATEC top-level site folder IDs are persisted locally so every interface resolves them in a single cache lookup on repeat bookouts
**Depends on**: Phase 1
**Requirements**: CACHE-01, CACHE-02, CACHE-03, CACHE-04, CACHE-05, CACHE-06
**Success Criteria** (what must be TRUE):
  1. A `data/atec_folder_cache.json` file is created automatically on first write and contains a flat `{site_name: folder_id}` map that persists across server restarts
  2. A repeat bookout for a known Direct ATEC site resolves the top-level folder without making any Drive API calls (cache hit)
  3. On a cache miss, the system checks Drive for an existing folder before creating one — the folder ID is resolved (found or created) then written to cache immediately
  4. Cache logic lives entirely in `utils/drive_folders.py` so all three interfaces (CLI, web, Telegram) benefit without interface-specific changes
  5. Tests cover cache hit, cache miss + write, and missing-file initialisation — all passing offline
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. FMAS Site Auto-Detection | 2/2 | Complete | 2026-04-16 |
| 01.1. Telegram Bot Bug Fixes | 2/2 | Complete | 2026-04-17 |
| 2. Direct ATEC Folder ID Cache | 2/2 | Complete | 2026-04-21 |
