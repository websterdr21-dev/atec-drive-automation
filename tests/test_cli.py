"""
End-to-end CLI flow tests.
Target: bookout.py top-level commands and helpers.

The CLI is interactive via input(). Tests drive it by monkeypatching input
and by replacing the heavy util functions with stubs so we can observe the
command's decision logic.
"""

import builtins
import io
import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest

import bookout


# ---------------------------------------------------------------------------
# Input helper
# ---------------------------------------------------------------------------

class _Inputs:
    def __init__(self, items):
        self._it = iter(items)

    def __call__(self, prompt=""):
        try:
            return next(self._it)
        except StopIteration:
            raise AssertionError(
                f"CLI asked for more input than the test provided. Last prompt: {prompt!r}"
            )


def _feed(monkeypatch, items):
    monkeypatch.setattr(builtins, "input", _Inputs(items))


# ---------------------------------------------------------------------------
# _get_env
# ---------------------------------------------------------------------------

def test_get_env_exits_if_service_account_missing(monkeypatch):
    monkeypatch.delenv("SERVICE_ACCOUNT_PATH", raising=False)
    with pytest.raises(SystemExit):
        bookout._get_env()


def test_get_env_exits_if_drive_id_missing(monkeypatch, tmp_path):
    sa = tmp_path / "sa.json"
    sa.write_text("{}")
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", str(sa))
    monkeypatch.delenv("SHARED_DRIVE_ID", raising=False)
    with pytest.raises(SystemExit):
        bookout._get_env()


def test_get_env_exits_if_service_account_file_does_not_exist(monkeypatch):
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", "/really/not/here.json")
    monkeypatch.setenv("SHARED_DRIVE_ID", "X")
    with pytest.raises(SystemExit):
        bookout._get_env()


def test_get_env_happy_path(monkeypatch, tmp_path):
    sa = tmp_path / "sa.json"
    sa.write_text("{}")
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", str(sa))
    monkeypatch.setenv("SHARED_DRIVE_ID", "SHARED")
    assert bookout._get_env() == (str(sa), "SHARED")


# ---------------------------------------------------------------------------
# _confirm / _prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer,expected", [
    ("y", True), ("Y", True), ("yes", True), ("YES", True),
    ("n", False), ("no", False), ("", False), ("maybe", False),
])
def test_confirm_parses_answer(monkeypatch, answer, expected):
    _feed(monkeypatch, [answer])
    assert bookout._confirm() is expected


def test_prompt_returns_default_on_empty(monkeypatch):
    _feed(monkeypatch, [""])
    assert bookout._prompt("Label", default="DEFAULT") == "DEFAULT"


def test_prompt_returns_entered_value(monkeypatch):
    _feed(monkeypatch, ["entered"])
    assert bookout._prompt("Label", default="DEFAULT") == "entered"


# ---------------------------------------------------------------------------
# _ask_site_type
# ---------------------------------------------------------------------------

def test_ask_site_type_fmas(monkeypatch):
    _feed(monkeypatch, ["1"])
    assert bookout._ask_site_type("S") is True


def test_ask_site_type_atec(monkeypatch):
    _feed(monkeypatch, ["2"])
    assert bookout._ask_site_type("S") is False


def test_ask_site_type_retries_on_bad_input(monkeypatch, capsys):
    _feed(monkeypatch, ["x", "3", "", "2"])
    assert bookout._ask_site_type("S") is False


# ---------------------------------------------------------------------------
# check-stock command — happy + miss paths
# ---------------------------------------------------------------------------

def test_check_stock_prints_found_row(monkeypatch, capsys, tmp_path):
    # Valid env
    sa = tmp_path / "sa.json"; sa.write_text("{}")
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", str(sa))
    monkeypatch.setenv("SHARED_DRIVE_ID", "SHARED")

    monkeypatch.setattr(
        "utils.auth.get_drive_service", lambda _sa: MagicMock(name="drive")
    )
    monkeypatch.setattr(
        "utils.sheets.find_serial_number",
        lambda svc, did, serial: {
            "file_id": "f1",
            "file_name": "Serial Number Listing CPT.xlsx",
            "sheet_name": "Stock",
            "row_index": 3,
            "row_values": ["SN-0001", "ONT", "Stock", None],
            "headers": ["Serial Number", "Item Code", "Current Account", "Date Last Move"],
        },
    )
    _feed(monkeypatch, ["SN-0001"])

    bookout.cmd_check_stock()
    out = capsys.readouterr().out
    assert "[FOUND]" in out
    assert "Serial Number Listing CPT.xlsx" in out
    assert "SN-0001" in out


def test_check_stock_prints_not_found(monkeypatch, capsys, tmp_path):
    sa = tmp_path / "sa.json"; sa.write_text("{}")
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", str(sa))
    monkeypatch.setenv("SHARED_DRIVE_ID", "SHARED")

    monkeypatch.setattr("utils.auth.get_drive_service", lambda _sa: MagicMock())
    monkeypatch.setattr("utils.sheets.find_serial_number", lambda *a, **k: None)
    _feed(monkeypatch, ["NOT-HERE"])

    bookout.cmd_check_stock()
    out = capsys.readouterr().out
    assert "[NOT FOUND]" in out


def test_check_stock_exits_on_blank_input(monkeypatch, tmp_path):
    sa = tmp_path / "sa.json"; sa.write_text("{}")
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", str(sa))
    monkeypatch.setenv("SHARED_DRIVE_ID", "SHARED")
    monkeypatch.setattr("utils.auth.get_drive_service", lambda _sa: MagicMock())
    _feed(monkeypatch, [""])

    with pytest.raises(SystemExit):
        bookout.cmd_check_stock()


# ---------------------------------------------------------------------------
# bookout command — happy FMAS path (no real API calls)
# ---------------------------------------------------------------------------

def _stub_env(monkeypatch, tmp_path):
    sa = tmp_path / "sa.json"; sa.write_text("{}")
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", str(sa))
    monkeypatch.setenv("SHARED_DRIVE_ID", "SHARED")
    monkeypatch.setattr("utils.auth.get_drive_service", lambda _sa: MagicMock(name="drive"))


def test_cmd_bookout_fmas_happy_path(
    monkeypatch, tmp_path, extracted_client_details, capsys
):
    _stub_env(monkeypatch, tmp_path)
    photo = tmp_path / "label.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")

    # Stub the util layer
    monkeypatch.setattr(
        "utils.extract.extract_client_details",
        lambda _t: dict(extracted_client_details),
    )
    monkeypatch.setattr(
        "utils.extract.extract_serial_from_photo",
        lambda _p: {"serial_number": "SN-0001", "item_code": "ONT-GPON-1"},
    )
    monkeypatch.setattr(
        "utils.sheets.find_serial_number",
        lambda svc, did, serial: {
            "file_id": "f", "file_name": "S.xlsx", "sheet_name": "Stock",
            "row_index": 3, "row_values": [], "headers": [],
        },
    )
    update_calls = []
    monkeypatch.setattr(
        "utils.sheets.update_stock_row",
        lambda svc, did, serial, account: update_calls.append((serial, account)),
    )
    monkeypatch.setattr(
        "utils.drive_folders.get_unit_folder",
        lambda *a, **k: ("unit_id", "https://drive/unit", True, True),
    )
    upload_calls = []
    monkeypatch.setattr(
        "utils.photos.upload_bookout_photos",
        lambda *a, **k: upload_calls.append(a) or [("01_Serial_Number.jpg", "id")],
    )
    email_calls = []
    monkeypatch.setattr(
        "utils.gmail.print_bookout_email",
        lambda d: email_calls.append(d),
    )

    _feed(monkeypatch, [
        "Some ticket text",  # ticket line 1
        "",                  # blank
        "",                  # second blank to exit ticket block
        "y",                 # details correct
        "1",                 # FMAS
        str(photo),          # serial photo path
        "y",                 # serial/item correct
        "y",                 # proceed with stock update
        "",                  # device photo path (skip)
    ])

    bookout.cmd_bookout()

    assert update_calls == [("SN-0001", "42 Atlantic Beach Estate")]
    assert upload_calls, "photos should have been uploaded"
    assert email_calls and email_calls[0]["serial_number"] == "SN-0001"
    assert email_calls[0]["is_fmas"] is True


def test_cmd_bookout_swap_mode_skips_sheet_update_and_email(
    monkeypatch, tmp_path, extracted_client_details
):
    _stub_env(monkeypatch, tmp_path)
    photo = tmp_path / "label.jpg"; photo.write_bytes(b"\xff\xd8\xff\xd9")

    monkeypatch.setattr(
        "utils.extract.extract_client_details",
        lambda _t: dict(extracted_client_details),
    )
    monkeypatch.setattr(
        "utils.extract.extract_serial_from_photo",
        lambda _p: {"serial_number": "NEW-SERIAL", "item_code": "ONT"},
    )
    # Serial not found → swap mode
    monkeypatch.setattr("utils.sheets.find_serial_number", lambda *a, **k: None)

    update_calls = []
    monkeypatch.setattr(
        "utils.sheets.update_stock_row",
        lambda *a, **k: update_calls.append(a),
    )
    monkeypatch.setattr(
        "utils.drive_folders.get_unit_folder",
        lambda *a, **k: ("uid", "https://drive/u", False, False),
    )
    monkeypatch.setattr(
        "utils.photos.upload_bookout_photos",
        lambda *a, **k: [("01_Serial_Number.jpg", "id")],
    )
    email_calls = []
    monkeypatch.setattr(
        "utils.gmail.print_bookout_email", lambda d: email_calls.append(d)
    )

    _feed(monkeypatch, [
        "ticket", "", "",   # ticket text + double-blank
        "y",                # details correct
        "1",                # FMAS
        str(photo),         # photo path
        "y",                # serial/item correct
        "y",                # proceed as swap
        "",                 # no device photo
    ])

    bookout.cmd_bookout()

    assert update_calls == [], "sheet update must be skipped in swap mode"
    assert email_calls == [], "email must be skipped in swap mode"


def test_cmd_bookout_aborts_when_swap_not_confirmed(
    monkeypatch, tmp_path, extracted_client_details
):
    _stub_env(monkeypatch, tmp_path)
    photo = tmp_path / "label.jpg"; photo.write_bytes(b"\xff\xd8\xff\xd9")

    monkeypatch.setattr(
        "utils.extract.extract_client_details",
        lambda _t: dict(extracted_client_details),
    )
    monkeypatch.setattr(
        "utils.extract.extract_serial_from_photo",
        lambda _p: {"serial_number": "X", "item_code": "Y"},
    )
    monkeypatch.setattr("utils.sheets.find_serial_number", lambda *a, **k: None)

    get_unit_called = []
    monkeypatch.setattr(
        "utils.drive_folders.get_unit_folder",
        lambda *a, **k: get_unit_called.append(a) or ("", "", False, False),
    )

    _feed(monkeypatch, [
        "ticket", "", "",
        "y", "1", str(photo), "y",
        "n",  # REFUSE to proceed as swap
    ])

    with pytest.raises(SystemExit) as exc:
        bookout.cmd_bookout()
    assert exc.value.code == 0
    assert get_unit_called == [], "no drive writes should occur after abort"


def test_cmd_bookout_exits_if_no_ticket_text(monkeypatch, tmp_path):
    _stub_env(monkeypatch, tmp_path)
    _feed(monkeypatch, ["", ""])  # immediate blank terminates
    with pytest.raises(SystemExit):
        bookout.cmd_bookout()


def test_cmd_bookout_exits_if_serial_photo_missing(
    monkeypatch, tmp_path, extracted_client_details
):
    _stub_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "utils.extract.extract_client_details",
        lambda _t: dict(extracted_client_details),
    )
    _feed(monkeypatch, [
        "ticket", "", "",
        "y", "1",
        "/no/such/photo.jpg",
    ])
    with pytest.raises(SystemExit):
        bookout.cmd_bookout()


# ---------------------------------------------------------------------------
# add-photos command — folder must exist (non-FMAS branch raises cleanly)
# ---------------------------------------------------------------------------

def test_add_photos_skips_upload_when_no_paths_given(
    monkeypatch, tmp_path
):
    _stub_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "utils.drive_folders.get_unit_folder",
        lambda *a, **k: ("uid", "https://drive/u", False, False),
    )
    upload_called = []
    monkeypatch.setattr(
        "utils.photos.upload_post_install_photos",
        lambda *a, **k: upload_called.append(a) or [],
    )
    _feed(monkeypatch, [
        "Site X",          # site name
        "42",              # unit number
        "1",               # FMAS
        "",                # ONT photo path (skip)
        "",                # first install photo (skip — breaks loop)
        "",                # speed photo (skip)
    ])

    bookout.cmd_add_photos()

    assert upload_called == [], "nothing should be uploaded when no paths given"


# ---------------------------------------------------------------------------
# CLI entry-point dispatch
# ---------------------------------------------------------------------------

def test_main_unknown_command_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bookout.py", "nope"])
    with pytest.raises(SystemExit):
        runpy.run_path(bookout.__file__, run_name="__main__")


def test_main_no_command_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bookout.py"])
    with pytest.raises(SystemExit):
        runpy.run_path(bookout.__file__, run_name="__main__")


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------

def test_no_real_gmail_api_imported():
    """
    format_bookout_email must not require googleapi to send mail — this
    codebase intentionally prints emails rather than sending them.
    """
    import utils.gmail as gmail_mod
    assert not hasattr(gmail_mod, "send_email")
    assert not hasattr(gmail_mod, "send")
