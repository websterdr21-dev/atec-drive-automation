"""
Ticket + serial-label extraction via Claude API.
Target: utils/extract.py
"""

import json
from unittest.mock import MagicMock

import pytest

from utils import extract


def _set_response_text(client, text):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    client.messages.create.return_value = resp


# ---------------------------------------------------------------------------
# extract_client_details — ticket parsing
# ---------------------------------------------------------------------------

def test_extract_client_details_returns_all_fields(
    mock_anthropic, sample_ticket_text, extracted_client_details
):
    _set_response_text(mock_anthropic, json.dumps(extracted_client_details))

    result = extract.extract_client_details(sample_ticket_text)
    assert result == extracted_client_details
    # Sanity-check the required keys.
    for key in [
        "full_name", "phone", "site_name", "unit_number",
        "address", "isp", "speed", "account_number",
    ]:
        assert key in result


def test_extract_client_details_strips_markdown_fences(mock_anthropic):
    """Claude sometimes wraps JSON in ```json fences — handler must cope."""
    payload = '```json\n{"full_name": "Jane", "phone": null, "site_name": null,' \
              '"unit_number": null, "address": null, "isp": null,' \
              '"speed": null, "account_number": null}\n```'
    _set_response_text(mock_anthropic, payload)
    assert extract.extract_client_details("foo")["full_name"] == "Jane"


def test_extract_client_details_handles_missing_account_number(mock_anthropic):
    payload = {
        "full_name": "A", "phone": "B", "site_name": "C", "unit_number": "D",
        "address": "E", "isp": "F", "speed": "G", "account_number": None,
    }
    _set_response_text(mock_anthropic, json.dumps(payload))
    result = extract.extract_client_details("ticket without account")
    assert result["account_number"] is None


def test_extract_client_details_passes_ticket_into_prompt(
    mock_anthropic, sample_ticket_text
):
    _set_response_text(mock_anthropic, '{"full_name": null, "phone": null,'
        '"site_name": null, "unit_number": null, "address": null, "isp": null,'
        '"speed": null, "account_number": null}')
    extract.extract_client_details(sample_ticket_text)

    call = mock_anthropic.messages.create.call_args
    messages = call.kwargs["messages"]
    user_content = messages[0]["content"]
    assert sample_ticket_text in user_content
    # Prompt must request the expected keys
    for key in ["full_name", "phone", "site_name", "unit_number",
                "address", "isp", "speed", "account_number"]:
        assert key in user_content


def test_extract_client_details_propagates_json_error(mock_anthropic):
    _set_response_text(mock_anthropic, "not JSON at all")
    with pytest.raises(json.JSONDecodeError):
        extract.extract_client_details("x")


# ---------------------------------------------------------------------------
# extract_serial_from_photo
# ---------------------------------------------------------------------------

def test_extract_serial_from_photo_returns_serial_and_item(
    mock_anthropic, tmp_jpeg
):
    _set_response_text(
        mock_anthropic,
        '{"serial_number": "SN-0001", "item_code": "ONT-GPON-1"}',
    )
    result = extract.extract_serial_from_photo(str(tmp_jpeg))
    assert result == {"serial_number": "SN-0001", "item_code": "ONT-GPON-1"}


def test_extract_serial_from_photo_sends_image_as_base64(
    mock_anthropic, tmp_jpeg
):
    _set_response_text(
        mock_anthropic,
        '{"serial_number": "X", "item_code": "Y"}',
    )
    extract.extract_serial_from_photo(str(tmp_jpeg))

    call = mock_anthropic.messages.create.call_args
    messages = call.kwargs["messages"]
    parts = messages[0]["content"]
    image_part = next(p for p in parts if p["type"] == "image")
    assert image_part["source"]["type"] == "base64"
    assert image_part["source"]["media_type"] == "image/jpeg"
    assert image_part["source"]["data"]  # non-empty base64 payload


def test_extract_serial_handles_png(mock_anthropic, tmp_path):
    png = tmp_path / "label.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    _set_response_text(
        mock_anthropic, '{"serial_number": "Z", "item_code": "Z"}'
    )
    extract.extract_serial_from_photo(str(png))

    call = mock_anthropic.messages.create.call_args
    parts = call.kwargs["messages"][0]["content"]
    image_part = next(p for p in parts if p["type"] == "image")
    assert image_part["source"]["media_type"] == "image/png"


def test_extract_serial_handles_null_item_code(mock_anthropic, tmp_jpeg):
    _set_response_text(
        mock_anthropic, '{"serial_number": "ABC", "item_code": null}'
    )
    result = extract.extract_serial_from_photo(str(tmp_jpeg))
    assert result["item_code"] is None
    assert result["serial_number"] == "ABC"
