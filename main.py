import base64
import json
import re
from datetime import date
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import get_setting
from services import (
    build_line_payload,
    build_mutasi_pdf,
    get_master_outlets,
    get_master_products,
    get_supabase_admin_client,
    get_supabase_client,
    upload_file_to_supabase,
)

MAX_UPLOAD_MB = 200
AUTH_COOKIE_NAME = "sb_access_token"
COOKIE_SAMESITE = (get_setting("COOKIE_SAMESITE") or "lax").lower()
COOKIE_SECURE = (get_setting("COOKIE_SECURE") or "false").lower() == "true"
SUPERADMIN_EMAIL = (get_setting("SUPERADMIN_EMAIL") or "").strip().lower()
SUPERADMIN_PASSWORD = get_setting("SUPERADMIN_PASSWORD") or ""
SUPERADMIN_FULL_NAME = get_setting("SUPERADMIN_FULL_NAME") or "Superadmin"
SUPERADMIN_OUTLET = get_setting("SUPERADMIN_OUTLET") or "Cost Control"

app = FastAPI(title="Form Berita Acara Mutasi")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def on_startup():
    _ensure_superadmin_account()


def _parse_names(raw_value):
    return [name.strip() for name in (raw_value or "").split(",") if name.strip()]


def _parse_items(items_json):
    if not items_json:
        return []
    try:
        data = json.loads(items_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    items = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            qty = float(item.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            harga = float(item.get("harga") or 0)
        except (TypeError, ValueError):
            harga = 0.0
        items.append(
            {
                "product_name": str(item.get("product_name", "")).strip(),
                "kode_item": str(item.get("kode_item", "")).strip(),
                "uom": str(item.get("uom", "")).strip(),
                "qty": qty,
                "harga": harga,
            }
        )
    return items


def _normalize_outlet_id(outlet_id):
    value = str(outlet_id or "").strip()
    if not value:
        return ""
    return int(value) if value.isdigit() else value


def _get_outlet_by_id(outlet_id):
    if outlet_id in (None, ""):
        return None
    target = str(outlet_id)
    outlets = get_master_outlets()
    for outlet in outlets:
        if str(outlet.get("id")) == target:
            return outlet
    return None


def _validate_form(
    no_form,
    outlet_pengirim_id,
    outlet_penerima_id,
    tanggal,
    dibuat_list,
    diterima_list,
    items,
):
    missing = []
    if not no_form:
        missing.append("No Form")
    if not outlet_pengirim_id:
        missing.append("Outlet Pengirim")
    if not outlet_penerima_id:
        missing.append("Outlet Penerima")
    if not tanggal:
        missing.append("Tanggal Kirim")
    if not dibuat_list:
        missing.append("Dibuat Oleh")
    if not diterima_list:
        missing.append("Diterima Oleh")

    if outlet_pengirim_id and outlet_pengirim_id == outlet_penerima_id:
        return False, "Outlet pengirim dan penerima tidak boleh sama."

    if outlet_pengirim_id and not _get_outlet_by_id(outlet_pengirim_id):
        return False, "Outlet pengirim tidak ditemukan. Perbarui profil Anda."
    if outlet_penerima_id and not _get_outlet_by_id(outlet_penerima_id):
        return False, "Outlet penerima tidak ditemukan."

    non_empty_items = [
        item
        for item in items
        if item.get("product_name") or float(item.get("qty") or 0) > 0
    ]
    if not non_empty_items:
        missing.append("Minimal 1 item")
    else:
        items_valid = all(
            item.get("product_name") and float(item.get("qty") or 0) > 0
            for item in non_empty_items
        )
        if not items_valid:
            missing.append("Lengkapi Nama Item dan Kuantiti di semua baris")

    if missing:
        return False, "Lengkapi dulu: " + ", ".join(missing)
    return True, ""


def _render_form(request, message=None, status=None, profile=None, user_email=None):
    outlets = get_master_outlets()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "outlets": outlets,
            "message": message,
            "status": status,
            "today": date.today().isoformat(),
            "max_upload_mb": MAX_UPLOAD_MB,
            "profile": profile or _get_profile_for_request(request),
            "user_email": user_email,
        },
    )


def _render_success(request, message, profile=None, user_email=None):
    return templates.TemplateResponse(
        "success.html",
        {
            "request": request,
            "message": message,
            "profile": profile or _get_profile_for_request(request),
            "user_email": user_email,
        },
    )


def _get_supabase():
    return get_supabase_client()


def _get_supabase_admin():
    return get_supabase_admin_client()


def _is_superadmin(user):
    if not user or not SUPERADMIN_EMAIL:
        return False
    return (user.email or "").lower() == SUPERADMIN_EMAIL


def _ensure_superadmin_account():
    if not SUPERADMIN_EMAIL or not SUPERADMIN_PASSWORD:
        return
    supabase_admin = _get_supabase_admin()
    if not supabase_admin:
        return
    try:
        resp = supabase_admin.auth.admin.create_user(
            {
                "email": SUPERADMIN_EMAIL,
                "password": SUPERADMIN_PASSWORD,
                "email_confirm": True,
                "user_metadata": {
                    "full_name": SUPERADMIN_FULL_NAME,
                    "outlet_name": SUPERADMIN_OUTLET,
                },
                "app_metadata": {"role": "superadmin"},
            }
        )
        user = getattr(resp, "user", None)
        if user:
            _ensure_profile(
                user, full_name=SUPERADMIN_FULL_NAME, outlet_name=SUPERADMIN_OUTLET
            )
    except Exception as exc:
        if "already" not in str(exc).lower():
            print(f"Superadmin bootstrap gagal: {exc}")


def _set_auth_cookie(response, session):
    if not session or not getattr(session, "access_token", None):
        return
    max_age = getattr(session, "expires_in", None)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        session.access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=max_age,
        path="/",
    )


def _clear_auth_cookie(response):
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def _get_current_user(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    supabase = _get_supabase()
    if not supabase:
        return None
    try:
        user_response = supabase.auth.get_user(token)
        return user_response.user
    except Exception:
        return None


def _get_profile(user_id):
    if not user_id:
        return None
    supabase = _get_supabase()
    if not supabase:
        return None
    try:
        resp = (
            supabase.table("profiles")
            .select("*")
            .eq("id", user_id)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def _ensure_profile(user, full_name=None, outlet_name=None, outlet_id=None):
    if not user:
        return None
    profile = _get_profile(user.id)
    if profile:
        return profile
    supabase = _get_supabase()
    if not supabase:
        return None
    payload = {
        "id": user.id,
        "full_name": full_name or "",
        "outlet_name": outlet_name or "",
    }
    if outlet_id not in (None, ""):
        payload["outlet_id"] = outlet_id
    try:
        supabase.table("profiles").insert(payload).execute()
    except Exception:
        if "outlet_id" in payload:
            payload.pop("outlet_id", None)
            try:
                supabase.table("profiles").insert(payload).execute()
            except Exception:
                pass
    return _get_profile(user.id)


def _get_profile_for_user(user):
    if not user:
        return None
    metadata = user.user_metadata or {}
    profile = _get_profile(user.id)
    if profile:
        outlet_id = profile.get("outlet_id") or metadata.get("outlet_id")
        if outlet_id and not profile.get("outlet_id"):
            profile = {**profile, "outlet_id": outlet_id}
        if outlet_id and not profile.get("outlet_name"):
            outlet = _get_outlet_by_id(outlet_id)
            if outlet and outlet.get("name"):
                profile = {**profile, "outlet_name": outlet.get("name")}
        return profile
    outlet_id = metadata.get("outlet_id")
    outlet_name = metadata.get("outlet_name", "")
    if outlet_id and not outlet_name:
        outlet = _get_outlet_by_id(outlet_id)
        outlet_name = outlet.get("name") if outlet else ""
    return {
        "id": user.id,
        "full_name": metadata.get("full_name", ""),
        "outlet_name": outlet_name,
        "outlet_id": outlet_id,
        "email": user.email,
    }


def _get_profile_for_request(request: Request):
    user = _get_current_user(request)
    return _get_profile_for_user(user)


def _redirect_to_login(request: Request):
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(
        url=f"/login?next={quote(next_url)}",
        status_code=303,
    )


@app.get("/")
def index(request: Request):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    profile = _get_profile_for_user(user)
    status = request.query_params.get("status")
    message = request.query_params.get("message")
    return _render_form(
        request,
        message=message,
        status=status,
        profile=profile,
        user_email=user.email,
    )


@app.get("/api/products")
def api_products(request: Request, outlet_id: str | None = None):
    if not _get_current_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not outlet_id:
        return JSONResponse([])
    try:
        company_id = int(outlet_id)
    except ValueError:
        return JSONResponse([])
    products = get_master_products(company_id)
    return JSONResponse(products)


@app.get("/success")
def success(request: Request):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    message = request.query_params.get("message") or "Data berhasil disimpan."
    profile = _get_profile_for_user(user)
    return _render_success(request, message, profile=profile, user_email=user.email)


@app.get("/login")
def login(request: Request):
    if _get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    next_url = request.query_params.get("next") or "/"
    message = request.query_params.get("message")
    status = request.query_params.get("status")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "next_url": next_url,
            "message": message,
            "status": status,
        },
    )


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    supabase = _get_supabase()
    if not supabase:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next_url": next or "/",
                "message": "Supabase belum dikonfigurasi.",
                "status": "error",
            },
            status_code=400,
        )
    try:
        auth_response = supabase.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
        session = auth_response.session
        if not session:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "next_url": next or "/",
                    "message": "Login gagal. Periksa email atau password.",
                    "status": "error",
                },
                status_code=400,
            )
        user = getattr(auth_response, "user", None)
        if _is_superadmin(user):
            _ensure_profile(
                user, full_name=SUPERADMIN_FULL_NAME, outlet_name=SUPERADMIN_OUTLET
            )
        response = RedirectResponse(url=next or "/", status_code=303)
        _set_auth_cookie(response, session)
        return response
    except Exception:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next_url": next or "/",
                "message": "Login gagal. Periksa email atau password.",
                "status": "error",
            },
            status_code=400,
        )


@app.get("/register")
def register(request: Request):
    if _get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    next_url = request.query_params.get("next") or "/"
    message = request.query_params.get("message")
    status = request.query_params.get("status")
    outlets = get_master_outlets()
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "next_url": next_url,
            "message": message,
            "status": status,
            "outlets": outlets,
        },
    )


@app.post("/register")
def register_submit(
    request: Request,
    full_name: str = Form(""),
    outlet_id: str = Form(""),
    outlet_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    next: str = Form("/"),
):
    supabase = _get_supabase()
    if not supabase:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "next_url": next or "/",
                "message": "Supabase belum dikonfigurasi.",
                "status": "error",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )
    if password != confirm_password:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "next_url": next or "/",
                "message": "Password dan konfirmasi harus sama.",
                "status": "error",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )
    outlet_id_value = _normalize_outlet_id(outlet_id)
    outlet = _get_outlet_by_id(outlet_id_value)
    if not outlet:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "next_url": next or "/",
                "message": "Pilih outlet dari daftar yang tersedia.",
                "status": "error",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )
    outlet_name = outlet.get("name") or outlet_name.strip()
    try:
        auth_response = supabase.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name.strip(),
                        "outlet_name": outlet_name.strip(),
                        "outlet_id": outlet_id_value,
                    }
                },
            }
        )
        user = auth_response.user
        if user:
            _ensure_profile(
                user,
                full_name=full_name,
                outlet_name=outlet_name,
                outlet_id=outlet_id_value,
            )
        session = auth_response.session
        if not session:
            return templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "next_url": next or "/",
                    "message": "Registrasi berhasil. Silakan cek email untuk verifikasi.",
                    "status": "success",
                    "outlets": get_master_outlets(),
                },
            )
        response = RedirectResponse(url=next or "/", status_code=303)
        _set_auth_cookie(response, session)
        return response
    except Exception:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "next_url": next or "/",
                "message": "Registrasi gagal. Periksa data Anda.",
                "status": "error",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    _clear_auth_cookie(response)
    return response


@app.get("/profile")
def profile(request: Request):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    profile_data = _get_profile(user.id)
    if not profile_data:
        profile_data = _ensure_profile(
            user,
            full_name=(user.user_metadata or {}).get("full_name", ""),
            outlet_name=(user.user_metadata or {}).get("outlet_name", ""),
            outlet_id=(user.user_metadata or {}).get("outlet_id", ""),
        )
    if profile_data and not profile_data.get("outlet_id"):
        meta_outlet_id = (user.user_metadata or {}).get("outlet_id")
        if meta_outlet_id:
            profile_data = {**profile_data, "outlet_id": meta_outlet_id}
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "profile": profile_data,
            "email": user.email,
            "outlets": get_master_outlets(),
        },
    )


@app.post("/profile")
def profile_update(
    request: Request,
    full_name: str = Form(""),
    outlet_id: str = Form(""),
):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    supabase = _get_supabase()
    if not supabase:
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "profile": _get_profile(user.id),
                "email": user.email,
                "message": "Supabase belum dikonfigurasi.",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )
    outlet_id_value = _normalize_outlet_id(outlet_id)
    outlet = _get_outlet_by_id(outlet_id_value)
    if not outlet:
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "profile": _get_profile(user.id),
                "email": user.email,
                "message": "Pilih outlet dari daftar yang tersedia.",
                "status": "error",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )
    outlet_name = outlet.get("name") or ""
    payload = {
        "id": user.id,
        "full_name": full_name.strip(),
        "outlet_name": outlet_name.strip(),
        "outlet_id": outlet_id_value,
    }
    try:
        try:
            supabase.table("profiles").upsert(payload).execute()
        except Exception:
            payload.pop("outlet_id", None)
            supabase.table("profiles").upsert(payload).execute()
        profile_data = _get_profile(user.id)
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "profile": profile_data,
                "email": user.email,
                "message": "Profil berhasil diperbarui.",
                "status": "success",
                "outlets": get_master_outlets(),
            },
        )
    except Exception as exc:
        profile_data = _get_profile(user.id)
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "profile": profile_data,
                "email": user.email,
                "message": f"Gagal memperbarui profil: {exc}",
                "status": "error",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )


@app.post("/preview")
async def preview(
    request: Request,
    no_form: str = Form(""),
    outlet_pengirim_id: str = Form(""),
    outlet_penerima_id: str = Form(""),
    tanggal: str = Form(""),
    dibuat_oleh: str = Form(""),
    disetujui_oleh: str = Form(""),
    diterima_oleh: str = Form(""),
    items_json: str = Form(""),
    file_upload: UploadFile | None = File(None),
):
    user = _get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    profile = _get_profile_for_user(user)
    locked_outlet_id = profile.get("outlet_id") if profile else None
    if locked_outlet_id not in (None, ""):
        outlet_pengirim_id = str(locked_outlet_id)
    items = _parse_items(items_json)
    dibuat_list = _parse_names(dibuat_oleh)
    diterima_list = _parse_names(diterima_oleh)
    disetujui_list = _parse_names(disetujui_oleh)

    valid, message = _validate_form(
        no_form.strip(),
        outlet_pengirim_id,
        outlet_penerima_id,
        tanggal,
        dibuat_list,
        diterima_list,
        items,
    )
    if not valid:
        return JSONResponse({"error": message}, status_code=400)

    outlets = get_master_outlets()
    outlet_map = {
        str(outlet["id"]): outlet["name"]
        for outlet in outlets
        if outlet.get("id") and outlet.get("name")
    }
    outlet_pengirim = outlet_map.get(outlet_pengirim_id, "")
    outlet_penerima = outlet_map.get(outlet_penerima_id, "")

    safe_no_form = re.sub(r"[^A-Za-z0-9_-]+", "_", no_form.strip()) or "draft"
    pdf_file_name = f"Form-Mutasi-{safe_no_form}.pdf"

    try:
        pdf_bytes = build_mutasi_pdf(
            no_form=no_form.strip(),
            tanggal=tanggal,
            outlet_pengirim=outlet_pengirim,
            outlet_penerima=outlet_penerima,
            dibuat_oleh=dibuat_list,
            disetujui_oleh=disetujui_list,
            diterima_oleh=diterima_list,
            items=items,
            file_name=file_upload.filename if file_upload else None,
            logo_path="static/img/faviconHWGBeritaAcara.png",
        )
        pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
        return JSONResponse(
            {
                "pdf_base64": pdf_base64,
                "pdf_file_name": pdf_file_name,
            }
        )
    except Exception as exc:
        return JSONResponse(
            {"error": f"Gagal membuat PDF: {exc}"},
            status_code=500,
        )


@app.post("/submit")
async def submit(
    request: Request,
    no_form: str = Form(""),
    outlet_pengirim_id: str = Form(""),
    outlet_penerima_id: str = Form(""),
    tanggal: str = Form(""),
    dibuat_oleh: str = Form(""),
    disetujui_oleh: str = Form(""),
    diterima_oleh: str = Form(""),
    items_json: str = Form(""),
    file_upload: UploadFile | None = File(None),
):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    profile = _get_profile_for_user(user)
    locked_outlet_id = profile.get("outlet_id") if profile else None
    if locked_outlet_id not in (None, ""):
        outlet_pengirim_id = str(locked_outlet_id)
    items = _parse_items(items_json)
    dibuat_list = _parse_names(dibuat_oleh)
    diterima_list = _parse_names(diterima_oleh)
    disetujui_list = _parse_names(disetujui_oleh)

    valid, message = _validate_form(
        no_form.strip(),
        outlet_pengirim_id,
        outlet_penerima_id,
        tanggal,
        dibuat_list,
        diterima_list,
        items,
    )
    if not valid:
        return _render_form(request, message=message, status="error")

    outlets = get_master_outlets()
    outlet_map = {
        str(outlet["id"]): outlet["name"]
        for outlet in outlets
        if outlet.get("id") and outlet.get("name")
    }
    outlet_pengirim = outlet_map.get(outlet_pengirim_id, "")
    outlet_penerima = outlet_map.get(outlet_penerima_id, "")

    supabase = get_supabase_client()
    if not supabase:
        return _render_form(
            request,
            message="Supabase belum dikonfigurasi. Lengkapi SUPABASE_URL dan SUPABASE_KEY.",
            status="error",
        )

    file_bytes = b""
    content_type = ""
    original_name = ""
    if file_upload:
        original_name = file_upload.filename or ""
        content_type = file_upload.content_type or ""
        file_bytes = await file_upload.read()
        if len(file_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
            return _render_form(
                request,
                message=f"Ukuran file melebihi {MAX_UPLOAD_MB}MB.",
                status="error",
            )

    try:
        bucket_name = get_setting("SUPABASE_BUCKET", "mutasi-files")
        file_url = upload_file_to_supabase(
            supabase, file_bytes, original_name, content_type, bucket_name
        )

        header_payload = {
            "no_form": no_form.strip(),
            "tanggal": tanggal,
            "outlet_pengirim": outlet_pengirim,
            "outlet_penerima": outlet_penerima,
            "dibuat_oleh": ", ".join(dibuat_list),
            "disetujui_oleh": disetujui_list,
            "diterima_oleh": ", ".join(diterima_list),
            "file_url": file_url,
        }
        header_resp = supabase.table("mutasi_header").insert(header_payload).execute()
        header_row = header_resp.data[0]

        header_payload["id"] = header_row["id"]
        lines_payload = build_line_payload(items, header_payload)
        if lines_payload:
            supabase.table("mutasi_lines").insert(lines_payload).execute()

        message = (
            "Form Mutasi No "
            f"{no_form.strip()} dari {outlet_pengirim} ke {outlet_penerima}, "
            "berhasil dicatat. Terimakasih."
        )
        return RedirectResponse(
            url=f"/success?message={quote(message)}",
            status_code=303,
        )
    except Exception as exc:
        return _render_form(request, message=f"Gagal menyimpan data: {exc}", status="error")
