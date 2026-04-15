"""
Step 3: Verify sheet search can find the active folder and read all
Serial Number Listing sheets.

Usage:
    python test_sheets.py
"""

import os, sys
from utils.env import load as load_env
from utils.auth import get_drive_service
from utils.sheets import get_active_sheet_folder, list_serial_number_sheets

load_env()

def main():
    sa_path = os.getenv("SERVICE_ACCOUNT_PATH")
    drive_id = os.getenv("SHARED_DRIVE_ID")

    service = get_drive_service(sa_path)

    print("Looking for active 'Currently in use' folder...")
    folder_id, folder_name = get_active_sheet_folder(service, drive_id)
    print(f"  Found: {folder_name}  ({folder_id})\n")

    print("Listing Serial Number Listing sheets...")
    sheets = list_serial_number_sheets(service, folder_id, drive_id)

    if not sheets:
        print("  [WARN] No Serial Number Listing files found.")
        sys.exit(1)

    for s in sheets:
        print(f"  - {s['name']}  ({s['id']})")

    print(f"\nFound {len(sheets)} sheet(s). Sheet search test PASSED.")

if __name__ == "__main__":
    main()
