"""
Photo naming + upload behaviour.
Target: utils/photos.py
"""

from unittest.mock import patch

import pytest

from utils import photos


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

def test_next_index_first_upload_uses_base_name():
    assert photos._next_index(set(), "01_Serial_Number") == "01_Serial_Number.jpg"


def test_next_index_appends_numeric_suffix_when_base_exists():
    existing = {"01_Serial_Number.jpg"}
    assert photos._next_index(existing, "01_Serial_Number") == "01_Serial_Number_02.jpg"


def test_next_index_skips_occupied_suffixes():
    existing = {
        "01_Serial_Number.jpg",
        "01_Serial_Number_02.jpg",
        "01_Serial_Number_03.jpg",
    }
    assert photos._next_index(existing, "01_Serial_Number") == "01_Serial_Number_04.jpg"


def test_next_install_index_first_is_one():
    assert photos._next_install_index(set()) == 1


def test_next_install_index_respects_existing():
    existing = {"03_Installation_01.jpg", "03_Installation_02.jpg"}
    assert photos._next_install_index(existing) == 3


def test_next_install_index_fills_gap():
    existing = {"03_Installation_01.jpg", "03_Installation_03.jpg"}
    assert photos._next_install_index(existing) == 2


# ---------------------------------------------------------------------------
# PHOTO_TYPES convention — lock the filenames we promise to uploaders
# ---------------------------------------------------------------------------

def test_photo_types_match_spec():
    assert photos.PHOTO_TYPES["serial"] == "01_Serial_Number.jpg"
    assert photos.PHOTO_TYPES["ont"] == "02_ONT_Router_Placement.jpg"
    assert photos.PHOTO_TYPES["installation"] == "03_Installation_{:02d}.jpg"
    assert photos.PHOTO_TYPES["device"] == "04_Device_Photo.jpg"
    assert photos.PHOTO_TYPES["speed"] == "05_Speed_Test.jpg"


# ---------------------------------------------------------------------------
# upload_bookout_photos
# ---------------------------------------------------------------------------

def _make_folder(drive, drive_id):
    sites = drive.add_folder("Sites", drive_id)
    fmas = drive.add_folder("FMAS", sites)
    site = drive.add_folder("AB", fmas)
    unit = drive.add_folder("Unit 1", site)
    return unit


def test_upload_bookout_uploads_serial_only_when_device_missing(
    fake_drive, drive_id, tmp_jpeg
):
    folder = _make_folder(fake_drive, drive_id)

    with patch("utils.photos.MediaFileUpload") as mock_media:
        mock_media.return_value = object()  # doesn't touch disk
        uploaded = photos.upload_bookout_photos(
            fake_drive, folder, str(tmp_jpeg), device_photo_path=None,
            drive_id=drive_id,
        )

    names = [n for (n, _) in uploaded]
    assert names == ["01_Serial_Number.jpg"]


def test_upload_bookout_uploads_serial_and_device_when_both_provided(
    fake_drive, drive_id, tmp_jpeg, tmp_path
):
    folder = _make_folder(fake_drive, drive_id)
    dev = tmp_path / "dev.jpg"
    dev.write_bytes(b"\xff\xd8\xff\xd9")

    with patch("utils.photos.MediaFileUpload") as mock_media:
        mock_media.return_value = object()
        uploaded = photos.upload_bookout_photos(
            fake_drive, folder, str(tmp_jpeg), str(dev), drive_id=drive_id,
        )

    assert [n for (n, _) in uploaded] == [
        "01_Serial_Number.jpg",
        "04_Device_Photo.jpg",
    ]


def test_upload_bookout_appends_suffix_when_base_exists(
    fake_drive, drive_id, tmp_jpeg
):
    folder = _make_folder(fake_drive, drive_id)
    fake_drive.add_file("01_Serial_Number.jpg", folder, mime="image/jpeg")

    with patch("utils.photos.MediaFileUpload") as mock_media:
        mock_media.return_value = object()
        uploaded = photos.upload_bookout_photos(
            fake_drive, folder, str(tmp_jpeg), drive_id=drive_id,
        )

    assert [n for (n, _) in uploaded] == ["01_Serial_Number_02.jpg"]


# ---------------------------------------------------------------------------
# upload_post_install_photos — multi-install numbering
# ---------------------------------------------------------------------------

def test_post_install_numbers_multiple_installation_photos_sequentially(
    fake_drive, drive_id, tmp_path
):
    folder = _make_folder(fake_drive, drive_id)
    paths = []
    for i in range(3):
        p = tmp_path / f"i{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xd9")
        paths.append(str(p))

    with patch("utils.photos.MediaFileUpload") as mock_media:
        mock_media.return_value = object()
        uploaded = photos.upload_post_install_photos(
            fake_drive, folder,
            ont_path=None, installation_paths=paths, speed_path=None,
            drive_id=drive_id,
        )

    assert [n for (n, _) in uploaded] == [
        "03_Installation_01.jpg",
        "03_Installation_02.jpg",
        "03_Installation_03.jpg",
    ]


def test_post_install_continues_numbering_past_existing_installs(
    fake_drive, drive_id, tmp_path
):
    folder = _make_folder(fake_drive, drive_id)
    fake_drive.add_file("03_Installation_01.jpg", folder, mime="image/jpeg")
    fake_drive.add_file("03_Installation_02.jpg", folder, mime="image/jpeg")
    new = tmp_path / "n.jpg"
    new.write_bytes(b"\xff\xd8\xff\xd9")

    with patch("utils.photos.MediaFileUpload") as mock_media:
        mock_media.return_value = object()
        uploaded = photos.upload_post_install_photos(
            fake_drive, folder,
            installation_paths=[str(new)], drive_id=drive_id,
        )
    assert uploaded[0][0] == "03_Installation_03.jpg"


def test_post_install_uploads_ont_and_speed_with_fixed_names(
    fake_drive, drive_id, tmp_jpeg
):
    folder = _make_folder(fake_drive, drive_id)
    with patch("utils.photos.MediaFileUpload") as mock_media:
        mock_media.return_value = object()
        uploaded = photos.upload_post_install_photos(
            fake_drive, folder,
            ont_path=str(tmp_jpeg),
            installation_paths=[],
            speed_path=str(tmp_jpeg),
            drive_id=drive_id,
        )

    names = [n for (n, _) in uploaded]
    assert names == [
        "02_ONT_Router_Placement.jpg",
        "05_Speed_Test.jpg",
    ]


def test_post_install_returns_nothing_when_all_paths_none(
    fake_drive, drive_id
):
    folder = _make_folder(fake_drive, drive_id)
    uploaded = photos.upload_post_install_photos(
        fake_drive, folder,
        ont_path=None, installation_paths=None, speed_path=None,
        drive_id=drive_id,
    )
    assert uploaded == []


# ---------------------------------------------------------------------------
# upload_photo — mime inference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext,expected", [
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".png", "image/png"),
])
def test_upload_photo_infers_mime_from_extension(
    fake_drive, drive_id, tmp_path, ext, expected
):
    folder = _make_folder(fake_drive, drive_id)
    p = tmp_path / f"file{ext}"
    p.write_bytes(b"\x00")

    with patch("utils.photos.MediaFileUpload") as mock_media:
        mock_media.return_value = object()
        photos.upload_photo(fake_drive, folder, str(p), f"01_Serial_Number{ext}")

    assert mock_media.call_args.kwargs["mimetype"] == expected
