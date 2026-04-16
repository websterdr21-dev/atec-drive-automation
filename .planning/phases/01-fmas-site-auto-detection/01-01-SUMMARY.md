---
phase: 01-fmas-site-auto-detection
plan: "01"
subsystem: site-detection
tags: [fmas, site-detection, utils, tests]
dependency_graph:
  requires: []
  provides: [utils/site_detection.py, data/fmas_sites.txt]
  affects: []
tech_stack:
  added: []
  patterns: [module-level-load, env-var-override, graceful-fallback]
key_files:
  created:
    - data/fmas_sites.txt
    - utils/site_detection.py
    - tests/test_site_detection.py
  modified: []
decisions:
  - "Case-insensitive exact match for FMAS site detection (fuzzy matching deferred to v2)"
  - "Module-level load at import time avoids repeated file I/O on each call"
  - "FMAS_SITES_PATH env var override allows test isolation without patching internals"
  - "reload() helper function enables test-time injection of custom site lists"
metrics:
  duration: "2m"
  completed: "2026-04-16"
  tasks_completed: 2
  files_changed: 3
---

# Phase 01 Plan 01: FMAS Site Detection Module Summary

**One-liner:** Case-insensitive FMAS site membership check loaded at import time from `data/fmas_sites.txt` with graceful fallback and test-friendly reload API.

## What Was Built

Created the foundation for FMAS auto-detection — a lightweight `utils/site_detection.py` module that loads 17 FMAS site names from `data/fmas_sites.txt` at import time into a normalised set, and exposes `is_fmas_site()` for O(1) case-insensitive, whitespace-stripped lookups. A `reload()` helper enables test isolation without monkey-patching.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create data/fmas_sites.txt and utils/site_detection.py | cf1d796 | data/fmas_sites.txt, utils/site_detection.py |
| 2 | Create tests/test_site_detection.py | beb6f6a | tests/test_site_detection.py |

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Case-insensitive exact match | Ticket extraction may vary capitalisation; strict match would miss valid FMAS sites. Fuzzy matching deferred to v2. |
| Module-level load at import | Avoid per-call file I/O; set loaded once, lookups are O(1) hash set membership. |
| FMAS_SITES_PATH env var | Allows alternative deployments to point at a different file without code changes. |
| reload() function | Clean test isolation — tests call reload(tmp_path) rather than patching module internals. |
| Graceful fallback on missing file | T-01-02 (DoS) threat mitigation: missing file logs warning and routes all sites to Direct ATEC safely. |

## Deviations from Plan

None — plan executed exactly as written.

## Verification Results

```
python -c "from utils.site_detection import is_fmas_site; assert is_fmas_site('The Topaz'); assert is_fmas_site('the topaz'); assert is_fmas_site('  The Topaz  '); assert not is_fmas_site('Unknown'); print('All checks pass')"
All checks pass

python -m pytest tests/test_site_detection.py -v
12 passed in 0.62s
```

## Known Stubs

None.

## Threat Flags

No new security surface introduced. Threat T-01-02 (missing file DoS) was explicitly mitigated per the plan's threat model: `_load_sites()` catches `FileNotFoundError`, logs a warning, and returns an empty set — no crash.

## Self-Check: PASSED

- data/fmas_sites.txt: FOUND
- utils/site_detection.py: FOUND
- tests/test_site_detection.py: FOUND
- Commit cf1d796: FOUND
- Commit beb6f6a: FOUND
