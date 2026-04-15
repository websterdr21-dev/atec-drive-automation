"""Shared Google API authentication using service account credentials."""

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.compose",
]


def get_credentials(service_account_path: str):
    return service_account.Credentials.from_service_account_file(
        service_account_path, scopes=SCOPES
    )


def get_drive_service(service_account_path: str):
    creds = get_credentials(service_account_path)
    return build("drive", "v3", credentials=creds)


def get_sheets_service(service_account_path: str):
    creds = get_credentials(service_account_path)
    return build("sheets", "v4", credentials=creds)


def get_docs_service(service_account_path: str):
    creds = get_credentials(service_account_path)
    return build("docs", "v1", credentials=creds)


def get_gmail_service(service_account_path: str, impersonate_email: str):
    """
    Returns a Gmail API service using domain-wide delegation.
    The service account must have DWD enabled and the Gmail scope granted
    in Google Workspace Admin → Security → API Controls.
    impersonate_email: the Gmail address to send/draft as.
    """
    creds = service_account.Credentials.from_service_account_file(
        service_account_path, scopes=SCOPES
    )
    delegated = creds.with_subject(impersonate_email)
    return build("gmail", "v1", credentials=delegated)
