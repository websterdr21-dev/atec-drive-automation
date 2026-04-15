"""
Shared test fixtures for the ATEC Stock Bookout test suite.

No network, no real Google/Anthropic calls. A FakeDriveService stands in
for googleapiclient.discovery.build("drive", "v3"). The unittest.mock
library mocks Anthropic responses.
"""

from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import openpyxl
import pytest

# Make project root importable as tests live in a sibling package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# FakeDriveService — minimal in-memory stand-in for googleapiclient Drive v3
# ---------------------------------------------------------------------------

FOLDER_MIME = "application/vnd.google-apps.folder"


class _Req:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


def _match_query(q: str, record: dict) -> bool:
    """Interpret a subset of Drive v3 query syntax that this codebase uses."""
    if not q:
        return True

    m = re.search(r"mimeType='([^']+)'", q)
    if m and record["mimeType"] != m.group(1):
        return False

    m = re.search(r"name='([^']+)'", q)
    if m and record["name"] != m.group(1):
        return False

    m = re.search(r"name contains '([^']+)'", q)
    if m and m.group(1) not in record["name"]:
        return False

    m = re.search(r"'([^']+)' in parents", q)
    if m and m.group(1) not in record["parents"]:
        return False

    if "trashed=false" in q and record["trashed"]:
        return False

    return True


class FakeDriveService:
    """Tiny fake of the Drive v3 service. Tracks create/update calls."""

    def __init__(self):
        self._next = 0
        self.records: dict[str, dict] = {}
        self.content: dict[str, bytes] = {}
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.list_queries: list[str] = []

    # ---- helpers used by tests to seed state ----
    def _new_id(self) -> str:
        self._next += 1
        return f"id{self._next}"

    def add_folder(self, name: str, parent_id: str | None = None) -> str:
        fid = self._new_id()
        self.records[fid] = {
            "id": fid,
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id] if parent_id else [],
            "trashed": False,
        }
        return fid

    def add_file(
        self,
        name: str,
        parent_id: str,
        content: bytes = b"",
        mime: str = "application/octet-stream",
    ) -> str:
        fid = self._new_id()
        self.records[fid] = {
            "id": fid,
            "name": name,
            "mimeType": mime,
            "parents": [parent_id],
            "trashed": False,
        }
        self.content[fid] = content
        return fid

    # ---- Drive API surface ----
    def files(self):
        return _FakeFilesAPI(self)


class _FakeFilesAPI:
    def __init__(self, drive: FakeDriveService):
        self.d = drive

    def list(self, q=None, **kw):
        self.d.list_queries.append(q or "")
        matches = [
            r for r in self.d.records.values() if _match_query(q, r)
        ]
        if kw.get("orderBy") == "name":
            matches.sort(key=lambda x: x["name"])
        return _Req(
            {"files": [{"id": r["id"], "name": r["name"]} for r in matches]}
        )

    def create(self, body=None, media_body=None, **kw):
        self.d.create_calls.append({"body": body, "media_body": media_body})
        fid = self.d._new_id()
        self.d.records[fid] = {
            "id": fid,
            "name": body["name"],
            "mimeType": body.get("mimeType", "application/octet-stream"),
            "parents": body.get("parents", []),
            "trashed": False,
        }
        if media_body is not None:
            self.d.content[fid] = b"UPLOADED"
        return _Req({"id": fid, "webViewLink": f"https://drive.google.com/file/d/{fid}/view"})

    def update(self, fileId=None, media_body=None, **kw):
        self.d.update_calls.append({"fileId": fileId, "media_body": media_body})
        return _Req({"id": fileId})

    def get_media(self, fileId=None, **kw):
        return ("media_request", fileId)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SHARED_DRIVE_ID = "SHARED_DRIVE_ID_123"


@pytest.fixture
def drive_id():
    return SHARED_DRIVE_ID


@pytest.fixture
def fake_drive(drive_id) -> FakeDriveService:
    """An empty fake Drive with the Shared-Drive root set up."""
    svc = FakeDriveService()
    # Seed the Shared Drive "root" — in real life drive_id IS the parent id
    # of top-level items. Our fake allows parents[0] == drive_id to work.
    svc.records[drive_id] = {
        "id": drive_id,
        "name": "Atec Cape Town",
        "mimeType": FOLDER_MIME,
        "parents": [],
        "trashed": False,
    }
    return svc


@pytest.fixture
def seeded_drive(fake_drive, drive_id) -> FakeDriveService:
    """A fake drive with the standard ATEC tree seeded."""
    sites = fake_drive.add_folder("Sites", drive_id)
    fmas = fake_drive.add_folder("FMAS", sites)
    stock_sheets = fake_drive.add_folder("Stock Sheets", drive_id)
    active = fake_drive.add_folder(
        "Stock Sheets (Currently in use)", stock_sheets
    )
    fake_drive.ids = {
        "sites": sites,
        "fmas": fmas,
        "stock_sheets": stock_sheets,
        "active": active,
    }
    return fake_drive


# ---------------------------------------------------------------------------
# Workbook fixtures
# ---------------------------------------------------------------------------

def _make_workbook_bytes(rows: list[list]) -> bytes:
    """Build a single-sheet xlsx where row 0 is the title and row 1 is headers."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def sample_sheet_bytes() -> bytes:
    """A realistic Serial Number Listing workbook."""
    return _make_workbook_bytes([
        ["Serial Number Listing — CPT"],  # title row
        ["Serial Number", "Item Code", "Current Account", "Date Last Move"],
        ["SN-0001", "ONT-GPON-1", "Stock", None],
        ["SN-0002", "ONT-GPON-2", "Unit 5 Atlantic Beach", "2026-01-15"],
        [200254233608, "ROUTER-AX", "Stock", None],  # numeric-stored serial
    ])


@pytest.fixture
def other_sheet_bytes() -> bytes:
    """A second sheet used to prove the search walks every file."""
    return _make_workbook_bytes([
        ["Serial Number Listing — FMAS"],
        ["Serial Number", "Item Code", "Current Account", "Date Last Move"],
        ["SN-9999", "ONT-XGS", "Stock", None],
    ])


# ---------------------------------------------------------------------------
# Image / photo fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_jpeg(tmp_path) -> Path:
    p = tmp_path / "label.jpg"
    # Minimal JPEG header/footer — not a valid image but good enough for
    # MediaFileUpload to accept a file path.
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9")
    return p


# ---------------------------------------------------------------------------
# Ticket fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ticket_text() -> str:
    return (
        "NEW INSTALL — ATEC\n"
        "Client: John Smith\n"
        "Phone: +27 82 555 1234\n"
        "Site: Atlantic Beach Estate\n"
        "Unit: 42\n"
        "Address: 12 Seaview Rd, Melkbos, Cape Town, 7441\n"
        "ISP: Vumatel\n"
        "Speed: 200/200 Mbps\n"
        "Account: AB-0042\n"
    )


@pytest.fixture
def extracted_client_details() -> dict:
    return {
        "full_name": "John Smith",
        "phone": "+27 82 555 1234",
        "site_name": "Atlantic Beach Estate",
        "unit_number": "42",
        "address": "12 Seaview Rd, Melkbos, Cape Town, 7441",
        "isp": "Vumatel",
        "speed": "200/200 Mbps",
        "account_number": "AB-0042",
    }


# ---------------------------------------------------------------------------
# Anthropic mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_anthropic(monkeypatch):
    """
    Replace anthropic.Anthropic with a mock whose messages.create returns
    a configurable text payload. Also clears the cached CLIENT in extract.py.
    """
    import utils.extract as extract_mod

    # Reset module-level client cache
    monkeypatch.setattr(extract_mod, "CLIENT", None, raising=False)

    fake_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text="{}")]  # overridden per-test
    fake_client.messages.create.return_value = response

    def _factory(*args, **kwargs):
        return fake_client

    monkeypatch.setattr(extract_mod.anthropic, "Anthropic", _factory)
    return fake_client


# ---------------------------------------------------------------------------
# Env guards — tests must never read live creds
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _safe_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SHARED_DRIVE_ID", SHARED_DRIVE_ID)
    monkeypatch.setenv("SERVICE_ACCOUNT_PATH", "/nonexistent/service_account.json")
