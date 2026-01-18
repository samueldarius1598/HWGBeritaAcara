import base64
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import get_setting
from routers.reports import router as reports_router
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
app.include_router(reports_router)

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


def _parse_date_value(raw_value, fallback):
    if not raw_value:
        return fallback
    try:
        return date.fromisoformat(str(raw_value))
    except ValueError:
        return fallback


def _parse_decimal(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return 0.0


def _normalize_status(value):
    text = str(value or "").strip().upper()
    return text or "SENT"


def _status_meta(status):
    status_key = _normalize_status(status)
    labels = {
        "DRAFT": "Draft",
        "SENT": "Terkirim",
        "RECEIVED": "Diterima",
        "PARTIAL": "Diterima Sebagian",
    }
    return {
        "key": status_key,
        "label": labels.get(status_key, status_key.title()),
        "class": f"status-{status_key.lower()}",
    }


def _format_idr(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    formatted = f"{amount:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"Rp. {formatted}"


def _format_qty(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(amount - int(amount)) < 1e-6:
        return str(int(amount))
    formatted = f"{amount:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _is_superadmin_user(user):
    if _is_superadmin(user):
        return True
    if not user:
        return False
    role = ""
    try:
        role = (user.app_metadata or {}).get("role", "")
    except AttributeError:
        role = ""
    if not role:
        role = (user.user_metadata or {}).get("role", "")
    return str(role or "").strip().lower() == "superadmin"


def _resolve_outlet_id(outlet_id, outlet_name):
    if outlet_id not in (None, ""):
        return str(outlet_id)
    if not outlet_name:
        return ""
    target = str(outlet_name).strip().lower()
    outlets = get_master_outlets()
    for outlet in outlets:
        if str(outlet.get("name", "")).strip().lower() == target:
            return str(outlet.get("id") or "")
    return ""


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
    if user_email is None:
        user = _get_current_user(request)
        user_email = user.email if user else None
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
    if user_email is None:
        user = _get_current_user(request)
        user_email = user.email if user else None
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
def dashboard(request: Request):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    profile = _get_profile_for_user(user)
    total_transaksi = 0
    pending_incoming = 0
    supabase = _get_supabase()
    if supabase:
        is_superadmin = _is_superadmin_user(user)
        outlet_id = profile.get("outlet_id") if profile else None
        outlet_name = (profile.get("outlet_name") if profile else "") or ""
        outlet_id_value = str(outlet_id) if outlet_id not in (None, "") else ""
        outlet_name_value = outlet_name.strip()

        def _count_rows(query):
            resp = query.execute()
            count_value = getattr(resp, "count", None)
            if count_value is not None:
                return int(count_value or 0)
            return len(resp.data or [])

        try:
            if is_superadmin and not outlet_id_value and not outlet_name_value:
                total_transaksi = _count_rows(
                    supabase.table("mutasi_header").select("id", count="exact")
                )
            else:
                resp = None
                if outlet_id_value:
                    try:
                        resp = (
                            supabase.table("mutasi_header")
                            .select("id", count="exact")
                            .or_(
                                "outlet_pengirim_id.eq."
                                f"{outlet_id_value},outlet_penerima_id.eq.{outlet_id_value}"
                            )
                            .execute()
                        )
                        total_transaksi = int(resp.count or 0)
                    except Exception:
                        resp = None
                if resp is None and outlet_name_value:
                    total_transaksi = _count_rows(
                        supabase.table("mutasi_header")
                        .select("id", count="exact")
                        .or_(
                            "outlet_pengirim.ilike."
                            f"{outlet_name_value},outlet_penerima.ilike.{outlet_name_value}"
                        )
                    )
        except Exception:
            total_transaksi = 0

        try:
            if is_superadmin and not outlet_id_value and not outlet_name_value:
                pending_incoming = _count_rows(
                    supabase.table("mutasi_header")
                    .select("id", count="exact")
                    .or_("status.is.null,status.neq.RECEIVED")
                )
            else:
                resp = None
                if outlet_id_value:
                    try:
                        resp = (
                            supabase.table("mutasi_header")
                            .select("id", count="exact")
                            .eq("outlet_penerima_id", outlet_id_value)
                            .or_("status.is.null,status.neq.RECEIVED")
                            .execute()
                        )
                        pending_incoming = int(resp.count or 0)
                    except Exception:
                        resp = None
                if resp is None and outlet_name_value:
                    pending_incoming = _count_rows(
                        supabase.table("mutasi_header")
                        .select("id", count="exact")
                        .ilike("outlet_penerima", outlet_name_value)
                        .or_("status.is.null,status.neq.RECEIVED")
                    )
        except Exception:
            pending_incoming = 0
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user.email,
            "total_transaksi": total_transaksi,
            "pending_incoming": pending_incoming,
        },
    )


@app.get("/mutasi")
def mutasi_list(
    request: Request,
    start: str | None = None,
    end: str | None = None,
):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    profile = _get_profile_for_user(user)
    is_superadmin = _is_superadmin_user(user)
    outlet_id = profile.get("outlet_id") if profile else None
    outlet_name = profile.get("outlet_name") if profile else ""
    outlet_name_value = outlet_name.strip() if outlet_name else ""
    outlet_id_value = str(outlet_id) if outlet_id not in (None, "") else ""

    today = date.today()
    start_date = _parse_date_value(start, today - timedelta(days=14))
    end_date = _parse_date_value(end, today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    message = request.query_params.get("message")
    status = request.query_params.get("status")
    rows = []

    supabase = _get_supabase()
    if not supabase:
        message = message or "Supabase belum dikonfigurasi."
        status = status or "error"
    elif not is_superadmin and not outlet_id_value and not outlet_name:
        message = message or "Outlet Anda belum ditentukan. Perbarui profil Anda."
        status = status or "error"
    else:
        try:
            def _build_header_query():
                return (
                    supabase.table("mutasi_header")
                    .select("*")
                    .gte("tanggal", start_date.isoformat())
                    .lte("tanggal", end_date.isoformat())
                )

            resp = None
            if outlet_id_value and not is_superadmin:
                try:
                    resp = (
                        _build_header_query().or_(
                            "outlet_pengirim_id.eq."
                            f"{outlet_id_value},outlet_penerima_id.eq.{outlet_id_value}"
                        )
                        .order("tanggal", desc=True)
                        .execute()
                    )
                except Exception:
                    resp = None
            if resp is None or (
                resp is not None
                and not is_superadmin
                and outlet_name_value
                and not (resp.data or [])
            ):
                query = _build_header_query()
                if outlet_name_value and not is_superadmin:
                    query = query.or_(
                        "outlet_pengirim.ilike."
                        f"{outlet_name_value},outlet_penerima.ilike.{outlet_name_value}"
                    )
                resp = query.order("tanggal", desc=True).execute()
            for row in resp.data or []:
                dibuat_oleh = row.get("dibuat_oleh") or "-"
                if isinstance(dibuat_oleh, list):
                    dibuat_oleh = ", ".join(
                        str(name) for name in dibuat_oleh if str(name).strip()
                    )
                status_meta = _status_meta(row.get("status"))
                rows.append(
                    {
                        "id": row.get("id"),
                        "no_form": row.get("no_form") or "-",
                        "tanggal": row.get("tanggal") or "-",
                        "outlet_penerima": row.get("outlet_penerima") or "-",
                        "status_label": status_meta["label"],
                        "status_class": status_meta["class"],
                        "dibuat_oleh": dibuat_oleh or "-",
                    }
                )
        except Exception as exc:
            message = message or f"Gagal memuat data mutasi: {exc}"
            status = status or "error"

    return templates.TemplateResponse(
        "mutasi_list.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user.email,
            "rows": rows,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "message": message,
            "status": status,
        },
    )


@app.get("/mutasi/form")
def mutasi_form(request: Request):
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


@app.get("/mutasi/{mutasi_id}")
def mutasi_detail(request: Request, mutasi_id: str):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    profile = _get_profile_for_user(user)
    is_superadmin = _is_superadmin_user(user)
    message = request.query_params.get("message")
    status = request.query_params.get("status")

    supabase = _get_supabase()
    if not supabase:
        return RedirectResponse(
            url="/mutasi?status=error&message=Supabase%20belum%20dikonfigurasi.",
            status_code=303,
        )

    header_resp = supabase.table("mutasi_header").select("*").eq("id", mutasi_id).execute()
    header = header_resp.data[0] if header_resp.data else None
    if not header:
        return RedirectResponse(
            url="/mutasi?status=error&message=Data%20mutasi%20tidak%20ditemukan.",
            status_code=303,
        )

    outlet_pengirim_id = _resolve_outlet_id(
        header.get("outlet_pengirim_id"), header.get("outlet_pengirim")
    )
    outlet_penerima_id = _resolve_outlet_id(
        header.get("outlet_penerima_id"), header.get("outlet_penerima")
    )
    user_outlet_id = (
        str(profile.get("outlet_id")) if profile and profile.get("outlet_id") else ""
    )
    is_receiver = user_outlet_id and user_outlet_id == outlet_penerima_id

    if not is_superadmin and user_outlet_id not in (
        outlet_pengirim_id,
        outlet_penerima_id,
    ):
        return RedirectResponse(
            url="/mutasi?status=error&message=Akses%20ditolak.",
            status_code=303,
        )

    status_meta = _status_meta(header.get("status"))
    try:
        lines_resp = (
            supabase.table("mutasi_lines")
            .select("*")
            .eq("header_id", mutasi_id)
            .eq("movement_type", "masuk")
            .order("id", desc=False)
            .execute()
        )
        lines_raw = lines_resp.data or []
    except Exception:
        lines_raw = []

    if not lines_raw:
        try:
            lines_resp = (
                supabase.table("mutasi_lines")
                .select("*")
                .eq("header_id", mutasi_id)
                .order("id", desc=False)
                .execute()
            )
            lines_raw = lines_resp.data or []
        except Exception:
            lines_raw = []

    lines = []
    total_qty_sent = 0.0
    total_qty_received = 0.0
    total_value = 0.0
    for line in lines_raw:
        qty_sent = float(line.get("qty") or 0)
        qty_received = float(line.get("qty_received") or 0)
        missing_qty = max(qty_sent - qty_received, 0)
        total_qty_sent += qty_sent
        total_qty_received += qty_received
        line_data = {
            "id": line.get("id"),
            "nama_item": line.get("nama_item") or "-",
            "kode_item": line.get("kode_item") or "-",
            "uom": line.get("uom") or "-",
            "qty_sent": qty_sent,
            "qty_received": qty_received,
            "missing_qty": missing_qty,
        }
        if is_superadmin:
            harga = float(line.get("harga_cost") or 0)
            subtotal = qty_sent * harga
            total_value += subtotal
            line_data["harga_display"] = _format_idr(harga)
            line_data["subtotal_display"] = _format_idr(subtotal)
        lines.append(line_data)

    can_receive = is_receiver and status_meta["key"] != "RECEIVED"
    receiver_name = ""
    if profile:
        receiver_name = profile.get("full_name") or user.email
    totals = {
        "qty_sent": _format_qty(total_qty_sent),
        "qty_received": _format_qty(total_qty_received),
        "qty_missing": _format_qty(max(total_qty_sent - total_qty_received, 0)),
    }
    if is_superadmin:
        totals["total_harga"] = _format_idr(total_value)

    return templates.TemplateResponse(
        "mutasi_detail.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user.email,
            "header": header,
            "status_meta": status_meta,
            "lines": lines,
            "is_superadmin": is_superadmin,
            "can_receive": can_receive,
            "receiver_name": receiver_name,
            "totals": totals,
            "message": message,
            "status": status,
        },
    )


@app.post("/mutasi/{mutasi_id}/receive")
async def mutasi_receive(request: Request, mutasi_id: str):
    user = _get_current_user(request)
    if not user:
        return _redirect_to_login(request)
    profile = _get_profile_for_user(user)
    user_outlet_id = (
        str(profile.get("outlet_id")) if profile and profile.get("outlet_id") else ""
    )

    supabase = _get_supabase()
    if not supabase:
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message=Supabase%20belum%20dikonfigurasi.",
            status_code=303,
        )

    header_resp = supabase.table("mutasi_header").select("*").eq("id", mutasi_id).execute()
    header = header_resp.data[0] if header_resp.data else None
    if not header:
        return RedirectResponse(
            url="/mutasi?status=error&message=Data%20mutasi%20tidak%20ditemukan.",
            status_code=303,
        )

    outlet_penerima_id = _resolve_outlet_id(
        header.get("outlet_penerima_id"), header.get("outlet_penerima")
    )
    if not user_outlet_id or user_outlet_id != outlet_penerima_id:
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message=Akses%20ditolak.",
            status_code=303,
        )

    current_status = _normalize_status(header.get("status"))
    if current_status == "RECEIVED":
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=warning&message=Mutasi%20ini%20sudah%20diterima.",
            status_code=303,
        )

    lines_resp = (
        supabase.table("mutasi_lines")
        .select("id,qty,qty_received,movement_type")
        .eq("header_id", mutasi_id)
        .eq("movement_type", "masuk")
        .order("id", desc=False)
        .execute()
    )
    lines = lines_resp.data or []
    if not lines:
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message=Data%20item%20mutasi%20tidak%20ditemukan.",
            status_code=303,
        )

    form_data = await request.form()
    updates = []
    total_sent = 0.0
    total_received = 0.0
    errors = []

    for line in lines:
        line_id = line.get("id")
        if not line_id:
            errors.append("ID item mutasi tidak ditemukan.")
            continue
        qty_sent = float(line.get("qty") or 0)
        raw_value = form_data.get(f"qty_received_{line_id}")
        qty_received = _parse_decimal(raw_value)
        if qty_received < 0:
            errors.append("Qty terima tidak boleh minus.")
            continue
        if qty_received > qty_sent:
            errors.append("Qty terima tidak boleh melebihi qty kirim.")
            continue
        total_sent += qty_sent
        total_received += qty_received
        updates.append({"id": line_id, "qty_received": qty_received})

    if errors:
        error_message = " ".join(sorted(set(errors)))
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message={quote(error_message)}",
            status_code=303,
        )

    try:
        if updates:
            for payload in updates:
                supabase.table("mutasi_lines").update(
                    {"qty_received": payload["qty_received"]}
                ).eq("id", payload["id"]).eq("header_id", mutasi_id).execute()
        status_key = "RECEIVED" if abs(total_received - total_sent) < 1e-6 else "PARTIAL"
        receiver_name = ""
        if profile:
            receiver_name = profile.get("full_name") or user.email
        update_payload = {
            "status": status_key,
            "received_by": receiver_name or user.email,
            "received_at": datetime.utcnow().isoformat(),
        }
        try:
            supabase.table("mutasi_header").update(update_payload).eq(
                "id", mutasi_id
            ).execute()
        except Exception:
            fallback_payload = {
                key: value
                for key, value in update_payload.items()
                if key not in ("received_by", "received_at")
            }
            supabase.table("mutasi_header").update(fallback_payload).eq(
                "id", mutasi_id
            ).execute()

        if status_key == "PARTIAL":
            message = (
                "Barang diterima sebagian. Silakan buat Mutasi Baru untuk sisa barang "
                "yang belum sampai/hilang."
            )
            status_flag = "warning"
        else:
            message = "Penerimaan berhasil diproses."
            status_flag = "success"
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status={status_flag}&message={quote(message)}",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message={quote(str(exc))}",
            status_code=303,
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
            "user_email": user.email,
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
                "user_email": user.email,
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
                "user_email": user.email,
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
                "user_email": user.email,
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
                "user_email": user.email,
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
            "status": "SENT",
            "outlet_pengirim_id": _normalize_outlet_id(outlet_pengirim_id),
            "outlet_penerima_id": _normalize_outlet_id(outlet_penerima_id),
        }
        try:
            header_resp = supabase.table("mutasi_header").insert(header_payload).execute()
        except Exception:
            fallback_payload = {
                key: value
                for key, value in header_payload.items()
                if key
                not in ("status", "outlet_pengirim_id", "outlet_penerima_id")
            }
            header_resp = supabase.table("mutasi_header").insert(fallback_payload).execute()
        header_row = header_resp.data[0]

        header_payload["id"] = header_row["id"]
        lines_payload = build_line_payload(items, header_payload)
        if lines_payload:
            supabase.table("mutasi_lines").insert(lines_payload).execute()

        message = "Data berhasil disimpan."
        return RedirectResponse(
            url=f"/mutasi?status=success&message={quote(message)}",
            status_code=303,
        )
    except Exception as exc:
        return _render_form(request, message=f"Gagal menyimpan data: {exc}", status="error")
