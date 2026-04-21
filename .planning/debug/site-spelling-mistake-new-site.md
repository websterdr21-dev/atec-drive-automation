---
status: root_cause_found
slug: site-spelling-mistake-new-site
trigger: There needs to be a way to handle site spelling mistakes in the ticket provided so no new site is created because of a spelling mistake
created: 2026-04-21
updated: 2026-04-21
---

## Symptoms

- **Expected:** When a ticket contains a misspelled site name, the system should fuzzy-match the name to the nearest known existing site and use that
- **Actual:** A new site is created silently — the misspelled name is treated as a brand-new site
- **Error messages:** None — fails silently
- **Timeline:** Unknown — may have always behaved this way
- **Reproduction:** Paste a ticket containing a slightly misspelled site name (e.g. "Pretoria Nort" instead of "Pretoria North") — system creates a new site instead of matching the existing one
- **Input source:** Tickets are copy-pasted from the company's internal ticketing system
- **Site list:** No canonical list of valid sites exists yet, but one can be created from existing site records

## Current Focus

hypothesis: CONFIRMED — site_name from ticket is passed directly to `_find_or_create_folder` which performs an exact-name Drive query; on mismatch it creates a new folder. No normalization or fuzzy-matching layer exists anywhere in the call chain.
test: Traced full flow — extract_client_details → is_fmas_site → get_unit_folder / get_atec_site_folder → _find_or_create_folder
expecting: Direct string lookup with no similarity check
next_action: fix (requires design decision: silent auto-match vs. confirmation gate)

## Evidence

- timestamp: 2026-04-21
  file: utils/drive_folders.py
  lines: 71-103
  finding: >
    `_find_or_create_folder` performs an exact-name Drive query (`name='X'`).
    If no folder matches the exact string, it creates a new one immediately.
    There is no fallback, similarity check, or warning when creating a new site folder.

- timestamp: 2026-04-21
  file: utils/drive_folders.py
  lines: 39-68
  finding: >
    `get_atec_site_folder` wraps `_find_or_create_folder` with a disk cache.
    The cache key is the raw site_name string — so a misspelled name also gets cached,
    meaning future calls with the same misspelling reuse the wrong folder silently.

- timestamp: 2026-04-21
  file: utils/site_detection.py
  lines: 49-55
  finding: >
    `is_fmas_site` does case-insensitive membership check against `data/fmas_sites.txt`,
    but returns only a bool — it does not return the canonical site name.
    A misspelled site_name that is "close" to an FMAS site name will return False,
    routing the bookout to the wrong (ATEC) path and creating a spurious ATEC site folder.

- timestamp: 2026-04-21
  file: utils/extract.py + server.py:270 + bookout.py:171
  finding: >
    site_name flows from Claude extraction → is_fmas_site (bool only) →
    get_unit_folder / get_atec_site_folder with no normalization.
    No layer in the chain attempts fuzzy matching or prompts for confirmation on new-site creation.

- timestamp: 2026-04-21
  file: utils/drive_folders.py
  lines: 21-36
  finding: >
    `list_subfolders` already exists and can enumerate real site folders from Drive.
    This can serve as the canonical site list for fuzzy matching at runtime (ATEC path).
    For FMAS, `data/fmas_sites.txt` already exists as the authoritative list.

## Eliminated

- Sheets update path — site name is only used as a label in `current_account` string; no lookup occurs there.
- Photo upload path — photo naming is independent of site name resolution.

## Resolution

root_cause: >
  `_find_or_create_folder` in `utils/drive_folders.py` performs only an exact-name Drive query.
  The raw `site_name` extracted from the ticket is passed through without any normalization or
  fuzzy-match step. When the name does not exactly match an existing folder, Drive creates a new
  site folder silently. The same flaw affects `is_fmas_site`, which returns only a bool and
  does not resolve the input to a canonical FMAS site name, so a misspelling near an FMAS site
  name silently routes the bookout to the wrong site type.

fix: not applied — requires UX decision (silent auto-correction vs. confirmation gate with threshold)

fix_notes: >
  Recommended approach (to be confirmed before implementing):
  1. Build a canonical site list at runtime: for FMAS use `data/fmas_sites.txt`; for ATEC/FMAS
     folder check, call `list_subfolders` on Sites/FMAS or Sites to get real folder names.
  2. Add a fuzzy-match step using `difflib.get_close_matches` (stdlib, no new dependencies)
     before calling `_find_or_create_folder`. Apply a cutoff (e.g. 0.8).
  3. Confirmation gate: if a close match is found, surface it to the user ("Did you mean
     X?") rather than silently substituting. The Telegram and web UIs must both handle this.
  4. Resolve `is_fmas_site` to return the canonical name (not just a bool) so the corrected
     name propagates through the full flow.
  5. Tests needed: offline `FakeDriveService` coverage for fuzzy-match hit, miss, and
     confirmation paths.

verification:
files_changed:
