from fastapi import APIRouter, Request

from core.database import get_supabase_client
from core.factory import templates
from core.security import (
    get_current_user,
    get_profile_for_user,
    is_superadmin_user,
    redirect_to_login,
)

router = APIRouter(tags=["dashboard"])


@router.get("/")
def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile = get_profile_for_user(user)
    welcome = request.query_params.get("welcome")
    display_name = (profile or {}).get("full_name") or user.email
    total_transaksi = 0
    pending_incoming = 0
    supabase = get_supabase_client()
    if supabase:
        is_superadmin = is_superadmin_user(user)
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
            "welcome": welcome,
            "welcome_name": display_name,
        },
    )
