"""
Connection test — lists the top-level contents of the Shared Drive.
Run this first to confirm Google Drive + Service Account access before
building any automations.

Usage:
    python test_connection.py
"""

import os
import sys
from utils.env import load as load_env
from utils.auth import get_drive_service

load_env()


def test_drive_connection():
    service_account_path = os.getenv("SERVICE_ACCOUNT_PATH")
    shared_drive_id = os.getenv("SHARED_DRIVE_ID")

    missing = []
    if not service_account_path:
        missing.append("SERVICE_ACCOUNT_PATH")
    if not shared_drive_id:
        missing.append("SHARED_DRIVE_ID")
    if missing:
        print(f"[ERROR] Missing .env values: {', '.join(missing)}")
        print("  Copy .env.example to .env and fill in your values.")
        sys.exit(1)

    if not os.path.exists(service_account_path):
        print(f"[ERROR] service_account.json not found at: {service_account_path}")
        sys.exit(1)

    print(f"Connecting to Shared Drive: {shared_drive_id}")
    print(f"Using service account: {service_account_path}\n")

    try:
        service = get_drive_service(service_account_path)

        # List top-level items in the Shared Drive
        results = (
            service.files()
            .list(
                corpora="drive",
                driveId=shared_drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id, name, mimeType, modifiedTime)",
                pageSize=20,
                q=f"'{shared_drive_id}' in parents and trashed=false",
            )
            .execute()
        )

        files = results.get("files", [])

        if not files:
            print("[OK] Connected successfully — drive is empty (no top-level items).")
        else:
            print(f"[OK] Connected successfully — found {len(files)} top-level item(s):\n")
            for f in files:
                mime = f["mimeType"].split(".")[-1]  # shorten mime type
                print(f"  [{mime:>12}]  {f['name']}  ({f['id']})")

        print("\nConnection test PASSED.")
        return True

    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        print("\nCommon causes:")
        print("  - Service account not added to the Shared Drive as Content Manager")
        print("  - Wrong SHARED_DRIVE_ID (copy from the Drive URL, not the folder URL)")
        print("  - Drive API not enabled in your Google Cloud project")
        sys.exit(1)


if __name__ == "__main__":
    test_drive_connection()
