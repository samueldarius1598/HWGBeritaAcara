import base64
import hashlib
import json
import threading
import time
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from cachetools import TTLCache

from .config import get_setting
from .database import get_supabase_admin_client, get_supabase_client
from .masterdata import get_outlet_by_id

AUTH_COOKIE_NAME = "sb_access_token"
REFRESH_COOKIE_NAME = "sb_refresh_token"
REMEMBER_EMAIL_COOKIE_NAME = "sb_remember_email"
REMEMBER_FLAG_COOKIE_NAME = "sb_remember_flag"
COOKIE_SAMESITE = (get_setting("COOKIE_SAMESITE") or "lax").lower()
COOKIE_SECURE = (get_setting("COOKIE_SECURE") or "false").lower() == "true"
SUPERADMIN_EMAIL = (get_setting("SUPERADMIN_EMAIL") or "").strip().lower()
SUPERADMIN_PASSWORD = get_setting("SUPERADMIN_PASSWORD") or ""
SUPERADMIN_FULL_NAME = get_setting("SUPERADMIN_FULL_NAME") or "Superadmin"
SUPERADMIN_OUTLET = get_setting("SUPERADMIN_OUTLET") or "Cost Control"

_USER_CACHE = TTLCache(maxsize=10_000, ttl=60)  # token -> (user, expires_at)
_PROFILE_CACHE = TTLCache(maxsize=10_000, ttl=60)  # user_id -> profile
_CACHE_LOCK = threading.Lock()
_REQUEST_UNSET = object()
REMEMBER_ME_MAX_AGE = 60 * 60 * 24 * 30
_PENDING_AUTH_SESSION = "pending_auth_session"
_PENDING_REMEMBER_ME = "pending_remember_me"
_PENDING_CLEAR_AUTH_COOKIES = "pending_clear_auth_cookies"


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _jwt_remaining_ttl(token: str, max_ttl: int = 60) -> int:
    # TTL = min(max_ttl, sisa waktu sebelum exp). fallback ke max_ttl kalau parsing gagal.
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return max_ttl
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
        exp = int(payload.get("exp") or 0)
        if exp <= 0:
            return max_ttl
        remaining = exp - int(time.time())
        if remaining <= 0:
            return 1
        return max(1, min(max_ttl, remaining))
    except Exception:
        return max_ttl


def _token_key(token: str) -> str:
    # jangan simpan token mentah sebagai key cache
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def set_auth_cookie(response, session, remember_me: bool = False):
    if not session or not getattr(session, "access_token", None):
        return
    if remember_me:
        max_age = REMEMBER_ME_MAX_AGE
    else:
        max_age = getattr(session, "expires_in", 3600)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        session.access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=max_age,
        path="/",
    )


def set_refresh_cookie(response, session, remember_me: bool = False):
    if not session or not getattr(session, "refresh_token", None):
        return
    max_age = REMEMBER_ME_MAX_AGE if remember_me else None
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        session.refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=max_age,
        path="/",
    )


def set_login_hint_cookie(response, email: str, remember_me: bool = False):
    if remember_me and email:
        response.set_cookie(
            REMEMBER_EMAIL_COOKIE_NAME,
            email,
            httponly=False,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            max_age=REMEMBER_ME_MAX_AGE,
            path="/",
        )
        response.set_cookie(
            REMEMBER_FLAG_COOKIE_NAME,
            "1",
            httponly=False,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            max_age=REMEMBER_ME_MAX_AGE,
            path="/",
        )
        return
    response.delete_cookie(REMEMBER_EMAIL_COOKIE_NAME, path="/")
    response.delete_cookie(REMEMBER_FLAG_COOKIE_NAME, path="/")


def _queue_auth_cookie_refresh(request: Request, session, remember_me: bool):
    setattr(request.state, _PENDING_AUTH_SESSION, session)
    setattr(request.state, _PENDING_REMEMBER_ME, remember_me)


def _queue_auth_cookie_clear(request: Request):
    setattr(request.state, _PENDING_CLEAR_AUTH_COOKIES, True)


def apply_auth_cookie_updates(request: Request, response):
    if getattr(request.state, _PENDING_CLEAR_AUTH_COOKIES, False):
        response.delete_cookie(AUTH_COOKIE_NAME, path="/")
        response.delete_cookie(REFRESH_COOKIE_NAME, path="/")
    pending_session = getattr(request.state, _PENDING_AUTH_SESSION, None)
    if pending_session:
        remember_me = bool(getattr(request.state, _PENDING_REMEMBER_ME, False))
        set_auth_cookie(response, pending_session, remember_me=remember_me)
        set_refresh_cookie(response, pending_session, remember_me=remember_me)


def clear_auth_cookie(response, request: Request | None = None):
    if request:
        token = request.cookies.get(AUTH_COOKIE_NAME)
        if token:
            key = _token_key(token)
            with _CACHE_LOCK:
                _USER_CACHE.pop(key, None)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


def _refresh_user_from_token(
    request: Request, supabase, refresh_token: str, remember_me: bool
):
    try:
        auth_response = supabase.auth.refresh_session(refresh_token)
        session = getattr(auth_response, "session", None)
        user = getattr(auth_response, "user", None)
        if not session or not getattr(session, "access_token", None):
            return None

        ttl = _jwt_remaining_ttl(session.access_token, max_ttl=60)
        expires_at = time.time() + max(ttl, 1)
        with _CACHE_LOCK:
            _USER_CACHE[_token_key(session.access_token)] = (user, expires_at)

        _queue_auth_cookie_refresh(request, session, remember_me)
        return user
    except Exception:
        _queue_auth_cookie_clear(request)
        return None


def get_current_user(request: Request, supabase=None):
    # per-request cache
    cached = getattr(request.state, "current_user", _REQUEST_UNSET)
    if cached is not _REQUEST_UNSET:
        return cached

    token = request.cookies.get(AUTH_COOKIE_NAME)
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    remember_me = request.cookies.get(REMEMBER_FLAG_COOKIE_NAME) == "1"

    if not token:
        if refresh_token:
            if supabase is None:
                supabase = get_supabase_client()
            if not supabase:
                request.state.current_user = None
                return None
            user = _refresh_user_from_token(
                request, supabase, refresh_token, remember_me
            )
            request.state.current_user = user
            return user
        request.state.current_user = None
        return None

    key = _token_key(token)
    now = time.time()
    with _CACHE_LOCK:
        cached_entry = _USER_CACHE.get(key)
    if cached_entry is not None:
        cached_user, expires_at = cached_entry
        if expires_at > now:
            request.state.current_user = cached_user
            return cached_user
        with _CACHE_LOCK:
            _USER_CACHE.pop(key, None)

    if supabase is None:
        supabase = get_supabase_client()
    if not supabase:
        request.state.current_user = None
        return None
    try:
        user_response = supabase.auth.get_user(token)
        user = user_response.user

        # cache TTL “dinamis” berdasar exp (maks 60 detik)
        ttl = _jwt_remaining_ttl(token, max_ttl=60)
        expires_at = time.time() + max(ttl, 1)
        with _CACHE_LOCK:
            _USER_CACHE[key] = (user, expires_at)

        request.state.current_user = user
        return user
    except Exception:
        with _CACHE_LOCK:
            _USER_CACHE.pop(key, None)
        if refresh_token:
            user = _refresh_user_from_token(request, supabase, refresh_token, remember_me)
            request.state.current_user = user
            return user
        _queue_auth_cookie_clear(request)
        request.state.current_user = None
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
    with _CACHE_LOCK:
        cached = _PROFILE_CACHE.get(user_id)
    if cached is not None:
        return cached
    supabase = get_supabase_client()
    if not supabase:
        return None
    try:
        resp = (
            supabase.table("profiles")
            .select("id,full_name,outlet_name,outlet_id")
            .eq("id", user_id)
            .execute()
        )
        profile = resp.data[0] if resp.data else None
        with _CACHE_LOCK:
            _PROFILE_CACHE[user_id] = profile
        return profile
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
    cached = getattr(request.state, "current_profile", _REQUEST_UNSET)
    if cached is not _REQUEST_UNSET:
        return cached
    user = get_current_user(request)
    profile = get_profile_for_user(user)
    request.state.current_profile = profile
    return profile


def redirect_to_login(request: Request):
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(
        url=f"/login?next={quote(next_url)}",
        status_code=303,
    )
