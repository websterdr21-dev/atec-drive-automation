"""
Email message formatter for stock bookout.
Prints a ready-to-copy email — no sending, no API.
"""

import datetime


def format_bookout_email(details: dict) -> str:
    """
    Return a formatted email string ready to copy.

    Expected keys:
        item_code, serial_number, full_name, phone, site_name,
        unit_number, address, isp, speed, account_number (optional)
    """
    today = datetime.date.today().strftime("%Y-%m-%d")

    client_line = (
        "Please book out the following item for the FMAS client below."
        if details.get("is_fmas")
        else "Please book out the following item for the client below."
    )

    lines = [
        f"To: accounts@atec.co.za",
        f"Subject: Book out Request | {details['unit_number']} {details['site_name']}",
        "",
        "Good day,",
        "",
        client_line,
        "",
        f"Item: {details['item_code']}",
        f"Serial Number: {details['serial_number']}",
        f"Date: {today}",
        "",
        "Client Details:",
        f"Name: {details['full_name']}",
        f"Contact: {details['phone']}",
        f"Site: {details['site_name']}",
        f"Unit: {details['unit_number']}",
        f"Address: {details['address']}",
        f"ISP: {details['isp']}",
        f"Speed: {details['speed']}",
    ]

    if details.get("account_number"):
        lines.append(f"Account: {details['account_number']}")

    return "\n".join(lines)


def print_bookout_email(details: dict):
    """Print the formatted email with a clear separator for easy copying."""
    email = format_bookout_email(details)
    border = "-" * 60
    print(f"\n{border}")
    print("  COPY THIS EMAIL")
    print(border)
    print(email)
    print(border)
