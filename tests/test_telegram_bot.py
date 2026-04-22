"""
Offline simulation tests for utils/telegram_bot.py.

All flows use fake Update/Context objects and mock every I/O boundary:
- _get_drive() returns a FakeDriveService
- _download_photo() returns a pre-created tmp path
- utils.extract functions are monkeypatched
- utils.sheets functions are monkeypatched
- utils.drive_folders functions are monkeypatched where needed

No real Telegram, Google, or Anthropic calls are made.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.conftest import FakeDriveService, SHARED_DRIVE_ID
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


# ---------------------------------------------------------------------------
# Fake Update / Context factories
# ---------------------------------------------------------------------------

def _make_photo(file_id="photo_file_id"):
    p = MagicMock()
    p.file_id = file_id
    return p


def make_update(
    text: str = "",
    photos=None,
    user_id: int = 111,
    chat_id: int = 42,
    media_group_id=None,
):
    """Build a minimal fake telegram Update."""
    msg = MagicMock()

    # Telegram: photo messages have caption, text messages have text
    if photos:
        msg.text = None
        msg.caption = text or None
        msg.photo = photos
    else:
        msg.text = text
        msg.caption = None
        msg.photo = []

    msg.media_group_id = media_group_id
    msg.reply_text = AsyncMock()

    user = MagicMock()
    user.id = user_id

    chat = MagicMock()
    chat.id = chat_id

    update = MagicMock()
    update.effective_message = msg
    update.effective_user = user
    update.effective_chat = chat
    return update


def make_context(args=None):
    """Build a minimal fake telegram Context."""
    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.get_file = AsyncMock()
    ctx.args = args or []
    return ctx


# ---------------------------------------------------------------------------
# State isolation fixture (autouse)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_bot_state(tmp_path, monkeypatch):
    """Reset all module-level singletons between tests."""
    import utils.telegram_bot as bot_mod
    import utils.drive_folders as df_mod
    from utils.site_detection import reload as reload_sites

    # Restore real FMAS site list in case a prior test called reload() with a custom path.
    reload_sites()

    # Clear state manager
    bot_mod.STATE._store.clear()

    # Clear media group buffers
    bot_mod._MEDIA_GROUPS.clear()
    bot_mod._PROCESSED_GROUP_IDS.clear()

    # Fresh SITES store pointing at tmp_path (no disk pollution)
    fresh_sites = SiteStructureStore(str(tmp_path / "sites.json"))
    monkeypatch.setattr(bot_mod, "SITES", fresh_sites)

    # Reset drive_folders _CACHE so get_atec_site_folder doesn't return stale IDs
    monkeypatch.setattr(df_mod, "_CACHE", None)

    # Set allowed user ids and shared drive id
    monkeypatch.setenv("ALLOWED_USER_IDS", "111")
    monkeypatch.setenv("SHARED_DRIVE_ID", SHARED_DRIVE_ID)


# ---------------------------------------------------------------------------
# Common patch helper
# ---------------------------------------------------------------------------

def _seeded_fake_drive() -> FakeDriveService:
    """Return a FakeDriveService with the standard ATEC tree seeded."""
    svc = FakeDriveService()
    drive_id = SHARED_DRIVE_ID
    svc.records[drive_id] = {
        "id": drive_id,
        "name": "Atec Cape Town",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [],
        "trashed": False,
    }
    sites = svc.add_folder("Sites", drive_id)
    fmas = svc.add_folder("FMAS", sites)
    stock_sheets = svc.add_folder("Stock Sheets", drive_id)
    active = svc.add_folder("Stock Sheets (Currently in use)", stock_sheets)
    svc.ids = {
        "sites": sites,
        "fmas": fmas,
        "stock_sheets": stock_sheets,
        "active": active,
        "drive": drive_id,
    }
    return svc


def _make_tmp_photo(tmp_path, name="label.jpg") -> str:
    """Create a minimal JPEG-like temp file."""
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9")
    return str(p)


SAMPLE_DETAILS = {
    "full_name": "John Smith",
    "phone": "+27 82 555 1234",
    "site_name": "The Topaz",
    "unit_number": "42",
    "address": "12 Seaview Rd",
    "isp": "Vumatel",
    "speed": "200/200 Mbps",
    "account_number": "T-0042",
}

ATEC_DETAILS = {
    "full_name": "Jane Doe",
    "phone": "+27 82 111 2222",
    "site_name": "Sunset Heights",  # not in fmas_sites.txt
    "unit_number": "7",
    "address": "5 Ocean View",
    "isp": "Openserve",
    "speed": "100/100 Mbps",
    "account_number": "SH-007",
}

SERIAL_EXTRACTION = {"serial_number": "SN-GOOD-001", "item_code": "ONT-GPON"}
STOCK_RESULT = {
    "file_id": "fid1",
    "file_name": "Serial Number Listing CPT.xlsx",
    "sheet_name": "Stock",
    "row_index": 3,
    "row_values": ["SN-GOOD-001", "ONT-GPON", "Stock", None],
    "headers": ["Serial Number", "Item Code", "Current Account", "Date Last Move"],
}


# ===========================================================================
# PURE UNIT TESTS — no async, no Telegram
# ===========================================================================

class TestClassifyPhotoNames:
    def test_single_serial_label(self):
        from utils.telegram_bot import classify_photo_names
        extractions = [{"serial_number": "SN1", "item_code": "X"}]
        result = classify_photo_names(extractions)
        assert result[0] == ("serial", "01_Serial_Number.jpg")

    def test_two_serial_labels_uses_indexed_names(self):
        from utils.telegram_bot import classify_photo_names
        extractions = [
            {"serial_number": "SN1", "item_code": "X"},
            {"serial_number": "SN2", "item_code": "Y"},
        ]
        result = classify_photo_names(extractions)
        assert result[0] == ("serial", "01_Serial_Number_01.jpg")
        assert result[1] == ("serial", "01_Serial_Number_02.jpg")

    def test_serial_then_non_labels_get_positional_names(self):
        from utils.telegram_bot import classify_photo_names
        extractions = [
            {"serial_number": "SN1", "item_code": "X"},
            {"serial_number": None},
            {"serial_number": None},
        ]
        result = classify_photo_names(extractions)
        assert result[0] == ("serial", "01_Serial_Number.jpg")
        assert result[1] == ("device", "04_Device_Photo.jpg")
        assert result[2] == ("ont", "02_ONT_Router_Placement.jpg")

    def test_no_labels_all_positional(self):
        from utils.telegram_bot import classify_photo_names
        extractions = [
            {"serial_number": None},
            {"serial_number": None},
            {"serial_number": None},
            {"serial_number": None},
            {"serial_number": None},
        ]
        result = classify_photo_names(extractions)
        # 5 non-label photos: device, ont, speed, install_01, install_02
        roles = [r for r, _ in result]
        assert roles[0] == "device"
        assert roles[1] == "ont"
        assert roles[2] == "speed"
        assert roles[3] == "install"
        assert roles[4] == "install"


class TestCollectItemsFromExtractions:
    def test_stops_at_first_non_serial(self):
        from utils.telegram_bot import collect_items_from_extractions
        extractions = [
            {"serial_number": "SN1", "item_code": "A"},
            {"serial_number": None, "item_code": None},
            {"serial_number": "SN3", "item_code": "C"},
        ]
        items = collect_items_from_extractions(extractions)
        assert len(items) == 1
        assert items[0]["serial"] == "SN1"

    def test_collects_all_leading_serials(self):
        from utils.telegram_bot import collect_items_from_extractions
        extractions = [
            {"serial_number": "SN1", "item_code": "A"},
            {"serial_number": "SN2", "item_code": "B"},
        ]
        items = collect_items_from_extractions(extractions)
        assert len(items) == 2
        assert items[1]["serial"] == "SN2"
        assert items[1]["is_swap"] is False

    def test_empty_extractions(self):
        from utils.telegram_bot import collect_items_from_extractions
        assert collect_items_from_extractions([]) == []


class TestMarkSwaps:
    def test_none_result_means_swap(self):
        from utils.telegram_bot import mark_swaps
        items = [{"serial": "SN1", "is_swap": False}, {"serial": "SN2", "is_swap": False}]
        mark_swaps(items, [{"some": "data"}, None])
        assert items[0]["is_swap"] is False
        assert items[1]["is_swap"] is True

    def test_all_found_no_swaps(self):
        from utils.telegram_bot import mark_swaps
        items = [{"serial": "SN1", "is_swap": False}]
        mark_swaps(items, [{"row_index": 1}])
        assert items[0]["is_swap"] is False


class TestAllSwaps:
    def test_all_swap_items(self):
        from utils.telegram_bot import all_swaps
        assert all_swaps([{"is_swap": True}, {"is_swap": True}]) is True

    def test_mixed_items_not_all_swaps(self):
        from utils.telegram_bot import all_swaps
        assert all_swaps([{"is_swap": True}, {"is_swap": False}]) is False

    def test_empty_list_returns_false(self):
        from utils.telegram_bot import all_swaps
        assert all_swaps([]) is False


class TestApplyNavChoice:
    def _base_state(self, site_id="site_id", sub_id="sub_id"):
        return {
            "atec_nav_path": [site_id],
            "atec_nav_breadcrumb": ["Sunset Heights"],
            "atec_nav_current_id": site_id,
        }

    def test_u_returns_select(self):
        from utils.telegram_bot import apply_nav_choice
        state = self._base_state()
        result = apply_nav_choice(state, "u", [])
        assert result["action"] == "select"

    def test_b_at_root_returns_invalid(self):
        from utils.telegram_bot import apply_nav_choice
        state = self._base_state()
        result = apply_nav_choice(state, "b", [])
        assert result["action"] == "invalid"
        assert "top" in result["message"]

    def test_b_with_deeper_path_goes_up(self):
        from utils.telegram_bot import apply_nav_choice
        state = {
            "atec_nav_path": ["site_id", "sub_id"],
            "atec_nav_breadcrumb": ["Sunset Heights", "Block A"],
            "atec_nav_current_id": "sub_id",
        }
        subfolders = [{"id": "x", "name": "Unit 1"}]
        result = apply_nav_choice(state, "b", subfolders)
        assert result["action"] == "up"
        assert state["atec_nav_current_id"] == "site_id"
        assert state["atec_nav_breadcrumb"] == ["Sunset Heights"]

    def test_numeric_choice_descends(self):
        from utils.telegram_bot import apply_nav_choice
        state = self._base_state()
        subfolders = [{"id": "unit7_id", "name": "Unit 7"}]
        result = apply_nav_choice(state, "1", subfolders)
        assert result["action"] == "descend"
        assert state["atec_nav_current_id"] == "unit7_id"
        assert "Unit 7" in state["atec_nav_breadcrumb"]

    def test_out_of_range_number_returns_invalid(self):
        from utils.telegram_bot import apply_nav_choice
        state = self._base_state()
        result = apply_nav_choice(state, "5", [{"id": "x", "name": "A"}])
        assert result["action"] == "invalid"

    def test_non_numeric_string_returns_invalid(self):
        from utils.telegram_bot import apply_nav_choice
        state = self._base_state()
        result = apply_nav_choice(state, "abc", [])
        assert result["action"] == "invalid"


class TestBuildNavReply:
    def test_first_level_shows_new_site_header(self):
        from utils.telegram_bot import build_nav_reply
        reply = build_nav_reply("Sunset Heights", ["Sunset Heights"], [])
        assert "New site: Sunset Heights" in reply

    def test_deeper_level_no_new_site_header(self):
        from utils.telegram_bot import build_nav_reply
        reply = build_nav_reply("Sunset Heights", ["Sunset Heights", "Block A"], [])
        assert "New site" not in reply

    def test_subfolders_listed_with_numbers(self):
        from utils.telegram_bot import build_nav_reply
        subs = [{"id": "a", "name": "Unit 1"}, {"id": "b", "name": "Unit 2"}]
        reply = build_nav_reply("Site", ["Site"], subs)
        assert "1. Unit 1" in reply
        assert "2. Unit 2" in reply

    def test_no_subfolders_shows_message(self):
        from utils.telegram_bot import build_nav_reply
        reply = build_nav_reply("Site", ["Site"], [])
        assert "(no subfolders)" in reply


class TestFormatSerialCorrectionPrompt:
    def test_shows_serial_in_backticks(self):
        from utils.telegram_bot import format_serial_correction_prompt
        msg = format_serial_correction_prompt("SN-BAD-001", "ONT-X")
        assert "`SN-BAD-001`" in msg
        assert "swap" in msg.lower()

    def test_shows_item_code_in_parens(self):
        from utils.telegram_bot import format_serial_correction_prompt
        msg = format_serial_correction_prompt("SN-BAD-001", "ONT-X")
        assert "(ONT-X)" in msg

    def test_no_item_code(self):
        from utils.telegram_bot import format_serial_correction_prompt
        msg = format_serial_correction_prompt("SN-BAD-001", None)
        assert "(None)" not in msg


class TestFormatSwapWarning:
    def test_lists_all_swap_items(self):
        from utils.telegram_bot import format_swap_warning
        items = [
            {"serial": "SN1", "item_code": "ONT"},
            {"serial": "SN2", "item_code": ""},
        ]
        msg = format_swap_warning(items)
        assert "SN1" in msg
        assert "SN2" in msg
        assert "/cancel" in msg

    def test_no_item_code_shows_placeholder(self):
        from utils.telegram_bot import format_swap_warning
        items = [{"serial": "SN-X", "item_code": None}]
        msg = format_swap_warning(items)
        assert "(no item code)" in msg


class TestFormatSuccess:
    def test_includes_email_for_non_swap(self):
        from utils.telegram_bot import format_success
        items = [{"serial": "SN1", "item_code": "ONT", "is_swap": False}]
        result = format_success(SAMPLE_DETAILS, items, "https://drive.google.com/x", 1, "EMAIL TEXT")
        assert "EMAIL TEXT" in result
        assert "ACCOUNTS EMAIL" in result

    def test_no_email_for_all_swaps(self):
        from utils.telegram_bot import format_success
        items = [{"serial": "SN1", "item_code": "ONT", "is_swap": True}]
        result = format_success(SAMPLE_DETAILS, items, "https://drive.google.com/x", 1, None)
        assert "ACCOUNTS EMAIL" not in result

    def test_swap_badge_shown(self):
        from utils.telegram_bot import format_success
        items = [{"serial": "SN1", "item_code": "ONT", "is_swap": True}]
        result = format_success(SAMPLE_DETAILS, items, "https://x", 1, None)
        assert "Swap (not updated)" in result

    def test_non_swap_badge_shown(self):
        from utils.telegram_bot import format_success
        items = [{"serial": "SN1", "item_code": "ONT", "is_swap": False}]
        result = format_success(SAMPLE_DETAILS, items, "https://x", 1, "email")
        assert "Sheet updated" in result


# ===========================================================================
# ASYNC FLOW TESTS
# ===========================================================================

class TestNonWhitelistedUser:
    def test_non_whitelisted_user_dropped_silently(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        update = make_update(text="hello", user_id=999)
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        update.effective_message.reply_text.assert_not_called()
        ctx.bot.send_message.assert_not_called()


class TestMissingCaption:
    def test_no_caption_on_photo_returns_error(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo = _make_photo()
        update = make_update(text="", photos=[photo])
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        ctx.bot.send_message.assert_called_once()
        call_text = ctx.bot.send_message.call_args[0][1]
        assert "no ticket text found" in call_text


class TestNoPhotos:
    def test_no_photos_returns_error(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        # Set STEP_COLLECTING state so it tries to process a bookout without photos
        state = new_bookout_state()
        state["step"] = STEP_COLLECTING
        bot_mod.STATE.set(42, state)

        update = make_update(text="some ticket text")
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        ctx.bot.send_message.assert_called_once()
        call_text = ctx.bot.send_message.call_args[0][1]
        assert "no photos attached" in call_text


class TestVisionNoSerial:
    def test_vision_returns_no_serial_sends_error(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": None, "item_code": None}
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        ctx.bot.send_message.assert_called()
        calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        assert any("no serial label" in c for c in calls)


class TestMissingTicketFields:
    def test_missing_full_name_returns_error(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-001", "item_code": "ONT"}
        )
        # Return details without full_name
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: {"site_name": "The Topaz", "unit_number": "42"}
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        ctx.bot.send_message.assert_called()
        calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        assert any("missing field(s)" in c and "full_name" in c for c in calls)


class TestFMASHappyPath:
    def test_fmas_bookout_full_flow(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: dict(SERIAL_EXTRACTION)
        )
        # "The Topaz" is in fmas_sites.txt — resolve_fmas_site returns it
        details = dict(SAMPLE_DETAILS)  # site_name = "The Topaz" (FMAS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        ctx.bot.send_message.assert_called()
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg
        assert "John Smith" in success_msg
        assert "ACCOUNTS EMAIL" in success_msg


class TestATECKnownTemplate:
    def test_atec_with_saved_template_resolves_folder(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        sites_id = fake_drive.ids["sites"]

        # Pre-create the ATEC site folder structure in the fake drive
        site_id = fake_drive.add_folder("Sunset Heights", sites_id)
        unit_id = fake_drive.add_folder("Unit 7", site_id)

        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        # Pre-learn the template with empty folder_id_cache so lookup goes through _find_or_create_folder
        bot_mod.SITES.learn("Sunset Heights", ["Unit 7"], "7")

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-ATEC-001", "item_code": "ONT-X"}
        )
        details = dict(ATEC_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )
        # Patch get_atec_site_folder to return the site_id directly
        import utils.drive_folders as df_mod
        monkeypatch.setattr(
            df_mod, "get_atec_site_folder",
            lambda svc, drive_id, site_name: (site_id, False)
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        ctx.bot.send_message.assert_called()
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg


class TestATECNewSiteNavToSubfolder:
    def test_nav_descend_then_upload(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod
        import utils.drive_folders as df_mod

        fake_drive = _seeded_fake_drive()
        sites_id = fake_drive.ids["sites"]
        site_id = fake_drive.add_folder("Sunset Heights", sites_id)
        unit_id = fake_drive.add_folder("Unit 7", site_id)

        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)
        monkeypatch.setattr(
            df_mod, "get_atec_site_folder",
            lambda svc, drive_id, site_name: (site_id, False)
        )

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-ATEC-NAV", "item_code": "ONT-Y"}
        )
        details = dict(ATEC_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        # Send initial bookout
        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        # Should now be in STEP_NAV with a nav prompt
        state = bot_mod.STATE.get(42)
        assert state is not None
        assert state["step"] == STEP_NAV

        nav_calls_before = ctx.bot.send_message.call_count

        # User descends into "Unit 7" subfolder (index 1)
        nav_update = make_update(text="1")
        asyncio.run(bot_mod.on_message(nav_update, ctx))

        # Should get new nav listing (went down)
        state = bot_mod.STATE.get(42)
        assert state is not None
        assert "Unit 7" in state["atec_nav_breadcrumb"]

        # User presses 'u' to upload here
        upload_update = make_update(text="u")
        asyncio.run(bot_mod.on_message(upload_update, ctx))

        # Should complete with success message
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg

        # SITES.learn should have been called
        assert bot_mod.SITES.has("Sunset Heights")


class TestATECNewSiteUploadAtRoot:
    def test_upload_at_site_root_autocreates_unit_folder(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod
        import utils.drive_folders as df_mod

        fake_drive = _seeded_fake_drive()
        sites_id = fake_drive.ids["sites"]
        site_id = fake_drive.add_folder("Sunset Heights", sites_id)
        # No Unit subfolder in the drive yet

        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)
        monkeypatch.setattr(
            df_mod, "get_atec_site_folder",
            lambda svc, drive_id, site_name: (site_id, False)
        )

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-ROOT-001", "item_code": "ONT-R"}
        )
        details = dict(ATEC_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        # Send initial bookout
        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        state = bot_mod.STATE.get(42)
        assert state is not None
        assert state["step"] == STEP_NAV

        # Immediately press 'u' at site root — no subfolders descended
        upload_update = make_update(text="u")
        asyncio.run(bot_mod.on_message(upload_update, ctx))

        # Unit folder "Unit 7" should have been auto-created in the fake drive
        created_names = [
            c["body"]["name"] for c in fake_drive.create_calls
            if c.get("body", {}).get("mimeType") == "application/vnd.google-apps.folder"
        ]
        assert "Unit 7" in created_names

        # SITES.learn should have been called with Unit token
        assert bot_mod.SITES.has("Sunset Heights")
        entry = bot_mod.SITES.get("Sunset Heights")
        assert any("{unit}" in seg for seg in entry["path_template"])

        # Success message should have been sent
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg


class TestSerialCorrectionOCRMisread:
    def test_ocr_misread_user_corrects_successfully(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        # Vision returns a bad serial
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-BAD-READ", "item_code": "ONT-X"}
        )
        details = dict(SAMPLE_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )

        # First lookup (bad serial) returns None; second (corrected) returns result
        call_count = [0]
        def find_serial(svc, drive_id, serial):
            call_count[0] += 1
            if serial == "SN-GOOD-CORRECTED":
                return dict(STOCK_RESULT)
            return None

        monkeypatch.setattr(sheets_mod, "find_serial_number", find_serial)
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        # Bot should be in STEP_SERIAL_CORRECTION now
        state = bot_mod.STATE.get(42)
        assert state is not None
        assert state["step"] == STEP_SERIAL_CORRECTION

        # User sends corrected serial
        correction_update = make_update(text="SN-GOOD-CORRECTED")
        asyncio.run(bot_mod.on_message(correction_update, ctx))

        # Should complete with success
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg


class TestSerialCorrectionUserTypesSwap:
    def test_user_replies_swap_during_correction(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-BAD", "item_code": "ONT"}
        )
        details = dict(SAMPLE_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: None  # always not found
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        state = bot_mod.STATE.get(42)
        assert state["step"] == STEP_SERIAL_CORRECTION

        # User replies "swap"
        swap_update = make_update(text="swap")
        asyncio.run(bot_mod.on_message(swap_update, ctx))

        # Item is now a swap — should have progressed (no more not-found items)
        # to complete successfully (FMAS site, no stock update, but folder/photos ok)
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg
        assert "Swap (not updated)" in success_msg


class TestSerialCorrectionStillNotFound:
    def test_corrected_serial_also_not_found_enters_swap_confirm(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-BAD", "item_code": "ONT"}
        )
        details = dict(SAMPLE_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        # Always not found
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: None
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        state = bot_mod.STATE.get(42)
        assert state["step"] == STEP_SERIAL_CORRECTION

        # User sends a "correction" that is still not found
        correction_update = make_update(text="SN-STILL-BAD")
        asyncio.run(bot_mod.on_message(correction_update, ctx))

        state = bot_mod.STATE.get(42)
        assert state is not None
        assert state["step"] == STEP_SWAP_CONFIRM

        # Swap warning message should have been sent
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        swap_warn = all_calls[-1]
        assert "not found in any stock sheet" in swap_warn


class TestSwapConfirmContinue:
    def test_any_message_in_swap_confirm_continues_flow(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-BAD", "item_code": "ONT"}
        )
        details = dict(SAMPLE_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: None
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        # Move to SWAP_CONFIRM by sending a non-found correction
        correction_update = make_update(text="SN-STILL-BAD")
        asyncio.run(bot_mod.on_message(correction_update, ctx))
        state = bot_mod.STATE.get(42)
        assert state["step"] == STEP_SWAP_CONFIRM

        # Reply with "ok" to continue
        ok_update = make_update(text="ok")
        asyncio.run(bot_mod.on_message(ok_update, ctx))

        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg


class TestCancelInSerialCorrection:
    def test_cancel_during_serial_correction_clears_state(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-BAD", "item_code": "ONT"}
        )
        details = dict(SAMPLE_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: None
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        state = bot_mod.STATE.get(42)
        assert state["step"] == STEP_SERIAL_CORRECTION

        cancel_update = make_update(text="/cancel")
        asyncio.run(bot_mod.on_message(cancel_update, ctx))

        assert bot_mod.STATE.get(42) is None
        cancel_reply = cancel_update.effective_message.reply_text.call_args[0][0]
        assert "Cancelled" in cancel_reply


class TestCancelInSwapConfirm:
    def test_cancel_during_swap_confirm_clears_state(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-BAD", "item_code": "ONT"}
        )
        details = dict(SAMPLE_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: None
        )

        # Get into SWAP_CONFIRM
        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        correction_update = make_update(text="SN-STILL-BAD")
        asyncio.run(bot_mod.on_message(correction_update, ctx))

        state = bot_mod.STATE.get(42)
        assert state["step"] == STEP_SWAP_CONFIRM

        cancel_update = make_update(text="/cancel")
        asyncio.run(bot_mod.on_message(cancel_update, ctx))

        assert bot_mod.STATE.get(42) is None
        cancel_reply = cancel_update.effective_message.reply_text.call_args[0][0]
        assert "Cancelled" in cancel_reply


class TestMultipleItemsSerialCorrection:
    def test_first_item_not_found_second_found_skips_correction_for_second(
        self, monkeypatch, tmp_path
    ):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path1 = _make_tmp_photo(tmp_path, "label1.jpg")
        photo_path2 = _make_tmp_photo(tmp_path, "label2.jpg")
        download_calls = [0]

        async def fake_download(bot, file_id):
            idx = download_calls[0]
            download_calls[0] += 1
            return [photo_path1, photo_path2][idx]

        monkeypatch.setattr(bot_mod, "_download_photo", fake_download)

        # First extraction: not-found serial; second: found serial
        extract_calls = [0]
        def extract_serial(path):
            idx = extract_calls[0]
            extract_calls[0] += 1
            if idx == 0:
                return {"serial_number": "SN-BAD-1", "item_code": "ONT1"}
            return {"serial_number": "SN-GOOD-2", "item_code": "ONT2"}

        monkeypatch.setattr(extract_mod, "extract_serial_from_photo", extract_serial)
        details = dict(SAMPLE_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )

        def find_serial(svc, drive_id, serial):
            if serial in ("SN-GOOD-2", "SN-CORRECTED-1"):
                return dict(STOCK_RESULT)
            return None

        monkeypatch.setattr(sheets_mod, "find_serial_number", find_serial)
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        photo1 = _make_photo("fid1")
        photo2 = _make_photo("fid2")
        update = make_update(text="Ticket text", photos=[photo1, photo2])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        # Should be in STEP_SERIAL_CORRECTION for first item only
        state = bot_mod.STATE.get(42)
        assert state is not None
        assert state["step"] == STEP_SERIAL_CORRECTION
        assert state["_correction_serial_index"] == 0

        # User corrects first serial
        correction_update = make_update(text="SN-CORRECTED-1")
        asyncio.run(bot_mod.on_message(correction_update, ctx))

        # Second item was already found — should complete without another correction prompt
        all_calls = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        success_msg = all_calls[-1]
        assert "Booked out" in success_msg


class TestNavBackAtRoot:
    def test_nav_back_at_root_shows_already_at_top(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod
        import utils.drive_folders as df_mod

        fake_drive = _seeded_fake_drive()
        sites_id = fake_drive.ids["sites"]
        site_id = fake_drive.add_folder("Sunset Heights", sites_id)

        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)
        monkeypatch.setattr(
            df_mod, "get_atec_site_folder",
            lambda svc, drive_id, site_name: (site_id, False)
        )

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-NAV", "item_code": "ONT"}
        )
        details = dict(ATEC_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        # Initial bookout
        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        state = bot_mod.STATE.get(42)
        assert state["step"] == STEP_NAV

        # Press 'b' at root — should show "Already at the top."
        back_update = make_update(text="b")
        asyncio.run(bot_mod.on_message(back_update, ctx))

        back_update.effective_message.reply_text.assert_called()
        reply = back_update.effective_message.reply_text.call_args[0][0]
        assert "Already at the top" in reply


class TestNavInvalidChoice:
    def test_nav_invalid_text_shows_error(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod
        import utils.drive_folders as df_mod

        fake_drive = _seeded_fake_drive()
        sites_id = fake_drive.ids["sites"]
        site_id = fake_drive.add_folder("Sunset Heights", sites_id)

        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)
        monkeypatch.setattr(
            df_mod, "get_atec_site_folder",
            lambda svc, drive_id, site_name: (site_id, False)
        )

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: {"serial_number": "SN-NAV", "item_code": "ONT"}
        )
        details = dict(ATEC_DETAILS)
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        photo = _make_photo()
        update = make_update(text="Ticket text", photos=[photo])
        ctx = make_context()
        asyncio.run(bot_mod.on_message(update, ctx))

        # Send garbage text in nav
        bad_update = make_update(text="xyz")
        asyncio.run(bot_mod.on_message(bad_update, ctx))

        bad_update.effective_message.reply_text.assert_called()
        reply = bad_update.effective_message.reply_text.call_args[0][0]
        assert "number" in reply.lower() or "invalid" in reply.lower()


class TestCheckstock:
    def test_checkstock_found(self, monkeypatch):
        import utils.telegram_bot as bot_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )

        update = make_update(text="/checkstock SN-GOOD-001")
        update.effective_user.id = 111
        ctx = make_context(args=["SN-GOOD-001"])

        asyncio.run(bot_mod.cmd_checkstock(update, ctx))

        update.effective_message.reply_text.assert_called_once()
        reply = update.effective_message.reply_text.call_args[0][0]
        assert "Serial Number Listing CPT.xlsx" in reply

    def test_checkstock_not_found(self, monkeypatch):
        import utils.telegram_bot as bot_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: None
        )

        update = make_update()
        update.effective_user.id = 111
        ctx = make_context(args=["SN-UNKNOWN"])

        asyncio.run(bot_mod.cmd_checkstock(update, ctx))

        reply = update.effective_message.reply_text.call_args[0][0]
        assert "not found" in reply

    def test_checkstock_no_args_shows_usage(self, monkeypatch):
        import utils.telegram_bot as bot_mod

        update = make_update()
        update.effective_user.id = 111
        ctx = make_context(args=[])

        asyncio.run(bot_mod.cmd_checkstock(update, ctx))

        reply = update.effective_message.reply_text.call_args[0][0]
        assert "Usage" in reply


class TestAddPhotosTextOnly:
    def test_add_photos_text_only_prompts_to_send_as_caption(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod

        # Plain text message (no photos)
        update = make_update(text="add photos Sunset Heights 7")
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        update.effective_message.reply_text.assert_called()
        reply = update.effective_message.reply_text.call_args[0][0]
        # Should ask user to send photos with caption
        assert "caption" in reply.lower() or "send" in reply.lower()


class TestAddPhotosAsCaption:
    def test_add_photos_as_caption_on_photo_uploads(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod
        import utils.drive_folders as df_mod

        fake_drive = _seeded_fake_drive()
        sites_id = fake_drive.ids["sites"]
        fmas_id = fake_drive.ids["fmas"]
        # "The Topaz" is an FMAS site — create folders in fake drive
        topaz_id = fake_drive.add_folder("The Topaz", fmas_id)
        unit_id = fake_drive.add_folder("Unit 42", topaz_id)

        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )

        photo = _make_photo()
        update = make_update(text="add photos The Topaz 42", photos=[photo])
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        update.effective_message.reply_text.assert_called()
        reply = update.effective_message.reply_text.call_args[0][0]
        assert "Uploaded" in reply or "02_ONT" in reply


class TestRelearn:
    def test_relearn_known_site_clears_it(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod

        # Pre-load a site
        bot_mod.SITES.learn("Sunset Heights", ["Unit 7"], "7")
        assert bot_mod.SITES.has("Sunset Heights")

        update = make_update(text="relearn Sunset Heights")
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        update.effective_message.reply_text.assert_called()
        reply = update.effective_message.reply_text.call_args[0][0]
        assert "Cleared" in reply
        assert not bot_mod.SITES.has("Sunset Heights")

    def test_relearn_unknown_site_replies_no_saved_structure(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod

        update = make_update(text="relearn Unknown Site")
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        reply = update.effective_message.reply_text.call_args[0][0]
        assert "No saved structure" in reply


class TestPlainTextHelp:
    def test_plain_text_no_state_shows_help(self, monkeypatch, tmp_path):
        import utils.telegram_bot as bot_mod

        update = make_update(text="hello there")
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        update.effective_message.reply_text.assert_called_once()
        reply = update.effective_message.reply_text.call_args[0][0]
        assert "ATEC Bookout Bot" in reply or "/checkstock" in reply


class TestFuzzySiteCorrection:
    def test_fmas_misspelled_site_corrected_and_notified(self, monkeypatch, tmp_path):
        """Misspelled FMAS site is silently corrected; bot notifies user and completes flow."""
        import utils.telegram_bot as bot_mod
        import utils.extract as extract_mod
        import utils.sheets as sheets_mod

        fake_drive = _seeded_fake_drive()
        monkeypatch.setattr(bot_mod, "_get_drive", lambda: fake_drive)
        monkeypatch.setattr(bot_mod, "_drive_id", lambda: SHARED_DRIVE_ID)

        photo_path = _make_tmp_photo(tmp_path)
        monkeypatch.setattr(
            bot_mod, "_download_photo",
            AsyncMock(return_value=photo_path)
        )
        monkeypatch.setattr(
            extract_mod, "extract_serial_from_photo",
            lambda path: dict(SERIAL_EXTRACTION)
        )
        # Claude returns a misspelled FMAS site name
        details = dict(SAMPLE_DETAILS, site_name="The Topazz")
        monkeypatch.setattr(
            extract_mod, "extract_client_details",
            lambda text: dict(details)
        )
        monkeypatch.setattr(
            sheets_mod, "find_serial_number",
            lambda svc, drive_id, serial: dict(STOCK_RESULT)
        )
        monkeypatch.setattr(
            sheets_mod, "update_stock_row",
            lambda svc, drive_id, serial, account: None
        )

        update = make_update(text="Ticket text", photos=[_make_photo()])
        ctx = make_context()

        asyncio.run(bot_mod.on_message(update, ctx))

        all_msgs = [call[0][1] for call in ctx.bot.send_message.call_args_list]
        correction_msgs = [m for m in all_msgs if "corrected" in m.lower()]
        assert correction_msgs, "Expected a site-name correction notification"
        assert "The Topaz" in correction_msgs[0]
        assert "The Topazz" in correction_msgs[0]

        # Full flow still completes
        assert "Booked out" in all_msgs[-1]
