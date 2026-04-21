"""FMAS site membership check — loaded once from data/fmas_sites.txt."""

import difflib
import os
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "fmas_sites.txt")
_SITES_PATH = os.getenv("FMAS_SITES_PATH", _DEFAULT_PATH)

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_FMAS_SITES: set[str] = set()
_FMAS_SITES_ORIGINAL: list[str] = []


def _load_sites(path: str) -> tuple[set[str], list[str]]:
    """
    Read site names from path, one per line.

    Returns (lowercase_set, original_case_list).
    If the file is missing, logs a warning and returns empty collections.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            originals = [line.strip() for line in fh if line.strip()]
        return {s.lower() for s in originals}, originals
    except FileNotFoundError:
        logger.warning(
            "FMAS sites file not found: %s — all sites will route to Direct ATEC",
            path,
        )
        return set(), []


# Load at import time so the set is populated on first import.
_FMAS_SITES, _FMAS_SITES_ORIGINAL = _load_sites(_SITES_PATH)
logger.info("Loaded %d FMAS site names from %s", len(_FMAS_SITES), _SITES_PATH)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_fmas_site(site_name: str, cutoff: float = 0.8) -> str | None:
    """
    Return the canonical FMAS site name for site_name, or None if no match.

    Tries exact (case-insensitive) first, then fuzzy-matches using difflib
    at the given cutoff. Returns original-case canonical name on match.
    """
    normalized = site_name.strip().lower()
    for original in _FMAS_SITES_ORIGINAL:
        if original.strip().lower() == normalized:
            return original
    lower_list = [s.strip().lower() for s in _FMAS_SITES_ORIGINAL]
    matches = difflib.get_close_matches(normalized, lower_list, n=1, cutoff=cutoff)
    if matches:
        return _FMAS_SITES_ORIGINAL[lower_list.index(matches[0])]
    return None


def is_fmas_site(site_name: str) -> bool:
    """
    Return True if site_name matches an entry in the FMAS sites list.

    Accepts exact (case-insensitive) and fuzzy matches above the default cutoff.
    """
    return resolve_fmas_site(site_name) is not None


def reload(path: str | None = None) -> int:
    """Reload the site list (useful for tests). Returns count of sites loaded."""
    global _FMAS_SITES, _FMAS_SITES_ORIGINAL
    _FMAS_SITES, _FMAS_SITES_ORIGINAL = _load_sites(path or _SITES_PATH)
    return len(_FMAS_SITES)
