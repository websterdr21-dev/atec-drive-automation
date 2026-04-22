"""
ATEC Stock Bookout — Telegram bot (one-shot flow).

A technician sends ONE message with the ticket text as caption + photos
(serial label(s), optional device shot, optional post-install photos).
The bot extracts everything, updates the stock sheet, creates / opens the
Drive folder, uploads photos with the ATEC naming convention, and replies
with a single success message (with accounts-email copy block).

Interaction is kept to a minimum:
  - Silent drop for non-whitelisted users.
  - Per-item swap-mode confirmation if any serial isn't in the sheets.
  - Guided folder navigation for a direct ATEC site the bot hasn't seen.
    Structure is saved to disk and replayed on subsequent bookouts.

Only `telegram_bot.py` and `telegram_state.py` are modified. All Google API
calls flow through existing helpers in `utils/`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from utils.site_detection import is_fmas_site, resolve_fmas_site
from utils.telegram_state import (
    STEP_COLLECTING,
    STEP_NAV,
    STEP_SERIAL_CORRECTION,
    STEP_SWAP_CONFIRM,
    STEP_TYPE_SELECT,
    SiteStructureStore,
    StateManager,
    new_bookout_state,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

STATE = StateManager()
SITES = SiteStructureStore()

# Media-group buffer — maps media_group_id -> list of pending photo dicts.
# Populated by the message handler and drained by the buffer task.
_MEDIA_GROUPS: dict[str, dict] = {}
_MEDIA_GROUPS_LOCK = asyncio.Lock()
_PROCESSED_GROUP_IDS: set[str] = set()

MEDIA_GROUP_WAIT_SECONDS = 2.5

_HELP_TEXT = (
    "ATEC Bookout Bot\n\n"
    "Send the ticket text as the caption on your serial-label photo(s). "
    "Include any device / ONT / install / speed-test photos in the same "
    "media group and the bot will file them for you.\n\n"
    "Commands:\n"
    "  /checkstock <serial>  — look up a serial\n"
    "  /cancel               — clear any in-progress state\n"
    "  /start                — show this message\n\n"
    "Other messages:\n"
    "  add photos <Site> <Unit>  — append post-install photos to an existing unit\n"
    "  relearn <Site>            — clear saved folder structure for a site"
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _allowed_user_ids() -> set[int]:
    # Spec uses ALLOWED_USER_IDS; legacy .env used TELEGRAM_ALLOWED_USERS.
    raw = os.getenv("ALLOWED_USER_IDS") or os.getenv("TELEGRAM_ALLOWED_USERS") or ""
    raw = raw.strip()
    if not raw:
        return set()
    try:
        return {int(x.strip()) for x in raw.split(",") if x.strip()}
    except ValueError:
        logger.warning("ALLOWED_USER_IDS is not a comma-separated integer list")
        return set()


def is_allowed(user_id: int) -> bool:
    allowed = _allowed_user_ids()
    # If the allow-list is empty, treat that as "nobody" — fail closed.
    # This deviates from the legacy "empty = everyone" behaviour but matches
    # the new spec, where a non-whitelisted user is silently dropped.
    return user_id in allowed


# ---------------------------------------------------------------------------
# Drive service helpers
# ---------------------------------------------------------------------------

def _get_drive():
    from utils.auth import get_drive_service
    return get_drive_service()


def _drive_id() -> str:
    return os.getenv("SHARED_DRIVE_ID", "")


# ---------------------------------------------------------------------------
# Pure helpers — no Telegram, no I/O. All are unit-testable in isolation.
# ---------------------------------------------------------------------------

def classify_photo_names(extractions: list[dict]) -> list[tuple[str, str]]:
    """
    Given the ordered list of vision-extraction results (one per photo in the
    media group), return a list of (role, filename) pairs matching the ATEC
    naming convention:

        Serial label photos (N at the start, while extraction returned a
        serial)       → 01_Serial_Number.jpg (single) or _01.jpg, _02, … (multiple)
        Next photo    → 04_Device_Photo.jpg
        Following     → 02_ONT_Router_Placement.jpg
        Following     → 05_Speed_Test.jpg
        Remaining     → 03_Installation_01.jpg, _02, …

    `extractions[i]` is the dict returned by extract_serial_from_photo, or
    an empty dict / one with serial_number == None for non-label photos.

    Role strings: "serial", "device", "ont", "speed", "install".
    """
    # Split into leading labels + remainder
    n_labels = 0
    for e in extractions:
        if e and e.get("serial_number"):
            n_labels += 1
        else:
            break

    names: list[tuple[str, str]] = []

    if n_labels == 1:
        names.append(("serial", "01_Serial_Number.jpg"))
    else:
        for i in range(n_labels):
            names.append(("serial", f"01_Serial_Number_{i + 1:02d}.jpg"))

    positional = ["device", "ont", "speed"]
    positional_filenames = {
        "device": "04_Device_Photo.jpg",
        "ont": "02_ONT_Router_Placement.jpg",
        "speed": "05_Speed_Test.jpg",
    }

    remaining = list(range(n_labels, len(extractions)))
    for role in positional:
        if not remaining:
            break
        remaining.pop(0)
        names.append((role, positional_filenames[role]))

    install_idx = 1
    for _ in remaining:
        names.append(("install", f"03_Installation_{install_idx:02d}.jpg"))
        install_idx += 1

    return names


def collect_items_from_extractions(extractions: list[dict]) -> list[dict]:
    """Extract the leading label photos as items."""
    items: list[dict] = []
    for e in extractions:
        if not e or not e.get("serial_number"):
            break
        items.append({
            "serial":    e["serial_number"],
            "item_code": e.get("item_code") or "",
            "is_swap":   False,  # filled in after sheet search
        })
    return items


def mark_swaps(items: list[dict], stock_results: list[Optional[dict]]) -> None:
    """
    Mutate each item in-place to set `is_swap`. `stock_results[i]` is the
    `find_serial_number` result for items[i] (or None).
    """
    for item, result in zip(items, stock_results):
        item["is_swap"] = result is None


def all_swaps(items: list[dict]) -> bool:
    return bool(items) and all(it["is_swap"] for it in items)


# --- nav helpers -----------------------------------------------------------

def apply_nav_choice(
    state: dict,
    choice: str,
    subfolders: list[dict],
) -> dict:
    """
    Update `state` based on a nav reply. `subfolders` is the current listing
    at state["atec_nav_current_id"].

    Returns a small result dict:
        {"action": "descend"|"up"|"select"|"invalid", "message": str}

    - "descend"  → state now points at a chosen subfolder
    - "up"       → popped one level
    - "select"   → user pressed 'u' — state's current folder is final
    - "invalid"  → message explains the issue
    """
    choice = (choice or "").strip().lower()

    if choice == "u":
        return {"action": "select", "message": ""}

    if choice == "b":
        if len(state["atec_nav_path"]) <= 1:
            return {"action": "invalid", "message": "Already at the top."}
        state["atec_nav_path"].pop()
        state["atec_nav_breadcrumb"].pop()
        state["atec_nav_current_id"] = state["atec_nav_path"][-1]
        return {"action": "up", "message": ""}

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(subfolders):
            chosen = subfolders[idx]
            state["atec_nav_path"].append(chosen["id"])
            state["atec_nav_breadcrumb"].append(chosen["name"])
            state["atec_nav_current_id"] = chosen["id"]
            return {"action": "descend", "message": ""}
        return {"action": "invalid",
                "message": f"Enter a number between 1 and {len(subfolders)}."}

    return {"action": "invalid", "message": "Reply with a number, 'b' (back), or 'u' (upload here)."}


def build_nav_reply(
    site_name: str,
    breadcrumb: list[str],
    subfolders: list[dict],
) -> str:
    header = f"New site: {site_name}\n" if len(breadcrumb) == 1 else ""
    path_line = "Path: " + " / ".join(breadcrumb)
    if subfolders:
        body = "\n".join(
            f"  {i + 1}. {f['name']}" for i, f in enumerate(subfolders)
        )
    else:
        body = "  (no subfolders)"
    footer = "Reply with a number to open, 'u' to upload here, 'b' to go back."
    return f"{header}{path_line}\n\nSubfolders:\n{body}\n\n{footer}"


# --- reply formatters ------------------------------------------------------

def format_swap_warning(swap_items: list[dict]) -> str:
    lines = ["The following serials were not found in any stock sheet:"]
    for it in swap_items:
        lines.append(f"  - {it['serial']} — {it['item_code'] or '(no item code)'}")
    lines.append("")
    lines.append(
        "These will be treated as swaps — sheet update and email skipped for these items."
    )
    lines.append("Reply /cancel to abort or any message to continue.")
    return "\n".join(lines)


def format_serial_correction_prompt(serial: str, item_code: str | None) -> str:
    """Message shown when a serial is not found — asks user to correct or confirm swap."""
    code_str = f" ({item_code})" if item_code else ""
    return (
        f"Serial `{serial}`{code_str} was not found in any stock sheet.\n\n"
        "Reply with the correct serial number to retry, or reply *swap* to skip the sheet update for this item."
    )


def format_error(step: str, reason: str, hint: str = "") -> str:
    out = f"{step} failed — {reason}"
    if hint:
        out += f"\n{hint}"
    return out


def format_success(
    client_details: dict,
    items: list[dict],
    folder_url: str,
    photo_count: int,
    email_text: Optional[str],
) -> str:
    unit = client_details.get("unit_number", "?")
    site = client_details.get("site_name", "?")
    name = client_details.get("full_name", "?")
    isp = client_details.get("isp", "")
    speed = client_details.get("speed", "")

    lines = [f"Booked out — {name} — Unit {unit} — {site}", "", "Items:"]
    for it in items:
        badge = "Swap (not updated)" if it["is_swap"] else "Sheet updated + highlighted red"
        code = it["item_code"] or "(no item code)"
        lines.append(f"  - {code} — S/N {it['serial']} — {badge}")
    lines += [
        "",
        f"Drive: {folder_url}",
        f"Photos: {photo_count} uploaded",
        f"ISP: {isp} | Speed: {speed}",
    ]
    if email_text:
        lines += ["", "--- ACCOUNTS EMAIL (copy below) ---", email_text]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def _download_photo(bot: Bot, file_id: str) -> str:
    tg_file = await bot.get_file(file_id)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    tmp.close()
    await tg_file.download_to_drive(tmp.name)
    return tmp.name


def _cleanup_paths(paths: list[str]) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Folder resolution — FMAS automated + ATEC learned-structure replay
# ---------------------------------------------------------------------------

def resolve_fmas_folder(service, site_name: str, unit_number: str) -> tuple[str, str]:
    from utils.drive_folders import get_unit_folder
    folder_id, folder_url, _, _ = get_unit_folder(
        service, _drive_id(), site_name, unit_number, is_fmas=True
    )
    return folder_id, folder_url


def resolve_atec_folder_from_template(
    service,
    site_name: str,
    unit_number: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Try to resolve the saved template for a direct ATEC site. Returns
    (folder_id, folder_url, error). If the site is not known, returns
    (None, None, None). If the template is stale (a segment is missing),
    returns (None, None, "<missing segment>") so the caller can prompt
    for a relearn.
    """
    from utils.drive_folders import _find_or_create_folder, get_atec_site_folder

    entry = SITES.get(site_name)
    if entry is None:
        return None, None, None

    # Start from Sites/[site_name]
    try:
        site_id, _ = get_atec_site_folder(service, _drive_id(), site_name)
    except FileNotFoundError as e:
        return None, None, str(e)

    current_id = site_id
    cache = entry.get("folder_id_cache", {})

    for seg in entry["path_template"]:
        concrete = seg.replace(SITES.UNIT_TOKEN, unit_number)
        cached_id = cache.get(seg) if SITES.UNIT_TOKEN not in seg else None

        if cached_id:
            # Try cached id first; if invalid, fall back to lookup.
            try:
                service.files().get(
                    fileId=cached_id,
                    supportsAllDrives=True,
                    fields="id,trashed",
                ).execute()
                current_id = cached_id
                continue
            except Exception:
                SITES.invalidate_cache_entry(site_name, seg)

        # Find-or-create under current parent. The unit segment may not exist
        # yet on first bookout at this unit — create it.
        try:
            child_id, created = _find_or_create_folder(
                service, concrete, current_id, _drive_id()
            )
        except Exception as e:
            return None, None, f"{concrete}: {e}"

        if SITES.UNIT_TOKEN not in seg:
            # Cache static segments.
            SITES.update_folder_id_cache(site_name, seg, child_id)
        current_id = child_id

    url = f"https://drive.google.com/drive/folders/{current_id}"
    return current_id, url, None


# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Single entry point for text + photos. Dispatches based on state."""
    msg = update.effective_message
    if msg is None:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else None
    if user is None or chat_id is None:
        return

    if not is_allowed(user.id):
        logger.debug("Dropping update from non-whitelisted user %s", user.id)
        return

    state = STATE.get(chat_id)
    text = (msg.text or msg.caption or "").strip()

    # ---- Command-ish free-text routes ----
    low = text.lower()
    if low.startswith("add photos"):
        await _handle_add_photos(update, context, text)
        return
    if low.startswith("relearn "):
        await _handle_relearn(update, text)
        return

    # ---- Mid-flow nav reply ----
    if state and state.get("step") == STEP_NAV and not msg.photo:
        await _handle_nav_reply(update, context, state, text)
        return

    # ---- Site-type selection reply (FMAS vs ATEC) ----
    if state and state.get("step") == STEP_TYPE_SELECT and not msg.photo:
        if low == "/cancel":
            STATE.clear(chat_id)
            await msg.reply_text("Cancelled.")
            return
        await _handle_type_select_reply(update, context, state, text)
        return

    # ---- Serial-correction reply ----
    if state and state.get("step") == STEP_SERIAL_CORRECTION and not msg.photo:
        if low == "/cancel":
            STATE.clear(chat_id)
            await msg.reply_text("Cancelled.")
            return
        await _handle_serial_correction(update, context, state, text)
        return

    # ---- Swap-confirm reply ----
    if state and state.get("step") == STEP_SWAP_CONFIRM and not msg.photo:
        if low == "/cancel":
            STATE.clear(chat_id)
            await msg.reply_text("Cancelled.")
            return
        # Any non-cancel message → continue.
        await _continue_after_swap_confirm(update, context, state)
        return

    # ---- New bookout: media group or single message with photos ----
    if msg.photo or (state and state.get("step") == STEP_COLLECTING):
        await _ingest_bookout_message(update, context)
        return

    # Plain text with no state → show help
    if text and not text.startswith("/"):
        await msg.reply_text(_HELP_TEXT)


# ---------------------------------------------------------------------------
# Media-group buffering
# ---------------------------------------------------------------------------

async def _ingest_bookout_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    group_id = msg.media_group_id

    if msg.photo:
        largest = msg.photo[-1]
        photo_ref = {"file_id": largest.file_id}
    else:
        photo_ref = None

    caption_or_text = (msg.caption or msg.text or "").strip()

    if group_id:
        async with _MEDIA_GROUPS_LOCK:
            if group_id in _PROCESSED_GROUP_IDS:
                return  # already flushed
            bucket = _MEDIA_GROUPS.setdefault(group_id, {
                "chat_id": chat_id,
                "photos": [],
                "caption": "",
                "user_id": update.effective_user.id,
                "flushed": False,
            })
            if photo_ref:
                bucket["photos"].append(photo_ref)
            if caption_or_text and not bucket["caption"]:
                bucket["caption"] = caption_or_text
            new_bucket = not bucket.get("task_scheduled")
            bucket["task_scheduled"] = True

        if new_bucket:
            asyncio.create_task(
                _flush_media_group_after_delay(group_id, context),
            )
        return

    # Non-grouped single photo — process immediately as a one-photo group.
    await _process_bookout(
        context=context,
        chat_id=chat_id,
        photos=[photo_ref] if photo_ref else [],
        caption=caption_or_text,
    )


async def _flush_media_group_after_delay(
    group_id: str, context: ContextTypes.DEFAULT_TYPE,
):
    await asyncio.sleep(MEDIA_GROUP_WAIT_SECONDS)
    async with _MEDIA_GROUPS_LOCK:
        bucket = _MEDIA_GROUPS.pop(group_id, None)
        if bucket is None or bucket.get("flushed"):
            return
        bucket["flushed"] = True
        _PROCESSED_GROUP_IDS.add(group_id)

    await _process_bookout(
        context=context,
        chat_id=bucket["chat_id"],
        photos=bucket["photos"],
        caption=bucket["caption"],
    )


# ---------------------------------------------------------------------------
# Core bookout processor
# ---------------------------------------------------------------------------

async def _process_bookout(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    photos: list[dict],
    caption: str,
):
    """Run the one-shot bookout flow for a single message / media group."""
    bot = context.bot

    # Fresh state overrides any stale one.
    state = new_bookout_state()
    STATE.set(chat_id, state)

    # Missing required inputs?
    if not caption:
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error(
            "Parse", "no ticket text found",
            "Send the ticket as the caption on your serial-label photo."))
        return
    if not photos:
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error(
            "Photos", "no photos attached",
            "Attach at least one serial-label photo in the same message."))
        return

    state["ticket_text"] = caption
    state["pending_photos"] = photos

    # ---- 1. Download every photo in parallel ----
    from utils.extract import extract_client_details, extract_serial_from_photo
    try:
        paths: list[str] = list(
            await asyncio.gather(*[_download_photo(bot, p["file_id"]) for p in photos])
        )
    except Exception as e:
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error("Download", str(e)))
        return

    # ---- 2+3. Vision on each photo + ticket extraction in parallel ----
    async def _vision(path: str) -> dict:
        try:
            return await asyncio.to_thread(extract_serial_from_photo, path)
        except Exception as e:
            logger.warning("Vision failed on %s: %s", path, e)
            return {"serial_number": None, "item_code": None}

    vision_tasks = [_vision(p) for p in paths]
    ticket_task = asyncio.to_thread(extract_client_details, caption)

    # return_exceptions=True so a ticket failure doesn't suppress vision results
    results = await asyncio.gather(*vision_tasks, ticket_task, return_exceptions=True)
    *extractions, details_or_exc = results

    # Check vision first — return early before worrying about ticket result
    items = collect_items_from_extractions(extractions)
    if not items:
        _cleanup_paths(paths)
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error(
            "Vision", "no serial label found in any photo",
            "Retake the label shot so the serial is legible and resend."))
        return
    state["items"] = items

    if isinstance(details_or_exc, Exception):
        _cleanup_paths(paths)
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error("Ticket parse", str(details_or_exc)))
        return
    details = details_or_exc

    required = ["full_name", "site_name", "unit_number"]
    missing = [k for k in required if not details.get(k)]
    if missing:
        _cleanup_paths(paths)
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error(
            "Ticket parse", f"missing field(s): {', '.join(missing)}",
            "Resend with those details included."))
        return

    state["client_details"] = details
    state["unit_number"] = details["unit_number"]

    # Auto-correct site name via fuzzy match before any Drive calls.
    raw_site = details["site_name"]
    canonical = resolve_fmas_site(raw_site)
    if canonical and canonical != raw_site:
        details["site_name"] = canonical
        state["client_details"] = details
        await bot.send_message(
            chat_id,
            f"Site name corrected: '{raw_site}' \u2192 '{canonical}'. "
            f"/cancel now if this is wrong.",
        )
    state["site_name"] = details["site_name"]

    # ---- 4. Stock-sheet lookup per item ----
    # Run BEFORE the site-type check so that _tmp_paths, _photo_names, and
    # is_swap are all saved into state before any early return. This ensures
    # _continue_after_swap_confirm has the correct data regardless of whether
    # the user had to answer an FMAS vs ATEC prompt for a new site.
    try:
        _lookup_service = _get_drive()
        from utils.sheets import find_serial_number
        results = []
        for it in items:
            res = await asyncio.to_thread(
                find_serial_number, _lookup_service, _drive_id(), it["serial"]
            )
            results.append(res)
        mark_swaps(items, results)
    except Exception as e:
        _cleanup_paths(paths)
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error("Stock lookup", str(e)))
        return

    swap_items = [it for it in items if it["is_swap"]]
    state["is_swap"] = all_swaps(items)

    # Save photo paths + names into state now so they survive any early return below.
    names = classify_photo_names(extractions)
    state["_tmp_paths"] = paths
    state["_photo_names"] = names

    # ---- 3b. Determine site type (FMAS vs ATEC direct) ----
    state["is_fmas"] = resolve_fmas_site(details["site_name"]) is not None

    if swap_items:
        # Enter serial correction for the first not-found item before falling back to swap.
        first = swap_items[0]
        state["step"] = STEP_SERIAL_CORRECTION
        state["_correction_serial_index"] = next(
            i for i, it in enumerate(state["items"]) if it["serial"] == first["serial"]
        )
        STATE.set(chat_id, state)
        await bot.send_message(
            chat_id,
            format_serial_correction_prompt(first["serial"], first.get("item_code")),
            parse_mode="Markdown",
        )
        return

    await _continue_after_swap_confirm(
        update=None, context=context, state=state, chat_id=chat_id
    )


async def _continue_after_swap_confirm(
    update: Optional[Update],
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
    chat_id: Optional[int] = None,
):
    bot = context.bot
    if chat_id is None and update is not None:
        chat_id = update.effective_chat.id

    paths: list[str] = state.get("_tmp_paths", [])
    names: list[tuple[str, str]] = state.get("_photo_names", [])

    # ---- 5. Non-swap items: update stock row ----
    try:
        from utils.sheets import update_stock_row
        service = _get_drive()
        details = state["client_details"]
        current_account = f"{details.get('unit_number','')} {details.get('site_name','')}".strip()
        for it in state["items"]:
            if not it["is_swap"]:
                await asyncio.to_thread(
                    update_stock_row,
                    service, _drive_id(), it["serial"], current_account,
                )
    except Exception as e:
        _cleanup_paths(paths)
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error("Stock update", str(e)))
        return

    # ---- 6. Resolve Drive folder ----
    try:
        if state["is_fmas"]:
            folder_id, folder_url = await asyncio.to_thread(
                resolve_fmas_folder,
                service, state["site_name"], state["unit_number"],
            )
        else:
            folder_id, folder_url, err = await asyncio.to_thread(
                resolve_atec_folder_from_template,
                service, state["site_name"], state["unit_number"],
            )
            if folder_id is None and err:
                _cleanup_paths(paths)
                STATE.clear(chat_id)
                await bot.send_message(chat_id, format_error(
                    "Drive folder",
                    f"saved structure for '{state['site_name']}' is invalid ({err})",
                    f"Reply \"relearn {state['site_name']}\" to navigate again."))
                return
            if folder_id is None:
                # Unknown site — kick off guided navigation.
                await _start_guided_nav(context, chat_id, state)
                return
    except Exception as e:
        _cleanup_paths(paths)
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error("Drive folder", str(e)))
        return

    state["folder_id"] = folder_id
    state["folder_url"] = folder_url

    await _upload_and_reply(context, chat_id, state)


async def _handle_serial_correction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
    text: str,
):
    """
    Handle the technician's reply during STEP_SERIAL_CORRECTION.

    - "swap" → mark item as swap, check if more not-found items remain, else continue.
    - Any other text → treat as corrected serial, re-run find_serial_number.
      If found: update item, check remaining not-found items, continue.
      If still not found: fall back to STEP_SWAP_CONFIRM.
    """
    bot = context.bot
    chat_id = update.effective_chat.id if update.effective_chat else None

    idx = state.get("_correction_serial_index", 0)
    item = state["items"][idx]

    if text.strip().lower() == "swap":
        # User confirmed swap for this item — check if others still need correction.
        item["is_swap"] = True
        await _advance_serial_correction(update, context, state, chat_id)
        return

    # Treat reply as corrected serial.
    corrected = text.strip()
    try:
        service = _get_drive()
        from utils.sheets import find_serial_number
        result = await asyncio.to_thread(
            find_serial_number, service, _drive_id(), corrected
        )
    except Exception as e:
        await bot.send_message(chat_id, format_error("Stock lookup", str(e)))
        return

    if result is not None:
        # Found — update the item with corrected serial and clear swap flag.
        item["serial"] = corrected
        item["is_swap"] = False
        await bot.send_message(
            chat_id, f"Found `{corrected}` in stock sheets.", parse_mode="Markdown"
        )
        await _advance_serial_correction(update, context, state, chat_id)
    else:
        # Still not found — fall back to swap confirm for all remaining not-found items.
        item["serial"] = corrected  # keep the corrected serial even in swap
        item["is_swap"] = True      # mark as swap so stock update is skipped
        swap_items = [it for it in state["items"] if it["is_swap"]]
        state["step"] = STEP_SWAP_CONFIRM
        STATE.set(chat_id, state)
        await bot.send_message(chat_id, format_swap_warning(swap_items))


async def _advance_serial_correction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
    chat_id: int,
):
    """
    After one item's serial is resolved (corrected or confirmed swap), check if
    more not-found items remain. If yes, prompt for the next one. If no, continue
    the bookout flow.
    """
    bot = context.bot
    STATE.set(chat_id, state)

    # Find next item that was not found in stock (is_swap still True from mark_swaps,
    # and hasn't been corrected yet — i.e. index is beyond the current one).
    next_swap = next(
        (
            (i, it) for i, it in enumerate(state["items"])
            if it["is_swap"]
            and i > state.get("_correction_serial_index", 0)
        ),
        None,
    )

    if next_swap:
        next_idx, next_item = next_swap
        state["_correction_serial_index"] = next_idx
        state["step"] = STEP_SERIAL_CORRECTION
        STATE.set(chat_id, state)
        await bot.send_message(
            chat_id,
            format_serial_correction_prompt(next_item["serial"], next_item.get("item_code")),
            parse_mode="Markdown",
        )
        return

    # All items resolved — continue bookout.
    state["is_swap"] = all(it["is_swap"] for it in state["items"])
    state.pop("_correction_serial_index", None)
    STATE.set(chat_id, state)
    await _continue_after_swap_confirm(
        update=update, context=context, state=state, chat_id=chat_id
    )


async def _handle_type_select_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
    text: str,
):
    """
    Handle the technician's 1 / 2 reply when we could not determine site type
    from Drive (new site).  Sets is_fmas on state then resumes the flow.
    """
    bot = context.bot
    chat_id = update.effective_chat.id

    choice = text.strip()
    if choice == "1":
        state["is_fmas"] = True
    elif choice == "2":
        state["is_fmas"] = False
    else:
        await bot.send_message(
            chat_id,
            f"Please reply 1 (FMAS) or 2 (ATEC direct), or /cancel to abort.",
        )
        return

    STATE.set(chat_id, state)
    await _continue_after_swap_confirm(
        update=update, context=context, state=state, chat_id=chat_id
    )


def _strip_numeric_suffix(base: str) -> str:
    """Remove a trailing _01 / _02 etc. from a filename stem.

    Used so conflict-resolution always indexes from the canonical base
    (e.g. ``01_Serial_Number``) rather than the per-message name
    (e.g. ``01_Serial_Number_01``), preventing double-indexed filenames
    like ``01_Serial_Number_01_02.jpg``.
    """
    return re.sub(r"_\d{2}$", "", base)


async def _upload_and_reply(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    state: dict,
):
    bot = context.bot
    paths: list[str] = state.get("_tmp_paths", [])
    names: list[tuple[str, str]] = state.get("_photo_names", [])
    folder_id = state["folder_id"]

    # ---- 7. Upload photos ----
    from utils.photos import upload_photo, list_existing_filenames, _next_index
    try:
        service = _get_drive()
        existing = await asyncio.to_thread(
            list_existing_filenames, service, folder_id, _drive_id()
        )
        uploaded_count = 0
        for (role, want_name), local_path in zip(names, paths):
            # Let photos.py's conflict suffixing run if needed.
            # Strip trailing _NN so _next_index indexes from the canonical
            # base (e.g. "01_Serial_Number") not the per-message name
            # (e.g. "01_Serial_Number_01"), avoiding "01_Serial_Number_01_02".
            base = _strip_numeric_suffix(want_name.rsplit(".", 1)[0])
            final = want_name if want_name not in existing else _next_index(existing, base)
            await asyncio.to_thread(
                upload_photo, service, folder_id, local_path, final,
            )
            existing.add(final)
            uploaded_count += 1
    except Exception as e:
        _cleanup_paths(paths)
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error("Photo upload", str(e)))
        return

    # ---- 8. Email (only non-swap items) ----
    non_swap = [it for it in state["items"] if not it["is_swap"]]
    email_text = None
    if non_swap:
        from utils.gmail import format_bookout_email
        details = dict(state["client_details"])
        # The email helper expects single serial/item. If multiple non-swap
        # items exist, join them — keeps the helper unchanged.
        details["serial_number"] = ", ".join(it["serial"] for it in non_swap)
        details["item_code"] = ", ".join(
            (it["item_code"] or "?") for it in non_swap
        )
        details["is_fmas"] = bool(state["is_fmas"])
        email_text = format_bookout_email(details)

    _cleanup_paths(paths)
    reply = format_success(
        state["client_details"], state["items"],
        state["folder_url"], uploaded_count, email_text,
    )
    STATE.clear(chat_id)
    await bot.send_message(chat_id, reply)


# ---------------------------------------------------------------------------
# Guided nav for unknown ATEC sites
# ---------------------------------------------------------------------------

async def _start_guided_nav(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    state: dict,
):
    bot = context.bot
    from utils.drive_folders import get_atec_site_folder, list_subfolders

    service = _get_drive()
    try:
        site_id, _ = await asyncio.to_thread(
            get_atec_site_folder, service, _drive_id(), state["site_name"],
        )
        subs = await asyncio.to_thread(
            list_subfolders, service, site_id, _drive_id(),
        )
    except Exception as e:
        _cleanup_paths(state.get("_tmp_paths", []))
        STATE.clear(chat_id)
        await bot.send_message(chat_id, format_error("Drive folder", str(e)))
        return

    state["step"] = STEP_NAV
    state["atec_nav_path"] = [site_id]
    state["atec_nav_current_id"] = site_id
    state["atec_nav_breadcrumb"] = [state["site_name"]]
    STATE.set(chat_id, state)

    await bot.send_message(
        chat_id,
        build_nav_reply(state["site_name"], state["atec_nav_breadcrumb"], subs),
    )


async def _handle_nav_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
    text: str,
):
    bot = context.bot
    chat_id = update.effective_chat.id
    from utils.drive_folders import list_subfolders

    service = _get_drive()
    subs = await asyncio.to_thread(
        list_subfolders, service, state["atec_nav_current_id"], _drive_id(),
    )

    result = apply_nav_choice(state, text, subs)

    if result["action"] == "invalid":
        await update.effective_message.reply_text(result["message"])
        return

    if result["action"] == "select":
        # Learn the structure. Strip the leading site_name entry from
        # breadcrumb to get just the segments below Sites/[site_name]/.
        segments = state["atec_nav_breadcrumb"][1:]
        if not segments:
            # User pressed 'u' at the site root — auto-create Unit [N] subfolder
            # instead of landing photos in the site root.
            from utils.drive_folders import _find_or_create_folder
            unit_name = f"Unit {state['unit_number']}"
            try:
                unit_id, _ = await asyncio.to_thread(
                    _find_or_create_folder,
                    service, unit_name, state["atec_nav_current_id"], _drive_id(),
                )
            except Exception as e:
                _cleanup_paths(state.get("_tmp_paths", []))
                STATE.clear(chat_id)
                await bot.send_message(
                    chat_id, format_error("Drive folder", f"Could not create {unit_name}: {e}")
                )
                return
            folder_id = unit_id
            SITES.learn(
                state["site_name"], [unit_name], state["unit_number"],
                learned_by=update.effective_user.id if update.effective_user else None,
            )
        else:
            folder_id = state["atec_nav_current_id"]
            SITES.learn(
                state["site_name"], segments, state["unit_number"],
                learned_by=update.effective_user.id if update.effective_user else None,
            )

        state["folder_id"] = folder_id
        state["folder_url"] = f"https://drive.google.com/drive/folders/{folder_id}"
        STATE.set(chat_id, state)

        sub_segments = state["atec_nav_breadcrumb"][1:] or [f"Unit {state['unit_number']} (auto-created)"]
        template_str = " / ".join(sub_segments)
        await bot.send_message(
            chat_id,
            f"Structure saved for {state['site_name']}: {template_str}\n"
            f"Future bookouts will use this path automatically.",
        )
        await _upload_and_reply(context, chat_id, state)
        return

    # descend / up → render the new listing
    new_subs = await asyncio.to_thread(
        list_subfolders, service, state["atec_nav_current_id"], _drive_id(),
    )
    STATE.set(chat_id, state)
    await bot.send_message(
        chat_id,
        build_nav_reply(state["site_name"], state["atec_nav_breadcrumb"], new_subs),
    )


# ---------------------------------------------------------------------------
# add photos / relearn / /checkstock / /cancel / /start
# ---------------------------------------------------------------------------

_ADD_PHOTOS_RE = re.compile(r"^add\s+photos\s+(.+?)\s+(\S+)\s*$", re.IGNORECASE)


async def _handle_add_photos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
):
    """
    Support two forms:
      - `add photos <Site> <Unit>` as a *caption* on a media group — attach
        post-install photos right then and there.
      - `add photos <Site> <Unit>` as a plain text message — reply with a
        note asking the user to send it as a caption on the photos.
    """
    msg = update.effective_message
    chat_id = update.effective_chat.id
    bot = context.bot

    m = _ADD_PHOTOS_RE.match(text)
    if not m:
        await msg.reply_text(
            "Usage: add photos <Site Name> <Unit Number>  (as caption on the photos)"
        )
        return

    site = m.group(1).strip()
    unit = m.group(2).strip()

    if not msg.photo and not msg.media_group_id:
        await msg.reply_text(
            f"Now send the photos as a media group with caption "
            f"\"add photos {site} {unit}\"."
        )
        return

    # Resolve folder — determine FMAS vs ATEC via site list.
    service = _get_drive()
    is_fmas = resolve_fmas_site(site) is not None
    try:
        if is_fmas:
            folder_id, folder_url = await asyncio.to_thread(
                resolve_fmas_folder, service, site, unit,
            )
        else:
            folder_id, folder_url, err = await asyncio.to_thread(
                resolve_atec_folder_from_template, service, site, unit,
            )
            if folder_id is None:
                await msg.reply_text(format_error(
                    "Add photos", err or f"no saved structure for '{site}'",
                    f"Do a bookout on this site first, or reply \"relearn {site}\"."))
                return
    except Exception as e:
        await msg.reply_text(format_error("Drive folder", str(e)))
        return

    # Download + classify + upload. Treat every photo as non-label — the
    # classifier will start at "device" position and walk forward.
    photo_ref = {"file_id": msg.photo[-1].file_id} if msg.photo else None
    if photo_ref is None:
        await msg.reply_text("No photo attached.")
        return

    path = await _download_photo(bot, photo_ref["file_id"])
    try:
        # Use the classifier in its "all non-label" mode: empty extractions
        # list of length 1 → single photo gets assigned device/ont/speed/install
        # based on position. For a one-shot "add photos" we treat the first
        # photo as ONT_placement, since that's the usual first post-install shot.
        from utils.photos import upload_photo, list_existing_filenames, _next_index, _next_install_index

        existing = await asyncio.to_thread(
            list_existing_filenames, service, folder_id, _drive_id(),
        )

        # Simple assignment: if no 02_ONT yet, this is ONT; else next install.
        if "02_ONT_Router_Placement.jpg" not in existing:
            name = "02_ONT_Router_Placement.jpg"
        elif "05_Speed_Test.jpg" not in existing:
            name = "05_Speed_Test.jpg"
        else:
            idx = _next_install_index(existing)
            name = f"03_Installation_{idx:02d}.jpg"

        if name in existing:
            name = _next_index(existing, name.rsplit(".", 1)[0])

        await asyncio.to_thread(upload_photo, service, folder_id, path, name)
        await msg.reply_text(f"Uploaded {name}\nFolder: {folder_url}")
    finally:
        _cleanup_paths([path])


async def _handle_relearn(update: Update, text: str):
    msg = update.effective_message
    name = text[len("relearn"):].strip()
    if not name:
        await msg.reply_text("Usage: relearn <Site Name>")
        return
    removed = SITES.forget(name)
    if removed:
        await msg.reply_text(
            f"Cleared saved structure for '{name}'. The next bookout at this "
            f"site will walk you through navigation again."
        )
    else:
        await msg.reply_text(f"No saved structure for '{name}'.")


async def cmd_checkstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    msg = update.effective_message
    args = context.args if context else []
    if not args:
        await msg.reply_text("Usage: /checkstock <serial>")
        return
    serial = " ".join(args).strip()

    from utils.sheets import find_serial_number
    service = _get_drive()
    try:
        result = await asyncio.to_thread(
            find_serial_number, service, _drive_id(), serial,
        )
    except Exception as e:
        await msg.reply_text(format_error("Stock lookup", str(e)))
        return

    if result is None:
        await msg.reply_text(f"'{serial}' was not found in any sheet.")
        return

    lines = [
        f"Found in {result['file_name']} / {result['sheet_name']} (row {result['row_index']})",
    ]
    for h, v in zip(result["headers"], result["row_values"]):
        if v is not None and str(v).strip():
            lines.append(f"  {h or '(no header)'}: {v}")
    await msg.reply_text("\n".join(lines))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    state = STATE.get(chat_id)
    if state:
        _cleanup_paths(state.get("_tmp_paths", []))
    STATE.clear(chat_id)
    await update.effective_message.reply_text("Cancelled.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.effective_message.reply_text(_HELP_TEXT)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def build_application(token: str) -> Application:
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("checkstock", cmd_checkstock))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.TEXT | filters.CAPTION,
        on_message,
    ))
    return app
