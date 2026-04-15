"""
Email composition.
Target: utils/gmail.py

This codebase does NOT send email via the Gmail API — format_bookout_email
returns a ready-to-copy string and print_bookout_email prints it.
All tests assert content only; nothing is ever sent.
"""

import datetime

import pytest

from utils import gmail


@pytest.fixture
def base_details():
    return {
        "item_code": "ONT-GPON-1",
        "serial_number": "SN-0001",
        "full_name": "John Smith",
        "phone": "+27 82 555 1234",
        "site_name": "Atlantic Beach",
        "unit_number": "Unit 42",
        "address": "12 Seaview Rd, Melkbos",
        "isp": "Vumatel",
        "speed": "200/200 Mbps",
        "is_fmas": True,
    }


# ---------------------------------------------------------------------------
# Structural assertions
# ---------------------------------------------------------------------------

def test_email_subject_line(base_details):
    body = gmail.format_bookout_email(base_details)
    assert "Subject: Book out Request | Unit 42 Atlantic Beach" in body


def test_email_to_line(base_details):
    body = gmail.format_bookout_email(base_details)
    assert body.startswith("To: accounts@atec.co.za\n")


def test_email_includes_all_required_fields(base_details):
    body = gmail.format_bookout_email(base_details)
    today = datetime.date.today().strftime("%Y-%m-%d")
    for fragment in [
        "Item: ONT-GPON-1",
        "Serial Number: SN-0001",
        f"Date: {today}",
        "Name: John Smith",
        "Contact: +27 82 555 1234",
        "Site: Atlantic Beach",
        "Unit: Unit 42",
        "Address: 12 Seaview Rd, Melkbos",
        "ISP: Vumatel",
        "Speed: 200/200 Mbps",
    ]:
        assert fragment in body, f"missing fragment: {fragment!r}"


def test_fmas_intro_line(base_details):
    base_details["is_fmas"] = True
    body = gmail.format_bookout_email(base_details)
    assert "Please book out the following item for the FMAS client below." in body
    assert "for the client below." not in body  # disambiguate strictly


def test_direct_atec_intro_line(base_details):
    base_details["is_fmas"] = False
    body = gmail.format_bookout_email(base_details)
    assert "Please book out the following item for the client below." in body
    assert "FMAS client" not in body


# ---------------------------------------------------------------------------
# Account number handling
# ---------------------------------------------------------------------------

def test_account_line_included_when_provided(base_details):
    base_details["account_number"] = "AB-0042"
    body = gmail.format_bookout_email(base_details)
    assert "Account: AB-0042" in body


def test_account_line_omitted_when_missing(base_details):
    # Key absent entirely
    assert "account_number" not in base_details
    body = gmail.format_bookout_email(base_details)
    assert "Account:" not in body


def test_account_line_omitted_when_none(base_details):
    base_details["account_number"] = None
    body = gmail.format_bookout_email(base_details)
    assert "Account:" not in body


def test_account_line_omitted_when_empty_string(base_details):
    base_details["account_number"] = ""
    body = gmail.format_bookout_email(base_details)
    assert "Account:" not in body


# ---------------------------------------------------------------------------
# No manual sign-off — Gmail signature handles that
# ---------------------------------------------------------------------------

def test_no_manual_sign_off(base_details):
    body = gmail.format_bookout_email(base_details)
    for forbidden in ["Regards,", "Kind regards", "Thanks,", "Thank you,", "Sincerely"]:
        assert forbidden not in body, f"unexpected sign-off fragment: {forbidden}"


# ---------------------------------------------------------------------------
# Print helper is side-effect only
# ---------------------------------------------------------------------------

def test_print_bookout_email_outputs_full_body(base_details, capsys):
    gmail.print_bookout_email(base_details)
    out = capsys.readouterr().out
    assert "COPY THIS EMAIL" in out
    assert "Serial Number: SN-0001" in out
