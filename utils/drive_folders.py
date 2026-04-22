"""
Drive folder helpers for install photo organisation.

Folder paths:
  FMAS site:   Sites → FMAS → [Site Name] → Unit [Unit Number]   (automated)
  ATEC site:   Sites → [Site Name] → <user browses to destination> (interactive)
"""

from __future__ import annotations

import difflib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"


def list_subfolders(service, folder_id, drive_id):
    """Return sorted list of {id, name} for all subfolders in folder_id."""
    results = service.files().list(
        q=(
            f"mimeType='{FOLDER_MIME}' and "
            f"'{folder_id}' in parents and "
            f"trashed=false"
        ),
        corpora="drive",
        driveId=drive_id,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id, name)",
        orderBy="name",
    ).execute()
    return results.get("files", [])


def get_atec_site_folder(service, drive_id, site_name):
    """
    Find or create Sites/[site_name] for a direct ATEC site.
    Returns (folder_id, created).
    Does NOT create anything deeper — user browses from here.

    Uses a local JSON cache (`data/atec_folder_cache.json`) keyed by
    site_name. On cache hit, returns `(cached_id, False)` without any
    Drive API call. On cache miss, resolves via find-or-create then
    writes the ID to cache before returning.

    Stale recovery: callers that hit a "folder not found" error when
    using the returned ID should call `_get_cache().delete(site_name)`
    and retry — the retry will fall through to the miss path and
    repopulate the cache with a fresh ID.
    """
    cache = _get_cache()
    cached_id = cache.get(site_name)
    if cached_id:
        return cached_id, False  # cache hit — no Drive call

    # Cache miss: find-or-create, then write to cache.
    sites_id = _find_folder_exact(service, "Sites", drive_id, drive_id)
    if not sites_id:
        raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")

    # Fuzzy-match against existing Sites/ subfolders before creating.
    canonical = _fuzzy_match_subfolder(service, drive_id, sites_id, site_name)
    if canonical and canonical != site_name:
        logger.info(
            "Site name '%s' fuzzy-matched to existing folder '%s'", site_name, canonical
        )
        cached_id = cache.get(canonical)
        if cached_id:
            cache.set(site_name, cached_id)
            return cached_id, False
        site_name = canonical

    folder_id, created = _find_or_create_folder(
        service, site_name, sites_id, drive_id
    )
    cache.set(site_name, folder_id)
    return folder_id, created


def _fuzzy_match_subfolder(
    service, drive_id, parent_id: str, name: str, cutoff: float = 0.8
) -> str | None:
    """
    Return the name of the closest existing subfolder under parent_id, or None.

    Uses difflib with the given cutoff. Returns None when no folder is close
    enough, allowing the caller to proceed with find-or-create as normal.
    """
    subfolders = list_subfolders(service, parent_id, drive_id)
    if not subfolders:
        return None
    folder_names = [f["name"] for f in subfolders]
    lower_names = [n.lower() for n in folder_names]
    normalized = name.strip().lower()
    matches = difflib.get_close_matches(normalized, lower_names, n=1, cutoff=cutoff)
    if matches:
        return folder_names[lower_names.index(matches[0])]
    return None


def _find_or_create_folder(service, name, parent_id, drive_id):
    """
    Return (folder_id, created) for a folder with exact name under parent_id.
    Creates it if it doesn't exist. Never duplicates.
    """
    results = service.files().list(
        q=(
            f"mimeType='{FOLDER_MIME}' and "
            f"name='{name}' and "
            f"'{parent_id}' in parents and "
            f"trashed=false"
        ),
        corpora="drive",
        driveId=drive_id,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id, name)",
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"], False

    folder = service.files().create(
        body={
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        },
        supportsAllDrives=True,
        fields="id",
    ).execute()
    return folder["id"], True


def _find_folder_exact(service, name, parent_id, drive_id):
    """Return folder id or None — never creates."""
    results = service.files().list(
        q=(
            f"mimeType='{FOLDER_MIME}' and "
            f"name='{name}' and "
            f"'{parent_id}' in parents and "
            f"trashed=false"
        ),
        corpora="drive",
        driveId=drive_id,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id, name)",
    ).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _format_unit_name(unit_number: str) -> str:
    """
    Always return 'Unit X' format.
    Strips any existing 'Unit ' prefix first so we never get 'Unit Unit X'.
    """
    stripped = unit_number.strip()
    if stripped.lower().startswith("unit "):
        stripped = stripped[5:].strip()
    return f"Unit {stripped}"


def get_unit_folder(service, drive_id, site_name, unit_number, is_fmas):
    """
    Find or create the unit folder and return its (folder_id, folder_url).

    Folder name is always formatted as 'Unit [unit_number]'.

    is_fmas=True  → Sites/FMAS/[site_name]/Unit [unit_number]
    is_fmas=False → Sites/[site_name]/Unit [unit_number]
    """
    unit_folder_name = _format_unit_name(unit_number)

    # Sites (always exists)
    sites_id = _find_folder_exact(service, "Sites", drive_id, drive_id)
    if not sites_id:
        raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")

    if is_fmas:
        fmas_id = _find_folder_exact(service, "FMAS", sites_id, drive_id)
        if not fmas_id:
            raise FileNotFoundError("'FMAS' folder not found inside 'Sites'.")
        parent_id = fmas_id
    else:
        parent_id = sites_id

    canonical = _fuzzy_match_subfolder(service, drive_id, parent_id, site_name)
    if canonical and canonical != site_name:
        logger.info(
            "Site name '%s' fuzzy-matched to existing folder '%s'", site_name, canonical
        )
        site_name = canonical

    site_id, site_created = _find_or_create_folder(service, site_name, parent_id, drive_id)
    unit_id, unit_created = _find_or_create_folder(service, unit_folder_name, site_id, drive_id)

    url = f"https://drive.google.com/drive/folders/{unit_id}"
    return unit_id, url, site_created, unit_created


# ---------------------------------------------------------------------------
# Direct ATEC folder ID cache
# ---------------------------------------------------------------------------


class _AtecFolderCache:
    """
    Flat `{site_name: folder_id}` cache persisted to JSON on disk.

    Used exclusively by `get_atec_site_folder()` to skip the Drive API
    traversal on repeat bookouts for the same Direct ATEC site.

    - File path: `data/atec_folder_cache.json` by default.
    - Auto-created on first write if the file/dir doesn't exist.
    - Atomic writes via `.tmp` + `os.replace()` — survives crash/interrupt.
    - Corrupt JSON is silently discarded — cache starts empty.
    - Keys are stored as-is (no internal casing transform — D-09).
    """

    DEFAULT_PATH = "data/atec_folder_cache.json"

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self._data: dict[str, str] = {}
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                self._data = json.load(f)
                if not isinstance(self._data, dict):
                    raise ValueError("top-level JSON is not an object")
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning(
                "Folder ID cache at %s is corrupt (%s) — starting empty",
                self.path, e,
            )
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    # ---- queries ----
    def get(self, site_name: str) -> str | None:
        return self._data.get(site_name)

    # ---- mutations ----
    def set(self, site_name: str, folder_id: str) -> None:
        self._data[site_name] = folder_id
        self._save()

    def delete(self, site_name: str) -> None:
        if site_name in self._data:
            del self._data[site_name]
            self._save()


_CACHE: "_AtecFolderCache | None" = None


def _get_cache() -> "_AtecFolderCache":
    global _CACHE
    if _CACHE is None:
        _CACHE = _AtecFolderCache()
    return _CACHE
