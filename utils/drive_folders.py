"""
Drive folder helpers for install photo organisation.

Folder paths:
  FMAS site:   Sites → FMAS → [Site Name] → Unit [Unit Number]   (automated)
  ATEC site:   Sites → [Site Name] → <user browses to destination> (interactive)
"""

FOLDER_MIME = "application/vnd.google-apps.folder"


def list_subfolders(service, folder_id, drive_id):
    """Return sorted list of {id, name} for all subfolders in folder_id."""
    results = service.files().list(
        q=(
            f"mimeType='{FOLDER_MIME}' and "
            f"'{folder_id}' in parents and "
            f"trashed=false"
        ),
        corpora="drive",
        driveId=drive_id,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id, name)",
        orderBy="name",
    ).execute()
    return results.get("files", [])


def get_atec_site_folder(service, drive_id, site_name):
    """
    Find or create Sites/[site_name] for a direct ATEC site.
    Returns (folder_id, created).
    Does NOT create anything deeper — user browses from here.
    """
    sites_id = _find_folder_exact(service, "Sites", drive_id, drive_id)
    if not sites_id:
        raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")
    return _find_or_create_folder(service, site_name, sites_id, drive_id)


def _find_or_create_folder(service, name, parent_id, drive_id):
    """
    Return (folder_id, created) for a folder with exact name under parent_id.
    Creates it if it doesn't exist. Never duplicates.
    """
    results = service.files().list(
        q=(
            f"mimeType='{FOLDER_MIME}' and "
            f"name='{name}' and "
            f"'{parent_id}' in parents and "
            f"trashed=false"
        ),
        corpora="drive",
        driveId=drive_id,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id, name)",
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"], False

    folder = service.files().create(
        body={
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        },
        supportsAllDrives=True,
        fields="id",
    ).execute()
    return folder["id"], True


def _find_folder_exact(service, name, parent_id, drive_id):
    """Return folder id or None — never creates."""
    results = service.files().list(
        q=(
            f"mimeType='{FOLDER_MIME}' and "
            f"name='{name}' and "
            f"'{parent_id}' in parents and "
            f"trashed=false"
        ),
        corpora="drive",
        driveId=drive_id,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id, name)",
    ).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _format_unit_name(unit_number: str) -> str:
    """
    Always return 'Unit X' format.
    Strips any existing 'Unit ' prefix first so we never get 'Unit Unit X'.
    """
    stripped = unit_number.strip()
    if stripped.lower().startswith("unit "):
        stripped = stripped[5:].strip()
    return f"Unit {stripped}"


def get_unit_folder(service, drive_id, site_name, unit_number, is_fmas):
    """
    Find or create the unit folder and return its (folder_id, folder_url).

    Folder name is always formatted as 'Unit [unit_number]'.

    is_fmas=True  → Sites/FMAS/[site_name]/Unit [unit_number]
    is_fmas=False → Sites/[site_name]/Unit [unit_number]
    """
    unit_folder_name = _format_unit_name(unit_number)

    # Sites (always exists)
    sites_id = _find_folder_exact(service, "Sites", drive_id, drive_id)
    if not sites_id:
        raise FileNotFoundError("'Sites' folder not found in Shared Drive root.")

    if is_fmas:
        fmas_id = _find_folder_exact(service, "FMAS", sites_id, drive_id)
        if not fmas_id:
            raise FileNotFoundError("'FMAS' folder not found inside 'Sites'.")
        parent_id = fmas_id
    else:
        parent_id = sites_id

    site_id, site_created = _find_or_create_folder(service, site_name, parent_id, drive_id)
    unit_id, unit_created = _find_or_create_folder(service, unit_folder_name, site_id, drive_id)

    url = f"https://drive.google.com/drive/folders/{unit_id}"
    return unit_id, url, site_created, unit_created
