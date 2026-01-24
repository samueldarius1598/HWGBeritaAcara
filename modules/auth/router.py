from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from core.database import get_supabase_client
from core.factory import templates
from core.masterdata import get_master_outlets, get_outlet_by_id, normalize_outlet_id
from core.security import (
    SUPERADMIN_FULL_NAME,
    SUPERADMIN_OUTLET,
    clear_auth_cookie,
    ensure_profile,
    get_current_user,
    get_profile,
    is_superadmin,
    redirect_to_login,
    set_auth_cookie,
)

router = APIRouter(tags=["auth"])


def _append_welcome_param(target_url: str) -> str:
    try:
        parsed = urlparse(target_url)
    except Exception:
        return target_url
    if parsed.scheme or parsed.netloc:
        return target_url
    query = dict(parse_qsl(parsed.query))
    if query.get("welcome") == "1":
        return target_url
    query["welcome"] = "1"
    return urlunparse(parsed._replace(query=urlencode(query)))


@router.get("/login")
def login(request: Request):
    if get_current_user(request):
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


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    supabase = get_supabase_client()
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
        if is_superadmin(user):
            ensure_profile(
                user,
                full_name=SUPERADMIN_FULL_NAME,
                outlet_name=SUPERADMIN_OUTLET,
            )
        target_url = _append_welcome_param(next or "/")
        response = RedirectResponse(url=target_url, status_code=303)
        set_auth_cookie(response, session)
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


@router.get("/register")
def register(request: Request):
    if get_current_user(request):
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


@router.post("/register")
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
    supabase = get_supabase_client()
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
    outlet_id_value = normalize_outlet_id(outlet_id)
    outlet = get_outlet_by_id(outlet_id_value)
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
            ensure_profile(
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
        set_auth_cookie(response, session)
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


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    clear_auth_cookie(response)
    return response


@router.get("/profile")
def profile(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile_data = get_profile(user.id)
    if not profile_data:
        profile_data = ensure_profile(
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


@router.post("/profile")
def profile_update(
    request: Request,
    full_name: str = Form(""),
    outlet_id: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    supabase = get_supabase_client()
    if not supabase:
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "profile": get_profile(user.id),
                "email": user.email,
                "user_email": user.email,
                "message": "Supabase belum dikonfigurasi.",
                "outlets": get_master_outlets(),
            },
            status_code=400,
        )
    outlet_id_value = normalize_outlet_id(outlet_id)
    outlet = get_outlet_by_id(outlet_id_value)
    if not outlet:
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "profile": get_profile(user.id),
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
        profile_data = get_profile(user.id)
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
        profile_data = get_profile(user.id)
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
