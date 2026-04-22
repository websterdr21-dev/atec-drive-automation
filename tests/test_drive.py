"""
Drive navigation + folder creation / dedup.
Target: utils/drive_folders.py
"""

import pytest

from utils import drive_folders


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Back _CACHE with a tmp_path file so tests never share singleton state."""
    cache = drive_folders._AtecFolderCache(str(tmp_path / "_test_cache.json"))
    monkeypatch.setattr(drive_folders, "_CACHE", cache)


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


# ---------------------------------------------------------------------------
# _AtecFolderCache + cache-aware get_atec_site_folder
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """
    Inject a tmp_path-backed `_AtecFolderCache` as the module-level
    singleton. Prevents tests from writing to the real
    `data/atec_folder_cache.json`. Returns the cache instance so tests
    can pre-populate or inspect it.
    """
    cache_path = tmp_path / "atec_folder_cache.json"
    cache = drive_folders._AtecFolderCache(str(cache_path))
    monkeypatch.setattr(drive_folders, "_CACHE", cache)
    yield cache
    monkeypatch.setattr(drive_folders, "_CACHE", None)


def test_get_atec_site_folder_cache_hit_skips_drive(
    seeded_drive, drive_id, tmp_cache
):
    svc = seeded_drive
    tmp_cache.set("Waterfront", "prefetched-id-xyz")

    lists_before = len(svc.list_queries)
    creates_before = len(svc.create_calls)

    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfront"
    )

    assert site_id == "prefetched-id-xyz"
    assert created is False
    assert len(svc.list_queries) == lists_before  # no Drive list call
    assert len(svc.create_calls) == creates_before  # no create call


def test_get_atec_site_folder_cache_miss_writes_to_cache(
    seeded_drive, drive_id, tmp_cache, tmp_path
):
    svc = seeded_drive
    creates_before = len(svc.create_calls)

    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Newsite"
    )

    assert created is True
    assert len(svc.create_calls) == creates_before + 1
    # Cache now populated in memory...
    assert tmp_cache.get("Newsite") == site_id
    # ...and persisted to disk.
    cache_file = tmp_path / "atec_folder_cache.json"
    assert cache_file.exists()
    import json
    on_disk = json.loads(cache_file.read_text(encoding="utf-8"))
    assert on_disk == {"Newsite": site_id}


def test_get_atec_site_folder_cache_miss_finds_existing_before_create(
    seeded_drive, drive_id, tmp_cache
):
    svc = seeded_drive
    existing = svc.add_folder("Waterfront", svc.ids["sites"])
    creates_before = len(svc.create_calls)

    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfront"
    )

    # Reused the existing folder — no duplicate created (CACHE-03).
    assert site_id == existing
    assert created is False
    assert len(svc.create_calls) == creates_before
    # Still cached for next time.
    assert tmp_cache.get("Waterfront") == existing


def test_get_atec_site_folder_creates_cache_file_on_first_write(
    seeded_drive, drive_id, tmp_path, monkeypatch
):
    # Point the singleton at a path whose PARENT dir also doesn't exist.
    cache_path = tmp_path / "nested" / "dir" / "atec_folder_cache.json"
    cache = drive_folders._AtecFolderCache(str(cache_path))
    monkeypatch.setattr(drive_folders, "_CACHE", cache)

    assert not cache_path.exists()
    assert not cache_path.parent.exists()

    drive_folders.get_atec_site_folder(seeded_drive, drive_id, "Anysite")

    # Both the nested dir and the file now exist (CACHE-05).
    assert cache_path.parent.exists()
    assert cache_path.exists()

    monkeypatch.setattr(drive_folders, "_CACHE", None)


# ---------------------------------------------------------------------------
# _fuzzy_match_subfolder — standalone
# ---------------------------------------------------------------------------

def test_fuzzy_match_subfolder_returns_canonical_on_close_match(fake_drive, drive_id):
    parent = fake_drive.add_folder("Sites", drive_id)
    fake_drive.add_folder("Waterfront Estate", parent)
    result = drive_folders._fuzzy_match_subfolder(
        fake_drive, drive_id, parent, "Watrefront Estate"
    )
    assert result == "Waterfront Estate"  # original case preserved


def test_fuzzy_match_subfolder_reverse_prefix_drive_longer(fake_drive, drive_id):
    # Drive has "Burgundy Estate", ticket says "Burgundy" (ratio 0.70, below cutoff)
    parent = fake_drive.add_folder("Sites", drive_id)
    fake_drive.add_folder("Burgundy Estate", parent)
    result = drive_folders._fuzzy_match_subfolder(
        fake_drive, drive_id, parent, "Burgundy"
    )
    assert result == "Burgundy Estate"


def test_fuzzy_match_subfolder_reverse_prefix_table_view(fake_drive, drive_id):
    parent = fake_drive.add_folder("Sites", drive_id)
    fake_drive.add_folder("Table View Gardens", parent)
    result = drive_folders._fuzzy_match_subfolder(
        fake_drive, drive_id, parent, "Table View"
    )
    assert result == "Table View Gardens"


def test_fuzzy_match_subfolder_returns_none_when_no_close_match(fake_drive, drive_id):
    parent = fake_drive.add_folder("Sites", drive_id)
    fake_drive.add_folder("Waterfront", parent)
    result = drive_folders._fuzzy_match_subfolder(
        fake_drive, drive_id, parent, "Completely Different Place"
    )
    assert result is None


def test_fuzzy_match_subfolder_returns_none_for_empty_parent(fake_drive, drive_id):
    parent = fake_drive.add_folder("Sites", drive_id)
    result = drive_folders._fuzzy_match_subfolder(
        fake_drive, drive_id, parent, "Anything"
    )
    assert result is None


# ---------------------------------------------------------------------------
# get_atec_site_folder — fuzzy branches
# ---------------------------------------------------------------------------

def test_get_atec_site_folder_fuzzy_match_reuses_existing_uncached(
    seeded_drive, drive_id, tmp_cache
):
    svc = seeded_drive
    existing = svc.add_folder("Waterfront", svc.ids["sites"])
    creates_before = len(svc.create_calls)

    # "Waterfrontt" fuzzy-matches "Waterfront" — no new folder created
    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfrontt"
    )

    assert site_id == existing
    assert created is False
    assert len(svc.create_calls) == creates_before
    assert tmp_cache.get("Waterfront") == existing


def test_get_atec_site_folder_fuzzy_match_with_canonical_already_cached(
    seeded_drive, drive_id, tmp_cache
):
    svc = seeded_drive
    svc.add_folder("Waterfront", svc.ids["sites"])
    tmp_cache.set("Waterfront", "cached-wf-id")
    creates_before = len(svc.create_calls)

    # Misspelling misses cache; fuzzy finds canonical; canonical IS cached
    # → returns cached ID, writes alias for misspelling
    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfrontt"
    )

    assert site_id == "cached-wf-id"
    assert created is False
    assert tmp_cache.get("Waterfrontt") == "cached-wf-id"
    assert len(svc.create_calls) == creates_before


def test_get_atec_site_folder_no_fuzzy_match_creates_new(
    seeded_drive, drive_id, tmp_cache
):
    svc = seeded_drive
    svc.add_folder("Waterfront", svc.ids["sites"])
    creates_before = len(svc.create_calls)

    # Name too different — no match, new folder created
    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Completely Different Place"
    )

    assert created is True
    assert len(svc.create_calls) == creates_before + 1
    assert svc.records[site_id]["name"] == "Completely Different Place"


# ---------------------------------------------------------------------------
# get_unit_folder — ATEC fuzzy path (is_fmas=False)
# ---------------------------------------------------------------------------

def test_get_unit_folder_atec_fuzzy_match_reuses_site(seeded_drive, drive_id):
    svc = seeded_drive
    existing_site = svc.add_folder("Waterfront", svc.ids["sites"])
    creates_before = len(svc.create_calls)

    # Misspelled site — fuzzy resolves to existing, only unit folder created
    unit_id, url, site_created, unit_created = drive_folders.get_unit_folder(
        svc, drive_id, "Waterfrontt", "5", is_fmas=False
    )

    assert site_created is False
    assert unit_created is True
    assert svc.records[unit_id]["parents"] == [existing_site]
    assert len(svc.create_calls) == creates_before + 1  # unit only


def test_get_atec_site_folder_stale_recovery_after_delete(
    seeded_drive, drive_id, tmp_cache
):
    svc = seeded_drive

    # Pre-populate with a bogus ID — simulates a cache entry for a
    # folder that has since been deleted/moved on Drive.
    tmp_cache.set("Waterfront", "stale-id-123")

    # First call returns the stale ID directly — by design (D-04, D-05):
    # the cache layer does not pre-validate.
    site_id, created = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfront"
    )
    assert site_id == "stale-id-123"
    assert created is False

    # Simulate the caller detecting staleness at actual use (D-05/D-06)
    # and invoking the documented recovery API.
    drive_folders._get_cache().delete("Waterfront")
    assert drive_folders._get_cache().get("Waterfront") is None

    # Retry — now falls through to find-or-create and repopulates cache.
    new_id, created2 = drive_folders.get_atec_site_folder(
        svc, drive_id, "Waterfront"
    )
    assert new_id != "stale-id-123"
    assert created2 is True
    assert drive_folders._get_cache().get("Waterfront") == new_id
