from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

from .config import get_setting
from .database import get_supabase_admin_client, get_supabase_client
from .masterdata import get_outlet_by_id

AUTH_COOKIE_NAME = "sb_access_token"
COOKIE_SAMESITE = (get_setting("COOKIE_SAMESITE") or "lax").lower()
COOKIE_SECURE = (get_setting("COOKIE_SECURE") or "false").lower() == "true"
SUPERADMIN_EMAIL = (get_setting("SUPERADMIN_EMAIL") or "").strip().lower()
SUPERADMIN_PASSWORD = get_setting("SUPERADMIN_PASSWORD") or ""
SUPERADMIN_FULL_NAME = get_setting("SUPERADMIN_FULL_NAME") or "Superadmin"
SUPERADMIN_OUTLET = get_setting("SUPERADMIN_OUTLET") or "Cost Control"


def set_auth_cookie(response, session):
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


def clear_auth_cookie(response):
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def get_current_user(request: Request, supabase=None):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    if supabase is None:
        supabase = get_supabase_client()
    if not supabase:
        return None
    try:
        user_response = supabase.auth.get_user(token)
        return user_response.user
    except Exception:
        return None


def is_superadmin(user):
    if not user or not SUPERADMIN_EMAIL:
        return False
    return (user.email or "").lower() == SUPERADMIN_EMAIL


def is_superadmin_user(user):
    if is_superadmin(user):
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


def ensure_superadmin_account():
    if not SUPERADMIN_EMAIL or not SUPERADMIN_PASSWORD:
        return
    supabase_admin = get_supabase_admin_client()
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
            ensure_profile(
                user, full_name=SUPERADMIN_FULL_NAME, outlet_name=SUPERADMIN_OUTLET
            )
    except Exception as exc:
        if "already" not in str(exc).lower():
            print(f"Superadmin bootstrap gagal: {exc}")


def get_profile(user_id):
    if not user_id:
        return None
    supabase = get_supabase_client()
    if not supabase:
        return None
    try:
        resp = supabase.table("profiles").select("*").eq("id", user_id).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def ensure_profile(user, full_name=None, outlet_name=None, outlet_id=None):
    if not user:
        return None
    profile = get_profile(user.id)
    if profile:
        return profile
    supabase = get_supabase_client()
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
    return get_profile(user.id)


def get_profile_for_user(user):
    if not user:
        return None
    metadata = user.user_metadata or {}
    profile = get_profile(user.id)
    if profile:
        outlet_id = profile.get("outlet_id") or metadata.get("outlet_id")
        if outlet_id and not profile.get("outlet_id"):
            profile = {**profile, "outlet_id": outlet_id}
        if outlet_id and not profile.get("outlet_name"):
            outlet = get_outlet_by_id(outlet_id)
            if outlet and outlet.get("name"):
                profile = {**profile, "outlet_name": outlet.get("name")}
        return profile
    outlet_id = metadata.get("outlet_id")
    outlet_name = metadata.get("outlet_name", "")
    if outlet_id and not outlet_name:
        outlet = get_outlet_by_id(outlet_id)
        outlet_name = outlet.get("name") if outlet else ""
    return {
        "id": user.id,
        "full_name": metadata.get("full_name", ""),
        "outlet_name": outlet_name,
        "outlet_id": outlet_id,
        "email": user.email,
    }


def get_profile_for_request(request: Request):
    user = get_current_user(request)
    return get_profile_for_user(user)


def redirect_to_login(request: Request):
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(
        url=f"/login?next={quote(next_url)}",
        status_code=303,
    )
