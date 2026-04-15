"""
ATEC Stock Bookout Automation CLI

Usage:
    python bookout.py bookout
    python bookout.py add-photos
    python bookout.py check-stock
"""

import os
import sys
from utils.env import load as load_env

load_env()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_env():
    sa_path = os.getenv("SERVICE_ACCOUNT_PATH")
    drive_id = os.getenv("SHARED_DRIVE_ID")
    if not sa_path or not drive_id:
        print("[ERROR] Missing SERVICE_ACCOUNT_PATH or SHARED_DRIVE_ID in .env")
        sys.exit(1)
    if not os.path.exists(sa_path):
        print(f"[ERROR] service_account.json not found at: {sa_path}")
        sys.exit(1)
    return sa_path, drive_id


def _confirm(prompt="Confirm? (y/n): ") -> bool:
    return input(prompt).strip().lower() in ("y", "yes")


def _prompt(label, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val if val else default


def _ask_site_type(site_name):
    print(f"\nIs '{site_name}' an FMAS site or a direct ATEC site?")
    print("  1. FMAS")
    print("  2. Direct ATEC")
    while True:
        choice = input("Enter 1 or 2: ").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("  Please enter 1 or 2.")


def _browse_to_folder(service, drive_id, start_folder_id, start_path):
    """
    Interactive CLI folder browser for ATEC sites.
    Returns (folder_id, folder_url) of the selected destination.

    Controls:
      [number]  Navigate into that subfolder
      u         Upload here (select current folder)
      b         Go back one level
    """
    from utils.drive_folders import list_subfolders

    # Stack of (id, name) pairs representing the current path
    stack = list(start_path)   # start_path: list of {id, name} dicts

    while True:
        current_id   = stack[-1]["id"]
        current_name = stack[-1]["name"]
        breadcrumb   = " / ".join(s["name"] for s in stack)

        print(f"\n  Path: {breadcrumb}")

        subfolders = list_subfolders(service, current_id, drive_id)

        if subfolders:
            print(f"  Subfolders:")
            for i, f in enumerate(subfolders, 1):
                print(f"    {i}. {f['name']}")
        else:
            print("  (no subfolders)")

        options = "[number to open"
        if len(stack) > 1:
            options += " | b = go back"
        options += " | u = upload here]: "

        choice = input(f"  {options}").strip().lower()

        if choice == "u":
            folder_url = f"https://drive.google.com/drive/folders/{current_id}"
            print(f"\n  Selected: {breadcrumb}")
            return current_id, folder_url

        if choice == "b":
            if len(stack) > 1:
                stack.pop()
            else:
                print("  Already at the top — can't go back further.")
            continue

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(subfolders):
                stack.append({"id": subfolders[idx]["id"], "name": subfolders[idx]["name"]})
            else:
                print(f"  Invalid number. Enter 1–{len(subfolders)}.")
            continue

        print("  Invalid input.")


def _divider():
    print("\n" + "-" * 60)


# ---------------------------------------------------------------------------
# bookout command
# ---------------------------------------------------------------------------

def cmd_bookout():
    from utils.auth import get_drive_service
    from utils.sheets import find_serial_number, update_stock_row
    from utils.drive_folders import get_unit_folder
    from utils.photos import upload_bookout_photos
    from utils.gmail import print_bookout_email
    from utils.extract import extract_client_details, extract_serial_from_photo

    sa_path, drive_id = _get_env()
    service = get_drive_service(sa_path)

    # ------------------------------------------------------------------
    # Step 1: Extract client details from ticket
    # ------------------------------------------------------------------
    _divider()
    print("STEP 1 — Paste ticket text (blank line + Enter when done):")
    print()
    lines = []
    while True:
        line = input()
        if line == "" and lines and lines[-1] == "":
            break
        lines.append(line)
    ticket_text = "\n".join(lines).strip()

    if not ticket_text:
        print("[ERROR] No ticket text provided.")
        sys.exit(1)

    print("\nExtracting client details...")
    details = extract_client_details(ticket_text)

    _divider()
    print("EXTRACTED CLIENT DETAILS — please review:\n")
    fields = [
        ("Full Name",    "full_name"),
        ("Phone",        "phone"),
        ("Site",         "site_name"),
        ("Unit",         "unit_number"),
        ("Address",      "address"),
        ("ISP",          "isp"),
        ("Speed",        "speed"),
        ("Account No.",  "account_number"),
    ]
    for label, key in fields:
        print(f"  {label:<14} {details.get(key) or '(not found)'}")

    print()
    if not _confirm("Are these details correct? (y/n): "):
        print("\nCorrect the details below (press Enter to keep current value):")
        for label, key in fields:
            val = _prompt(f"  {label}", default=details.get(key) or "")
            details[key] = val if val else details.get(key)

    # ------------------------------------------------------------------
    # Step 2: FMAS or direct site
    # ------------------------------------------------------------------
    _divider()
    is_fmas = _ask_site_type(details["site_name"])

    # ------------------------------------------------------------------
    # Step 3: Serial number from photo
    # ------------------------------------------------------------------
    _divider()
    print("STEP 3 — Serial number photo")
    print("Enter the path to the device label photo:")
    serial_photo_path = input("  Photo path: ").strip().strip('"')

    if not os.path.exists(serial_photo_path):
        print(f"[ERROR] File not found: {serial_photo_path}")
        sys.exit(1)

    print("\nReading label...")
    extracted = extract_serial_from_photo(serial_photo_path)

    serial_number = extracted.get("serial_number") or ""
    item_code = extracted.get("item_code") or ""

    print(f"\n  Serial Number : {serial_number or '(not found)'}")
    print(f"  Item Code     : {item_code or '(not found)'}")
    print()

    if not _confirm("Are these correct? (y/n): "):
        serial_number = _prompt("  Serial Number", default=serial_number)
        item_code = _prompt("  Item Code", default=item_code)

    if not serial_number:
        print("[ERROR] Serial number is required.")
        sys.exit(1)
    if not item_code:
        item_code = _prompt("  Item Code (required)", default="")
        if not item_code:
            print("[ERROR] Item code is required.")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 4: Search stock sheet — determine normal or swap mode
    # ------------------------------------------------------------------
    _divider()
    print(f"STEP 4 — Searching stock sheets for: {serial_number}")
    sheet_result = find_serial_number(service, drive_id, serial_number)
    is_swap = sheet_result is None

    if is_swap:
        print(f"\n  [NOT FOUND] '{serial_number}' was not found in any stock sheet.")
        print("  This will be treated as a SWAP / REPLACEMENT unit.")
        print("  Stock sheet update and accounts email will be skipped.")
        print()
        if not _confirm("  Proceed as swap? (y/n): "):
            print("Aborted.")
            sys.exit(0)
    else:
        print(f"  Found in : {sheet_result['file_name']} / {sheet_result['sheet_name']}")
        print(f"  Row      : {sheet_result['row_index']}")

    # ------------------------------------------------------------------
    # Step 5: Update stock sheet (skipped for swaps)
    # ------------------------------------------------------------------
    if not is_swap:
        _divider()
        current_account = f"{details.get('unit_number', '')} {details.get('site_name', '')}".strip()
        print(f"STEP 5 — Updating stock sheet...")
        print(f"  Setting Current Account to: {current_account}")
        if not _confirm("Proceed? (y/n): "):
            print("Aborted.")
            sys.exit(0)
        update_stock_row(service, drive_id, serial_number, current_account)
        print("  Stock sheet updated.")
    else:
        print("\n  [SWAP] Stock sheet update skipped.")

    # ------------------------------------------------------------------
    # Step 6: Drive folder — FMAS auto-created, ATEC browsed
    # ------------------------------------------------------------------
    _divider()
    if is_fmas:
        print("STEP 6 — Creating Drive folder (FMAS)...")
        folder_id, folder_url, site_created, unit_created = get_unit_folder(
            service, drive_id, details["site_name"], details["unit_number"], is_fmas=True
        )
        if site_created:
            print(f"  Created site folder : {details['site_name']}")
        if unit_created:
            print(f"  Created unit folder : {details['unit_number']}")
        if not site_created and not unit_created:
            print(f"  Opened existing folder.")
        print(f"  URL: {folder_url}")
    else:
        from utils.drive_folders import get_atec_site_folder
        print(f"STEP 6 — Navigate to unit folder (ATEC)")
        site_id, site_created = get_atec_site_folder(service, drive_id, details["site_name"])
        if site_created:
            print(f"  Created site folder: {details['site_name']}")
        print("  Use the browser below to navigate to the correct unit folder.")
        folder_id, folder_url = _browse_to_folder(
            service, drive_id, site_id,
            start_path=[{"id": site_id, "name": details["site_name"]}]
        )

    # ------------------------------------------------------------------
    # Step 7: Upload photos
    # ------------------------------------------------------------------
    _divider()
    print("STEP 7 — Upload photos")
    print("  01_Serial_Number.jpg will be uploaded from the label photo.")

    device_photo_path = None
    device_input = input("\n  Device photo path (Enter to skip): ").strip().strip('"')
    if device_input and os.path.exists(device_input):
        device_photo_path = device_input
    elif device_input:
        print(f"  [WARN] File not found, skipping device photo: {device_input}")

    print("\nUploading...")
    uploaded = upload_bookout_photos(service, folder_id, serial_photo_path, device_photo_path)
    for name, _ in uploaded:
        print(f"  Uploaded: {name}")

    # ------------------------------------------------------------------
    # Step 8: Print email (skipped for swaps)
    # ------------------------------------------------------------------
    _divider()
    if not is_swap:
        details["serial_number"] = serial_number
        details["item_code"] = item_code
        details["is_fmas"] = is_fmas
        print_bookout_email(details)
    else:
        print("  [SWAP] Accounts email skipped.")

    _divider()
    print("Bookout complete." + (" [SWAP MODE — no sheet update, no email]" if is_swap else ""))
    print(f"Drive folder: {folder_url}")


# ---------------------------------------------------------------------------
# add-photos command
# ---------------------------------------------------------------------------

def cmd_add_photos():
    from utils.auth import get_drive_service
    from utils.drive_folders import get_unit_folder
    from utils.photos import upload_post_install_photos, upload_photo, PHOTO_TYPES

    sa_path, drive_id = _get_env()
    service = get_drive_service(sa_path)

    _divider()
    print("ADD POST-INSTALL PHOTOS\n")
    site_name = _prompt("Site name")
    unit_number = _prompt("Unit number")
    is_fmas = _ask_site_type(site_name)

    if is_fmas:
        folder_id, folder_url, _, _ = get_unit_folder(service, drive_id, site_name, unit_number, is_fmas=True)
    else:
        from utils.drive_folders import get_atec_site_folder
        site_id, site_created = get_atec_site_folder(service, drive_id, site_name)
        if site_created:
            print(f"  Created site folder: {site_name}")
        print("  Navigate to the unit folder:")
        folder_id, folder_url = _browse_to_folder(
            service, drive_id, site_id,
            start_path=[{"id": site_id, "name": site_name}]
        )
    print(f"\nFolder: {folder_url}")

    _divider()
    print("Enter photo paths (press Enter to skip each):\n")

    ont = input("  02_ONT_Router_Placement photo: ").strip().strip('"') or None
    if ont and not os.path.exists(ont):
        print(f"  [WARN] Not found, skipping: {ont}")
        ont = None

    install_paths = []
    i = 1
    while True:
        p = input(f"  03_Installation_{i:02d} photo (Enter to stop): ").strip().strip('"')
        if not p:
            break
        if os.path.exists(p):
            install_paths.append(p)
            i += 1
        else:
            print(f"  [WARN] Not found, skipping: {p}")

    speed = input("  05_Speed_Test photo: ").strip().strip('"') or None
    if speed and not os.path.exists(speed):
        print(f"  [WARN] Not found, skipping: {speed}")
        speed = None

    if not any([ont, install_paths, speed]):
        print("\nNo valid photos provided. Nothing uploaded.")
        return

    print("\nUploading...")
    uploaded = upload_post_install_photos(service, folder_id, ont, install_paths, speed)
    for name, _ in uploaded:
        print(f"  Uploaded: {name}")

    print(f"\nDone. Folder: {folder_url}")


# ---------------------------------------------------------------------------
# check-stock command
# ---------------------------------------------------------------------------

def cmd_check_stock():
    from utils.auth import get_drive_service
    from utils.sheets import find_serial_number

    sa_path, drive_id = _get_env()
    service = get_drive_service(sa_path)

    serial = input("Serial number: ").strip()
    if not serial:
        print("[ERROR] No serial number entered.")
        sys.exit(1)

    print(f"\nSearching all Serial Number Listing sheets for: {serial}")
    result = find_serial_number(service, drive_id, serial)

    if result is None:
        print(f"\n[NOT FOUND] '{serial}' was not found in any sheet.")
        return

    print(f"\n[FOUND]")
    print(f"  Sheet file : {result['file_name']}")
    print(f"  Tab        : {result['sheet_name']}")
    print(f"  Row        : {result['row_index']}")
    print()
    for h, v in zip(result["headers"], result["row_values"]):
        if v is not None and str(v).strip():
            print(f"  {str(h) if h else '(no header)':<30} {v}")


# ---------------------------------------------------------------------------

COMMANDS = {
    "bookout":     cmd_bookout,
    "add-photos":  cmd_add_photos,
    "check-stock": cmd_check_stock,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python bookout.py <command>")
        print("Commands:", ", ".join(COMMANDS))
        sys.exit(1)

    COMMANDS[sys.argv[1]]()
