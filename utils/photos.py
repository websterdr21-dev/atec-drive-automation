"""
Photo upload helpers.

Photo naming convention:
  01_Serial_Number.jpg       — bookout
  04_Device_Photo.jpg        — bookout (optional)
  02_ONT_Router_Placement.jpg — post-install
  03_Installation_01.jpg     — post-install (increments for multiples)
  05_Speed_Test.jpg          — post-install
"""

import os
import re
from googleapiclient.http import MediaFileUpload

PHOTO_TYPES = {
    "serial":       "01_Serial_Number.jpg",
    "device":       "04_Device_Photo.jpg",
    "ont":          "02_ONT_Router_Placement.jpg",
    "installation": "03_Installation_{:02d}.jpg",  # formatted with index
    "speed":        "05_Speed_Test.jpg",
}


def list_existing_filenames(service, folder_id, drive_id=None):
    """Return a set of filenames already in the folder."""
    import os
    did = drive_id or os.getenv("SHARED_DRIVE_ID", "")
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        corpora="drive",
        driveId=did,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(name)",
    ).execute()
    return {f["name"] for f in results.get("files", [])}


def _next_index(existing_names, base):
    """
    Return the next available filename for a base like '01_Serial_Number'.
    First upload → '01_Serial_Number.jpg'
    Second upload → '01_Serial_Number_02.jpg', then _03, etc.
    """
    ext = ".jpg"
    # Check plain name first
    if f"{base}{ext}" not in existing_names:
        return f"{base}{ext}"
    # Find highest existing suffix
    used = {1}
    for name in existing_names:
        m = re.match(rf"{re.escape(base)}_(\d+){re.escape(ext)}", name)
        if m:
            used.add(int(m.group(1)))
    i = 2
    while i in used:
        i += 1
    return f"{base}_{i:02d}{ext}"


def _next_install_index(existing_names):
    """Return the next available installation photo index (1-based)."""
    used = set()
    for name in existing_names:
        m = re.match(r"03_Installation_(\d+)\.", name)
        if m:
            used.add(int(m.group(1)))
    i = 1
    while i in used:
        i += 1
    return i


def upload_photo(service, folder_id, local_path, drive_filename):
    """
    Upload a single photo to a Drive folder.
    Returns the uploaded file's id and web view link.
    """
    mime = "image/jpeg"
    ext = os.path.splitext(local_path)[1].lower()
    if ext == ".png":
        mime = "image/png"
    elif ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"

    media = MediaFileUpload(local_path, mimetype=mime, resumable=False)
    result = service.files().create(
        body={
            "name": drive_filename,
            "parents": [folder_id],
        },
        media_body=media,
        supportsAllDrives=True,
        fields="id, webViewLink",
    ).execute()
    return result["id"], result.get("webViewLink", "")


def upload_bookout_photos(service, folder_id, serial_photo_path, device_photo_path=None, drive_id=None):
    """
    Upload bookout photos. If a file already exists with the same base name,
    the new file gets a numeric suffix (_02, _03 …) rather than overwriting.
    Returns list of (drive_filename, file_id) tuples.
    """
    existing = list_existing_filenames(service, folder_id, drive_id)
    uploaded = []

    if serial_photo_path:
        name = _next_index(existing, "01_Serial_Number")
        fid, _ = upload_photo(service, folder_id, serial_photo_path, name)
        uploaded.append((name, fid))
        existing.add(name)

    if device_photo_path:
        name = _next_index(existing, "04_Device_Photo")
        fid, _ = upload_photo(service, folder_id, device_photo_path, name)
        uploaded.append((name, fid))
        existing.add(name)

    return uploaded


def upload_post_install_photos(service, folder_id, ont_path=None, installation_paths=None, speed_path=None, drive_id=None):
    """
    Upload post-install photos. All types use next-available numbering so
    existing photos are never overwritten — just appended as _02, _03 etc.
    Returns list of (drive_filename, file_id) tuples.
    """
    existing = list_existing_filenames(service, folder_id, drive_id)
    uploaded = []

    if ont_path:
        name = _next_index(existing, "02_ONT_Router_Placement")
        fid, _ = upload_photo(service, folder_id, ont_path, name)
        uploaded.append((name, fid))
        existing.add(name)

    install_idx = _next_install_index(existing)
    for path in (installation_paths or []):
        name = PHOTO_TYPES["installation"].format(install_idx)
        fid, _ = upload_photo(service, folder_id, path, name)
        uploaded.append((name, fid))
        existing.add(name)
        install_idx += 1

    if speed_path:
        name = _next_index(existing, "05_Speed_Test")
        fid, _ = upload_photo(service, folder_id, speed_path, name)
        uploaded.append((name, fid))
        existing.add(name)

    return uploaded
