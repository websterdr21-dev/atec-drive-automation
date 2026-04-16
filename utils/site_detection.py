"""FMAS site membership check — loaded once from data/fmas_sites.txt."""

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


def _load_sites(path: str) -> set[str]:
    """
    Read site names from path, one per line.

    Returns a set of normalised (lowercased, stripped) names.
    If the file is missing, logs a warning and returns an empty set.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return {line.strip().lower() for line in fh if line.strip()}
    except FileNotFoundError:
        logger.warning(
            "FMAS sites file not found: %s — all sites will route to Direct ATEC",
            path,
        )
        return set()


# Load at import time so the set is populated on first import.
_FMAS_SITES = _load_sites(_SITES_PATH)
logger.info("Loaded %d FMAS site names from %s", len(_FMAS_SITES), _SITES_PATH)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_fmas_site(site_name: str) -> bool:
    """
    Return True if site_name matches an entry in the FMAS sites list.

    Comparison is case-insensitive with leading/trailing whitespace stripped.
    """
    return site_name.strip().lower() in _FMAS_SITES


def reload(path: str | None = None) -> int:
    """Reload the site list (useful for tests). Returns count of sites loaded."""
    global _FMAS_SITES
    _FMAS_SITES = _load_sites(path or _SITES_PATH)
    return len(_FMAS_SITES)
