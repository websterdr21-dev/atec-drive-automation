"""
State for the Telegram bot.

Two concerns live in this module:

1. `StateManager` — per-chat-id in-memory conversation state. Not persistent
   across restarts (single-instance deploy only). A Redis-backed replacement
   would plug in here; see the stub at the bottom of the file.

2. `SiteStructureStore` — persistent learned folder structures for direct
   ATEC sites. This is the ONLY file the bot writes to disk.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

EXPIRY_SECONDS = 30 * 60  # 30 minutes


# ---------------------------------------------------------------------------
# Per-chat state
# ---------------------------------------------------------------------------

STEP_COLLECTING        = "collecting"
STEP_PARSING           = "parsing"
STEP_SWAP_CONFIRM      = "swap_confirm"
STEP_SERIAL_CORRECTION = "serial_correction"  # waiting for user to correct extracted serial
STEP_TYPE_SELECT       = "type_select"        # waiting for user to confirm FMAS vs ATEC
STEP_NAV               = "nav"
STEP_UPLOADING         = "uploading"
STEP_DONE              = "done"


def new_bookout_state() -> dict:
    """Return a fresh state dict for the one-shot bookout flow."""
    return {
        "step": STEP_COLLECTING,
        "ticket_text": "",
        "client_details": {},
        "items": [],                    # list of {serial, item_code, is_swap}
        "is_swap": False,               # True only if every items[] entry is a swap
        "pending_photos": [],           # buffered Telegram File / file_id references
        "media_group_id": None,
        "folder_id": "",
        "folder_url": "",
        "failed_step": None,
        # ATEC interactive navigation
        "atec_nav_path": [],            # folder IDs accumulated during nav
        "atec_nav_current_id": "",
        "atec_nav_breadcrumb": [],      # human-readable segments
        # bookkeeping
        "site_name": "",
        "unit_number": "",
        "is_fmas": None,
        "last_activity": time.time(),
    }


# Backwards-compatibility for the legacy multi-step flow: still exported so
# imports in older code don't break, but new code should use new_bookout_state.
def new_state(flow: str = "bookout", step: str = STEP_COLLECTING) -> dict:
    s = new_bookout_state()
    s["flow"] = flow
    s["step"] = step
    return s


class StateManager:
    """In-memory state store keyed by chat_id."""

    def __init__(self):
        self._store: dict[int, dict] = {}

    def get(self, chat_id: int) -> Optional[dict]:
        s = self._store.get(chat_id)
        if s is None:
            return None
        if time.time() - s.get("last_activity", 0) > EXPIRY_SECONDS:
            del self._store[chat_id]
            return None
        return s

    def set(self, chat_id: int, state: dict) -> None:
        state["last_activity"] = time.time()
        self._store[chat_id] = state

    def clear(self, chat_id: int) -> None:
        self._store.pop(chat_id, None)

    def has(self, chat_id: int) -> bool:
        return self.get(chat_id) is not None


# ---------------------------------------------------------------------------
# ATEC site structure persistence
# ---------------------------------------------------------------------------

DEFAULT_STORE_PATH = "data/atec_site_structures.json"


class SiteStructureStore:
    """
    Persist learned folder structures for direct ATEC sites.

    JSON shape:
        {
          "Sunset Manor": {
            "path_template": ["Residents", "Unit {unit}"],
            "example_resolved": "Sites/Sunset Manor/Residents/Unit 4",
            "folder_id_cache": {"Residents": "1AbC..."},
            "learned_at": "2025-04-15",
            "learned_by": 123456789
          }
        }

    The `{unit}` token in a path segment is substituted with the actual unit
    number at runtime. Only one token is supported.
    """

    UNIT_TOKEN = "{unit}"

    def __init__(self, path: str = DEFAULT_STORE_PATH):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
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
                "Site structure store at %s is corrupt (%s) — starting empty",
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
    def has(self, site_name: str) -> bool:
        return site_name in self._data

    def get(self, site_name: str) -> Optional[dict]:
        return self._data.get(site_name)

    def all_sites(self) -> list[str]:
        return sorted(self._data.keys())

    # ---- mutations ----
    def learn(
        self,
        site_name: str,
        segments: list[str],
        unit_number: str,
        folder_id_cache: Optional[dict[str, str]] = None,
        learned_by: Optional[int] = None,
    ) -> dict:
        """
        Record a path for `site_name`. `segments` is the list of folder names
        navigated under `Sites/[site_name]/`, ending with the unit folder.
        The segment matching `unit_number` is substituted with `{unit}` so the
        template generalises across units.
        """
        template = []
        for seg in segments:
            if unit_number and seg == unit_number:
                template.append(self.UNIT_TOKEN)
            elif unit_number and unit_number in seg:
                template.append(seg.replace(unit_number, self.UNIT_TOKEN))
            else:
                template.append(seg)

        resolved_parts = ["Sites", site_name] + segments
        entry = {
            "path_template": template,
            "example_resolved": "/".join(resolved_parts),
            "folder_id_cache": dict(folder_id_cache or {}),
            "learned_at": _dt.date.today().isoformat(),
            "learned_by": learned_by,
        }
        self._data[site_name] = entry
        self._save()
        return entry

    def forget(self, site_name: str) -> bool:
        if site_name not in self._data:
            return False
        del self._data[site_name]
        self._save()
        return True

    def update_folder_id_cache(self, site_name: str, segment: str, folder_id: str) -> None:
        entry = self._data.get(site_name)
        if entry is None:
            return
        entry.setdefault("folder_id_cache", {})[segment] = folder_id
        self._save()

    def invalidate_cache_entry(self, site_name: str, segment: str) -> None:
        entry = self._data.get(site_name)
        if entry is None:
            return
        cache = entry.get("folder_id_cache", {})
        cache.pop(segment, None)
        self._save()

    # ---- runtime resolution ----
    def resolve_template(self, site_name: str, unit_number: str) -> Optional[list[str]]:
        """Return the list of concrete folder names for a given unit, or None."""
        entry = self._data.get(site_name)
        if entry is None:
            return None
        return [
            seg.replace(self.UNIT_TOKEN, unit_number)
            for seg in entry["path_template"]
        ]


# ---------------------------------------------------------------------------
# Redis stub — left intentionally unused. Swap StateManager for this class
# if a multi-instance deploy is ever needed.
# ---------------------------------------------------------------------------
#
# class RedisStateManager:
#     def __init__(self, url: str):
#         import redis
#         self._r = redis.Redis.from_url(url, decode_responses=True)
#
#     def get(self, chat_id: int):
#         raw = self._r.get(f"atec:state:{chat_id}")
#         return json.loads(raw) if raw else None
#
#     def set(self, chat_id: int, state: dict):
#         state["last_activity"] = time.time()
#         self._r.set(f"atec:state:{chat_id}", json.dumps(state), ex=EXPIRY_SECONDS)
#
#     def clear(self, chat_id: int):
#         self._r.delete(f"atec:state:{chat_id}")
