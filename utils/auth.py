"""Shared Google API authentication using service account credentials.

Credential resolution order (checked at call time):
1. ``service_account_path`` argument — explicit file path (CLI / tests).
2. ``SERVICE_ACCOUNT_JSON`` env var — raw JSON string (Railway / hosted env).
3. ``SERVICE_ACCOUNT_PATH`` env var — file path fallback.
"""

import json
import os
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.compose",
]


def get_credentials():
    """Fetches credentials from local file or Railway Base64 env var."""
    scopes = [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/documents',
        'https://www.googleapis.com/auth/gmail.compose'
    ]

    # Check for local file (Your Desktop)
    if os.path.exists("service_account.json"):
        return service_account.Credentials.from_service_account_file(
            "service_account.json", scopes=scopes
        )

    # Check for Railway Environment Variable
    b64_key = os.getenv("GCP_SERVICE_ACCOUNT_B64")
    if b64_key:
        decoded_key = base64.b64decode(b64_key).decode("utf-8")
        return service_account.Credentials.from_service_account_info(
            json.loads(decoded_key), scopes=scopes
        )

    raise FileNotFoundError("No Google credentials found (file or env var)!")


def get_drive_service(service_account_path: str | None = None):
    creds = get_credentials(service_account_path)
    return build("drive", "v3", credentials=creds)


def get_sheets_service(service_account_path: str | None = None):
    creds = get_credentials(service_account_path)
    return build("sheets", "v4", credentials=creds)


def get_docs_service(service_account_path: str | None = None):
    creds = get_credentials(service_account_path)
    return build("docs", "v1", credentials=creds)


def get_gmail_service(
    service_account_path: str | None = None, impersonate_email: str = ""
):
    """
    Returns a Gmail API service using domain-wide delegation.
    The service account must have DWD enabled and the Gmail scope granted
    in Google Workspace Admin → Security → API Controls.
    impersonate_email: the Gmail address to send/draft as.
    """
    creds = get_credentials(service_account_path)
    if impersonate_email:
        creds = creds.with_subject(impersonate_email)
    return build("gmail", "v1", credentials=creds)
