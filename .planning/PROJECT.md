# ATEC Stock Bookout Automation

## What This Is

Python tooling that automates the stock bookout + install-photo workflow for ATEC, a fiber network company. Technicians submit a ticket, the system extracts client details and serial numbers via Claude AI, updates the stock spreadsheet in Google Drive, creates the right folder structure, and generates the accounts email — all without touching Drive manually.

Three interfaces share a common `utils/` core: a CLI (`bookout.py`), a FastAPI web app (`server.py`), and a Telegram bot (`utils/telegram_bot.py`).

## Core Value

A technician should be able to complete a full bookout — from ticket paste to email copy — in under two minutes, on any device, without knowing the Google Drive folder structure.

## Requirements

### Validated

- ✓ CLI bookout flow: ticket extraction → serial scan → folder creation → photo upload → email format — existing
- ✓ FastAPI web app with password auth, SPA frontend, drag-and-drop photo upload — existing
- ✓ Telegram bot conversational bookout flow (`/bookout`, `/addphotos`, `/checkstock`) — existing
- ✓ Google Sheets stock ledger: serial search across all `.xlsx` files, row update + red fill — existing
- ✓ Swap mode: serial not found → skip sheet update + email, still upload photos — existing
- ✓ FMAS automated folder creation: `Sites/FMAS/[site]/Unit [N]/` — existing
- ✓ Direct ATEC manual folder browsing with interactive navigation — existing
- ✓ Photo naming convention (`01_Serial_Number`, `02_ONT`, `03_Installation_NN`, etc.) with conflict suffix — existing
- ✓ `SiteStructureStore`: Telegram-only path template memory for Direct ATEC sites — existing
- ✓ Service account authentication for all Google APIs — existing

### Active

- [ ] **FMAS-AUTO-01**: Auto-detect site type from `sites.txt` — if extracted site name matches a known FMAS site, treat as FMAS automatically; if not found, treat as Direct ATEC. Eliminates the manual "FMAS or Direct ATEC?" selection across all three interfaces.
- [ ] **FMAS-AUTO-02**: `sites.txt` file (one FMAS site name per line) loaded at startup; used as the single source of truth for FMAS site membership.
- [ ] **CACHE-01**: Local JSON cache of Direct ATEC top-level folder IDs (`Sites/[site]/ → folder_id`). Populated on first creation/lookup; returned directly on subsequent bookouts without a Drive API traversal.
- [ ] **CACHE-02**: Cache auto-updates whenever a new Direct ATEC site folder is created or opened — no manual refresh needed.

### Out of Scope

- Pre-creating all FMAS site/unit folders upfront — unnecessary given auto-detection; `find_or_create` handles missing folders at bookout time
- Google Sheet or Drive-based cache storage — local JSON is sufficient and faster
- Autocomplete/dropdown UI for site name entry — site name comes from Claude ticket extraction, not manual input
- In-memory-only cache — must survive server restarts

## Context

### Existing folder ID memory

`utils/telegram_state.SiteStructureStore` already persists learned folder path templates for Direct ATEC sites in `data/atec_site_structures.json` — but only for the Telegram interface, and it stores path segments (not folder IDs directly). The new `CACHE-01` should be a simpler, interface-agnostic flat map of `{site_name: folder_id}` used by `drive_folders.py` directly, so all three interfaces benefit automatically.

### FMAS detection replaces a manual prompt

Currently all three interfaces ask the user to explicitly select "FMAS" or "Direct ATEC" at the start of each bookout. With `FMAS-AUTO-01`, the extracted `site_name` is checked against `sites.txt` at the point of ticket extraction — the choice is made silently and the rest of the flow proceeds without interruption.

### Matching strategy matters

FMAS site names in `sites.txt` may not exactly match what Claude extracts from the ticket (abbreviations, slight variations). A case-insensitive, partial-match or normalized comparison should be considered to avoid missed detections routing to Direct ATEC incorrectly.

## Constraints

- **Tech stack**: Python, Google Drive API (service account), openpyxl, python-telegram-bot, FastAPI — no new runtime dependencies unless clearly justified
- **Drive structure**: `Sites` and `Sites/FMAS` are never auto-created by code; `FileNotFoundError` raised if absent — must remain
- **Backward compatibility**: All three interfaces (CLI, web, Telegram) must work after changes; no interface-specific-only fixes
- **Offline tests**: pytest suite must remain fully offline — `FakeDriveService` and mocked Anthropic; any new cache/store code needs test coverage

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Local JSON file for ATEC folder ID cache | Simple, fast, persists across restarts; no extra Drive API overhead | — Pending |
| `sites.txt` as FMAS membership list | Easy for admin to maintain; loaded at startup, no Drive calls needed | — Pending |
| Auto-detection replaces manual prompt entirely | Removes friction for technicians; unknown site always falls back to Direct ATEC safely | — Pending |
| Case-insensitive matching for site name lookup | Ticket extraction may vary capitalization; strict match would miss valid FMAS sites | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-16 after initialization*
