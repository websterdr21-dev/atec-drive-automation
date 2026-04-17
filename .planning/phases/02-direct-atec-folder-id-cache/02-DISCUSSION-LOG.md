# Phase 2: Direct ATEC Folder ID Cache - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-17
**Phase:** 02-direct-atec-folder-id-cache
**Areas discussed:** Stale cache recovery

---

## Area Selection

| Gray Area | Selected |
|-----------|----------|
| Stale cache recovery | ✓ |
| SiteStructureStore overlap | |
| Cache key normalization | |
| Cache file path | |

---

## Stale Cache Recovery

### Q1: What happens when a cached folder ID is gone from Drive?

| Option | Description | Selected |
|--------|-------------|----------|
| Silent re-resolve | Catch Drive 404/error, treat as miss, re-resolve (find or create), update cache. Bookout continues uninterrupted. | ✓ |
| Raise and fail | Surface error to caller. Admin must manually clear cache before retry. | |
| Delete stale entry, re-resolve | Same outcome as silent re-resolve — just explicitly removes entry first. | |

**User's choice:** Silent re-resolve

---

### Q2: When re-resolving after stale hit — find-or-create or always create new?

| Option | Description | Selected |
|--------|-------------|----------|
| Find-or-create | Check Drive for existing folder before creating. Prevents duplicates. | ✓ |
| Create new | Always create fresh folder. Risks duplicates. | |

**User's choice:** Find-or-create

---

### Q3: How to detect a stale cache hit?

| Option | Description | Selected |
|--------|-------------|----------|
| Validate on first use | Use cached ID directly; catch Drive 404/error on actual use. No extra API call on happy path. | ✓ |
| Pre-validate cache entries | Metadata GET before every use. Extra Drive call on every cache hit. | |
| Never validate — trust the cache | Assume always valid. Simple but brittle. | |

**User's choice:** Validate on first use

---

## Claude's Discretion

- Internal helper naming
- Whether to expose `invalidate(site_name)` helper
- Cache key normalization (defaulting to as-is, matching upstream normalization pattern)
- `SiteStructureStore` coexistence (defaulting to independent coexistence — no Telegram bot changes)

## Deferred Ideas

- Fuzzy/partial site name matching — v2
- Admin cache invalidation endpoint — v2
- FMAS unit folder caching — v2
- Env var configurable cache path — v1 scope excluded
