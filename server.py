"""
ATEC Stock Bookout — FastAPI backend
Run: uvicorn server:app --reload --port 8000
"""

import os
import json
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from utils.env import load as load_env
load_env()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Telegram bot — initialised on startup if token is configured
# ---------------------------------------------------------------------------

_bot_app = None  # python-telegram-bot Application instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if token:
        from utils.telegram_bot import build_application
        _bot_app = build_application(token)
        await _bot_app.initialize()
        await _bot_app.start()

        domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

        if domain:
            webhook_url = f"https://{domain}/telegram/webhook"
            await _bot_app.bot.set_webhook(url=webhook_url, secret_token=secret or None)
            logger.info("Telegram webhook registered: %s", webhook_url)
        elif os.getenv("TELEGRAM_USE_POLLING", "").lower() == "true":
            # Local dev: run polling inside the same event loop
            await _bot_app.updater.start_polling()
            logger.info("Telegram bot started in polling mode")
        else:
            logger.info("Telegram bot initialised — no webhook or polling configured")
    else:
        logger.info("TELEGRAM_BOT_TOKEN not set — bot disabled")

    yield  # application runs

    if _bot_app:
        if os.getenv("TELEGRAM_USE_POLLING", "").lower() == "true":
            await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()

# ---------------------------------------------------------------------------
# Auth middleware — simple shared password
# ---------------------------------------------------------------------------

APP_PASSWORD = os.getenv("APP_PASSWORD", "")

class PasswordMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Allow health check and Telegram webhook without auth
        if request.url.path in ("/health", "/telegram/webhook"):
            return await call_next(request)
        # Check session cookie
        if APP_PASSWORD:
            token = request.cookies.get("atec_auth")
            if request.url.path == "/api/login":
                return await call_next(request)
            if token != APP_PASSWORD:
                # Return 401 for API routes, redirect for page routes
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"error": "Unauthorised"}, status_code=401)
                # Serve the login page for all other routes
                html = Path("static/index.html").read_text(encoding="utf-8")
                resp = HTMLResponse(html)
                return resp
        return await call_next(request)


app = FastAPI(title="ATEC Bookout", lifespan=lifespan)
app.add_middleware(PasswordMiddleware)


# Global handler so unhandled exceptions always return JSON, never HTML
from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse
import traceback as _traceback

@app.exception_handler(Exception)
async def _global_exc(request: _Request, exc: Exception):
    return _JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}", "trace": _traceback.format_exc()[-1000:]},
    )


# ---------------------------------------------------------------------------
# Telegram webhook endpoint
# ---------------------------------------------------------------------------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive updates from Telegram. Verifies X-Telegram-Bot-Api-Secret-Token header."""
    if _bot_app is None:
        return JSONResponse({"error": "Bot not configured"}, status_code=503)

    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if secret:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_secret != secret:
            return JSONResponse({"error": "Forbidden"}, status_code=403)

    from telegram import Update
    data = await request.json()
    update = Update.de_json(data, _bot_app.bot)
    await _bot_app.process_update(update)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Lazy service helpers
# ---------------------------------------------------------------------------

def get_drive():
    from utils.auth import get_drive_service
    return get_drive_service()

def drive_id():
    return os.getenv("SHARED_DRIVE_ID")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    password = body.get("password", "")
    if not APP_PASSWORD or password == APP_PASSWORD:
        resp = JSONResponse({"ok": True})
        resp.set_cookie("atec_auth", APP_PASSWORD, httponly=True, samesite="strict")
        return resp
    raise HTTPException(status_code=401, detail="Wrong password")


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("atec_auth")
    return resp


# ---------------------------------------------------------------------------
# Dashboard — recent bookouts
# ---------------------------------------------------------------------------

@app.get("/api/dashboard")
def dashboard():
    from utils.sheets import get_active_sheet_folder, list_serial_number_sheets, _download_xlsx
    service = get_drive()
    did = drive_id()

    folder_id, folder_name = get_active_sheet_folder(service, did)
    sheets = list_serial_number_sheets(service, folder_id, did)

    rows = []
    for file_info in sheets:
        wb = _download_xlsx(service, file_info["id"])
        ws = wb.worksheets[0]

        # Find header row
        header_row_idx = None
        headers = []
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if row and str(row[0]).strip().lower() == "serial number":
                header_row_idx = r_idx
                headers = list(row)
                break

        if header_row_idx is None:
            continue

        headers_lower = [str(h).strip().lower() if h else "" for h in headers]

        def col(name):
            return next((i for i, h in enumerate(headers_lower) if name in h), None)

        serial_col   = col("serial number")
        item_col     = col("item code")
        date_col     = col("date last move")
        location_col = col("current location")
        account_col  = col("current account")

        for ws_row in ws.iter_rows(min_row=header_row_idx + 1, values_only=False):
            vals = [c.value for c in ws_row]
            if not vals or not vals[0]:
                continue
            val0 = str(vals[0]).strip().lower()
            if "serial number count" in val0 or "item code" in val0:
                continue

            def cell(idx, _vals=vals):
                if idx is None or idx >= len(_vals):
                    return ""
                v = _vals[idx]
                if v is None:
                    return ""
                if hasattr(v, "strftime"):
                    return v.strftime("%Y-%m-%d")
                return str(int(v)) if isinstance(v, float) and v == int(v) else str(v)

            # Detect red fill on first cell
            booked_out = False
            try:
                fill = ws_row[0].fill
                if fill and fill.fill_type and fill.fill_type != "none":
                    rgb = fill.fgColor.rgb if fill.fgColor else ""
                    if rgb in ("FFFF0000", "00FF0000"):
                        booked_out = True
            except Exception:
                pass

            rows.append({
                "sheet":    file_info["name"].replace("Serial Number Listing", "").strip("_ ").replace(".xlsx", ""),
                "serial":   cell(serial_col),
                "item":     cell(item_col),
                "date":     cell(date_col),
                "location": cell(location_col),
                "account":  cell(account_col),
                "booked":   booked_out,
            })

    # Sort by date desc
    rows.sort(key=lambda r: r["date"], reverse=True)
    return {"rows": rows[:200], "active_folder": folder_name}


# ---------------------------------------------------------------------------
# Extract client details from ticket text
# ---------------------------------------------------------------------------

@app.post("/api/extract-ticket")
async def extract_ticket(request: Request):
    body = await request.json()
    ticket = body.get("ticket", "").strip()
    if not ticket:
        raise HTTPException(400, "No ticket text provided")
    from utils.extract import extract_client_details
    details = extract_client_details(ticket)
    return details


# ---------------------------------------------------------------------------
# Extract serial from photo
# ---------------------------------------------------------------------------

@app.post("/api/extract-serial")
async def extract_serial(photo: UploadFile = File(...)):
    from utils.extract import extract_serial_from_photo
    suffix = Path(photo.filename).suffix or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await photo.read())
        tmp_path = tmp.name
    try:
        result = extract_serial_from_photo(tmp_path)
    finally:
        os.unlink(tmp_path)
    return result


# ---------------------------------------------------------------------------
# Check stock
# ---------------------------------------------------------------------------

@app.get("/api/check-stock")
def check_stock(serial: str):
    from utils.sheets import find_serial_number
    service = get_drive()
    result = find_serial_number(service, drive_id(), serial)
    if result is None:
        return {"found": False}
    return {
        "found":     True,
        "file_name": result["file_name"],
        "sheet":     result["sheet_name"],
        "row":       result["row_index"],
        "headers":   [str(h) if h else "" for h in result["headers"]],
        "values":    [str(v) if v is not None else "" for v in result["row_values"]],
    }


# ---------------------------------------------------------------------------
# Folder browser (ATEC sites only)
# ---------------------------------------------------------------------------

@app.post("/api/create-folder")
async def create_folder(request: Request):
    body = await request.json()
    parent_id = body.get("parent_id", "").strip()
    name      = body.get("name", "").strip()
    if not parent_id or not name:
        raise HTTPException(400, "parent_id and name are required")
    try:
        from utils.drive_folders import _find_or_create_folder
        folder_id, created = _find_or_create_folder(get_drive(), name, parent_id, drive_id())
        return {
            "folder_id": folder_id,
            "name":      name,
            "created":   created,
            "url":       f"https://drive.google.com/drive/folders/{folder_id}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/browse")
def browse(folder_id: str):
    """List subfolders inside folder_id. Used by the interactive folder browser."""
    from utils.drive_folders import list_subfolders
    try:
        folders = list_subfolders(get_drive(), folder_id, drive_id())
        return {"folders": folders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/site-folder")
def site_folder(site_name: str):
    """
    Find or create Sites/[site_name] for a direct ATEC site.
    Returns the folder id and name to start the browser from.
    """
    from utils.drive_folders import get_atec_site_folder
    try:
        folder_id, created = get_atec_site_folder(get_drive(), drive_id(), site_name)
        return {
            "folder_id":   folder_id,
            "folder_name": site_name,
            "created":     created,
            "url":         f"https://drive.google.com/drive/folders/{folder_id}",
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Full bookout
# ---------------------------------------------------------------------------

@app.post("/api/bookout")
async def bookout(
    # Client details
    full_name:        str = Form(...),
    phone:            str = Form(...),
    site_name:        str = Form(...),
    unit_number:      str = Form(...),
    address:          str = Form(...),
    isp:              str = Form(...),
    speed:            str = Form(...),
    account_number:   str = Form(""),
    is_fmas:          str = Form(...),        # "true" / "false"
    serial_number:    str = Form(...),
    item_code:        str = Form(...),
    # FMAS: unit_number used to auto-create folder
    # ATEC: target_folder_id is the pre-selected destination from browser
    target_folder_id: str = Form(""),         # ATEC only — pre-selected via browser
    # Photos
    serial_photo:     UploadFile = File(...),
    device_photo:     Optional[UploadFile] = File(None),
):
    from utils.sheets import find_serial_number, update_stock_row
    from utils.drive_folders import get_unit_folder
    from utils.photos import upload_bookout_photos
    from utils.gmail import format_bookout_email

    service = get_drive()
    did = drive_id()
    fmas = is_fmas.lower() == "true"

    steps = []

    # 1. Search stock sheet — normal or swap mode
    result = find_serial_number(service, did, serial_number)
    is_swap = result is None

    if is_swap:
        steps.append(f"SWAP MODE — '{serial_number}' not in any sheet, sheet update and email skipped")
    else:
        steps.append(f"Found in {result['file_name']} / row {result['row_index']}")

    # 2. Update stock sheet (skipped for swaps)
    if not is_swap:
        current_account = f"{unit_number} {site_name}".strip()
        update_stock_row(service, did, serial_number, current_account)
        steps.append("Stock sheet updated")

    # 3. Resolve Drive folder
    if fmas:
        # FMAS: automated path — Sites/FMAS/[site]/Unit [unit]
        folder_id, folder_url, site_created, unit_created = get_unit_folder(
            service, did, site_name, unit_number, is_fmas=True
        )
        steps.append(f"Drive folder {'created' if unit_created else 'opened'}: {folder_url}")
    else:
        # ATEC: folder already selected via browser — use it directly
        if not target_folder_id:
            raise HTTPException(400, "target_folder_id is required for ATEC sites")
        folder_id = target_folder_id
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
        steps.append(f"Drive folder confirmed: {folder_url}")

    # 4. Upload photos
    tmp_files = []
    try:
        s_suffix = Path(serial_photo.filename).suffix or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=s_suffix) as f:
            f.write(await serial_photo.read())
            serial_tmp = f.name
        tmp_files.append(serial_tmp)

        device_tmp = None
        if device_photo and device_photo.filename:
            d_suffix = Path(device_photo.filename).suffix or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=d_suffix) as f:
                f.write(await device_photo.read())
                device_tmp = f.name
            tmp_files.append(device_tmp)

        uploaded = upload_bookout_photos(service, folder_id, serial_tmp, device_tmp, drive_id=did)
        steps.append(f"Uploaded {len(uploaded)} photo(s)")
    finally:
        for p in tmp_files:
            try: os.unlink(p)
            except Exception: pass

    # 5. Format email (skipped for swaps)
    email_text = None
    if not is_swap:
        details = {
            "full_name": full_name, "phone": phone, "site_name": site_name,
            "unit_number": unit_number, "address": address, "isp": isp,
            "speed": speed, "account_number": account_number,
            "serial_number": serial_number, "item_code": item_code,
            "is_fmas": fmas,
        }
        email_text = format_bookout_email(details)
        steps.append("Email ready — copy below")
    else:
        steps.append("Accounts email skipped (swap)")

    return {
        "ok":        True,
        "is_swap":   is_swap,
        "steps":     steps,
        "folder_url": folder_url,
        "email":     email_text,
    }


# ---------------------------------------------------------------------------
# Add post-install photos
# ---------------------------------------------------------------------------

@app.post("/api/add-photos")
async def add_photos(
    site_name:        str = Form(...),
    unit_number:      str = Form(""),         # required for FMAS, unused for ATEC
    is_fmas:          str = Form(...),
    target_folder_id: str = Form(""),         # ATEC only — pre-selected via browser
    ont:              Optional[UploadFile] = File(None),
    speed:            Optional[UploadFile] = File(None),
    installs:         list[UploadFile] = File(default=[]),
):
    from utils.drive_folders import get_unit_folder
    from utils.photos import upload_post_install_photos

    service = get_drive()
    did = drive_id()
    fmas = is_fmas.lower() == "true"

    if fmas:
        folder_id, folder_url, _, _ = get_unit_folder(service, did, site_name, unit_number, is_fmas=True)
    else:
        if not target_folder_id:
            raise HTTPException(400, "target_folder_id is required for ATEC sites")
        folder_id = target_folder_id
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"

    tmp_files = []
    ont_path = None
    speed_path = None
    install_paths = []

    try:
        async def save(upload):
            suffix = Path(upload.filename).suffix or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(await upload.read())
                tmp_files.append(f.name)
                return f.name

        if ont and ont.filename:
            ont_path = await save(ont)
        if speed and speed.filename:
            speed_path = await save(speed)
        for inst in installs:
            if inst and inst.filename:
                install_paths.append(await save(inst))

        uploaded = upload_post_install_photos(service, folder_id, ont_path, install_paths, speed_path, drive_id=did)
    finally:
        for p in tmp_files:
            try: os.unlink(p)
            except Exception: pass

    return {"ok": True, "uploaded": [name for name, _ in uploaded], "folder_url": folder_url}


# ---------------------------------------------------------------------------
# Serve frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
@app.get("/{full_path:path}", response_class=HTMLResponse)
def serve_frontend(full_path: str = ""):
    html_path = Path("static/index.html")
    if not html_path.exists():
        return HTMLResponse("<h1>Frontend not built yet</h1>", status_code=503)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
