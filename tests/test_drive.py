"""
Drive navigation + folder creation / dedup.
Target: utils/drive_folders.py
"""

import pytest

from utils import drive_folders


# ---------------------------------------------------------------------------
# list_subfolders
# ---------------------------------------------------------------------------

def test_list_subfolders_returns_only_folder_children(fake_drive, drive_id):
    parent = fake_drive.add_folder("Sites", drive_id)
    fake_drive.add_folder("FMAS", parent)
    fake_drive.add_folder("Direct Site", parent)
    # Non-folder file should be excluded by the mimeType filter.
    fake_drive.add_file("notes.txt", parent, mime="text/plain")

    result = drive_folders.list_subfolders(fake_drive, parent, drive_id)
    names = {r["name"] for r in result}

    assert names == {"FMAS", "Direct Site"}


def test_list_subfolders_sorted_by_name(fake_drive, drive_id):
    parent = fake_drive.add_folder("Sites", drive_id)
    fake_drive.add_folder("Zeta", parent)
    fake_drive.add_folder("Alpha", parent)

    result = drive_folders.list_subfolders(fake_drive, parent, drive_id)
    assert [r["name"] for r in result] == ["Alpha", "Zeta"]


# ---------------------------------------------------------------------------
# _format_unit_name — unit number normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("42", "Unit 42"),
    ("  42  ", "Unit 42"),
    ("Unit 42", "Unit 42"),
    ("unit 42", "Unit 42"),
    ("UNIT 42", "Unit 42"),
    ("Unit B7", "Unit B7"),
])
def test_format_unit_name_never_doubles_prefix(given, expected):
    assert drive_folders._format_unit_name(given) == expected


# ---------------------------------------------------------------------------
# get_unit_folder — FMAS path
# ---------------------------------------------------------------------------

def test_get_unit_folder_fmas_creates_site_and_unit_when_absent(
    seeded_drive, drive_id
):
    svc = seeded_drive
    unit_id, url, site_created, unit_created = drive_folders.get_unit_folder(
        svc, drive_id, "Atlantic Beach", "42", is_fmas=True
    )

    assert site_created is True
    assert unit_created is True
    assert url == f"https://drive.google.com/drive/folders/{unit_id}"

    # Folder parented under FMAS, unit parented under that site.
    site_rec = next(
        r for r in svc.records.values() if r["name"] == "Atlantic Beach"
    )
    assert site_rec["parents"] == [svc.ids["fmas"]]

    unit_rec = svc.records[unit_id]
    assert unit_rec["name"] == "Unit 42"
    assert unit_rec["parents"] == [site_rec["id"]]


def test_get_unit_folder_fmas_opens_existing_site_and_unit(
    seeded_drive, drive_id
):
    svc = seeded_drive
    existing_site = svc.add_folder("Atlantic Beach", svc.ids["fmas"])
    existing_unit = svc.add_folder("Unit 42", existing_site)
    calls_before = len(svc.create_calls)

    unit_id, _, site_created, unit_created = drive_folders.get_unit_folder(
        svc, drive_id, "Atlantic Beach", "42", is_fmas=True
    )

    assert site_created is False
    assert unit_created is False
    assert unit_id == existing_unit
    assert len(svc.create_calls) == calls_before  # zero create calls


def test_get_unit_folder_never_creates_duplicates_on_repeat_calls(
    seeded_drive, drive_id
):
    svc = seeded_drive
    drive_folders.get_unit_folder(svc, drive_id, "Atlantic Beach", "42", is_fmas=True)
    drive_folders.get_unit_folder(svc, drive_id, "Atlantic Beach", "42", is_fmas=True)
    drive_folders.get_unit_folder(svc, drive_id, "Atlantic Beach", "42", is_fmas=True)

    site_matches = [r for r in svc.records.values() if r["name"] == "Atlantic Beach"]
    unit_matches = [r for r in svc.records.values() if r["name"] == "Unit 42"]
    assert len(site_matches) == 1
    assert len(unit_matches) == 1


def test_get_unit_folder_normalises_unit_prefix(seeded_drive, drive_id):
    svc = seeded_drive
    _, _, _, _ = drive_folders.get_unit_folder(
        svc, drive_id, "Site X", "Unit 7", is_fmas=True
    )
    unit = next(r for r in svc.records.values() if r["name"] == "Unit 7")
    assert unit is not None


# ---------------------------------------------------------------------------
# Root-folder invariants: Sites / FMAS are never created
# ---------------------------------------------------------------------------

def test_get_unit_folder_raises_if_sites_missing(fake_drive, drive_id):
    # drive root exists but no Sites folder
    with pytest.raises(FileNotFoundError, match="Sites"):
        drive_folders.get_unit_folder(
            fake_drive, drive_id, "Site X", "1", is_fmas=True
        )


def test_get_unit_folder_raises_if_fmas_missing(fake_drive, drive_id):
    fake_drive.add_folder("Sites", drive_id)  # but no FMAS
    with pytest.raises(FileNotFoundError, match="FMAS"):
        drive_folders.get_unit_folder(
            fake_drive, drive_id, "Site X", "1", is_fmas=True
        )


def test_get_unit_folder_never_attempts_to_create_sites_or_fmas(
    seeded_drive, drive_id
):
    svc = seeded_drive
    drive_folders.get_unit_folder(
        svc, drive_id, "New Site", "9", is_fmas=True
    )
    created_names = [c["body"]["name"] for c in svc.create_calls]
    assert "Sites" not in created_names
    assert "FMAS" not in created_names


# ---------------------------------------------------------------------------
# get_atec_site_folder — direct (non-FMAS) path
# ---------------------------------------------------------------------------

def test_get_atec_site_folder_creates_if_missing(seeded_drive, drive_id):
    svc = seeded_drive
    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfront"
    )
    assert created is True
    assert svc.records[site_id]["parents"] == [svc.ids["sites"]]


def test_get_atec_site_folder_reuses_existing(seeded_drive, drive_id):
    svc = seeded_drive
    existing = svc.add_folder("Waterfront", svc.ids["sites"])
    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfront"
    )
    assert created is False
    assert site_id == existing


def test_get_atec_site_folder_raises_if_sites_missing(fake_drive, drive_id):
    with pytest.raises(FileNotFoundError, match="Sites"):
        drive_folders.get_atec_site_folder(fake_drive, drive_id, "Any")
