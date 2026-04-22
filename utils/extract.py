"""
Claude-powered extraction utilities.

- extract_client_details: parse a ticket text into structured client fields
- extract_serial_from_photo: read a serial number label photo and return serial + item code
"""

import base64
import json
import os
import anthropic

CLIENT = None


def _get_client():
    global CLIENT
    if CLIENT is None:
        CLIENT = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return CLIENT


# ---------------------------------------------------------------------------
# Ticket text extraction
# ---------------------------------------------------------------------------

TICKET_PROMPT = """Extract the client details from the following ticket text.
Return ONLY a JSON object with these exact keys (use null if not found):
{
  "full_name": "...",
  "phone": "...",
  "site_name": "...",
  "unit_number": "...",
  "address": "...",
  "isp": "...",
  "speed": "...",
  "account_number": "..."
}
Ticket text:
"""


def extract_client_details(ticket_text: str) -> dict:
    client = _get_client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": TICKET_PROMPT + ticket_text}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ---------------------------------------------------------------------------
# Serial number label photo extraction
# ---------------------------------------------------------------------------

SERIAL_PROMPT = """This is a photo of a networking device or its serial number label.

Find the serial number and item/model code on the label. They are typically labelled:
  Serial number: S/N, SN, Serial No, Serial Number
  Item/model code: P/N, PN, Part No, Part Number, Model, Item Code

Rules:
- Copy characters EXACTLY as printed — preserve all leading zeros and hyphens
- If a character is ambiguous (e.g. 0 vs O, 1 vs I), favour digits in serial fields
- If the label is partially obscured or the photo is blurry, return your best reading

Return ONLY a JSON object with these exact keys (use null if not found):
{
  "serial_number": "...",
  "item_code": "..."
}
No explanation — JSON only."""


def extract_serial_from_photo(image_path: str) -> dict:
    """
    Send an image to Claude vision and extract serial number + item code.
    Supports JPEG and PNG.
    """
    ext = os.path.splitext(image_path)[1].lower()
    media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    client = _get_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": SERIAL_PROMPT},
                ],
            }
        ],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
