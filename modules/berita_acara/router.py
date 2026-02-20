import base64
import re
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from core.concurrency import run_blocking
from core.database import get_supabase_client
from core.factory import templates
from core.masterdata import get_master_products_result
from core.security import get_current_user, get_profile_for_user, redirect_to_login
from .repository import BeritaAcaraRepository
from .services import (
    build_berita_acara_pdf,
    build_lines_payload,
    generate_no_form,
    get_master_purposes,
    parse_items,
    parse_names,
    validate_form,
)

router = APIRouter(tags=["berita_acara"])


def _is_json_request(request: Request) -> bool:
    content_type = (request.headers.get("content-type") or "").lower()
    return "application/json" in content_type


def _wants_json_response(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept


def _normalize_names(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return parse_names(value)


def _render_form(request, *, profile, user_email, message=None, status=None, no_form=""):
    return templates.TemplateResponse(
        "berita_acara/form.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user_email,
            "message": message,
            "status": status,
            "no_form": no_form,
            "today": date.today().isoformat(),
        },
    )


def _render_success_redirect(message: str):
    return RedirectResponse(
        url=f"/berita-acara?status=success&message={quote(message)}",
        status_code=303,
    )


@router.get("/berita-acara")
def berita_acara_list(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile = get_profile_for_user(user)
    message = request.query_params.get("message")
    status = request.query_params.get("status")

    supabase = get_supabase_client()
    rows = []
    if not supabase:
        message = message or "Supabase belum dikonfigurasi."
        status = status or "error"
    else:
        try:
            repo = BeritaAcaraRepository(supabase)
            raw_rows = repo.get_list()
            rows = [
                {
                    "id": row.get("id"),
                    "no_form": row.get("no_form") or "-",
                    "purpose_name": row.get("purpose_name") or "-",
                    "outlet_name": row.get("outlet_name") or "-",
                    "status": row.get("status") or "-",
                    "created_at": row.get("created_at") or "-",
                }
                for row in raw_rows
            ]
        except Exception as exc:
            message = message or f"Gagal memuat data Berita Acara: {exc}"
            status = status or "error"

    return templates.TemplateResponse(
        "berita_acara/list.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user.email,
            "rows": rows,
            "message": message,
            "status": status,
        },
    )


@router.get("/berita-acara/form")
def berita_acara_form(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile = get_profile_for_user(user)
    supabase = get_supabase_client()
    no_form = ""
    if supabase:
        try:
            repo = BeritaAcaraRepository(supabase)
            no_form = generate_no_form(repo)
        except Exception:
            no_form = ""
    if not no_form:
        no_form = f"BA/{date.today().strftime('%Y%m%d')}/001"
    return _render_form(
        request,
        profile=profile,
        user_email=user.email,
        no_form=no_form,
    )


@router.get("/berita-acara/{header_id}")
def berita_acara_detail(request: Request, header_id: str):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile = get_profile_for_user(user)

    supabase = get_supabase_client()
    if not supabase:
        return RedirectResponse(
            url="/berita-acara?status=error&message=Supabase%20belum%20dikonfigurasi.",
            status_code=303,
        )

    repo = BeritaAcaraRepository(supabase)
    header, lines = repo.get_detail(header_id)
    if not header:
        return RedirectResponse(
            url="/berita-acara?status=error&message=Data%20tidak%20ditemukan.",
            status_code=303,
        )

    return templates.TemplateResponse(
        "berita_acara/detail.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user.email,
            "header": header,
            "lines": lines,
        },
    )


@router.get("/api/berita-acara/products")
def api_products(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    profile = get_profile_for_user(user)
    outlet_id = profile.get("outlet_id") if profile else None
    if not outlet_id:
        return JSONResponse([])
    try:
        company_id = int(outlet_id)
    except ValueError:
        return JSONResponse([])
    try:
        result = get_master_products_result(company_id)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    response = JSONResponse(result.get("data") or [])
    response.headers["X-Products-Cache"] = str(result.get("cache_state") or "miss")
    response.headers["X-Products-Completeness"] = str(
        result.get("completeness") or "esb_only"
    )
    return response


@router.get("/api/berita-acara/purposes")
def api_purposes(request: Request):
    if not get_current_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        purposes = get_master_purposes()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(purposes)


@router.post("/berita-acara/preview")
async def preview(
    request: Request,
    no_form: str = Form(""),
    purpose_id: str = Form(""),
    purpose_name: str = Form(""),
    outlet_id: str = Form(""),
    outlet_name: str = Form(""),
    dibuat_oleh: str = Form(""),
    disetujui_oleh: str = Form(""),
    mengetahui_oleh: str = Form(""),
    items_json: str = Form(""),
    supabase=Depends(get_supabase_client),
):
    is_json = _is_json_request(request)
    user = await run_in_threadpool(get_current_user, request, supabase)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    profile = await run_in_threadpool(get_profile_for_user, user)
    if profile:
        outlet_id = str(profile.get("outlet_id") or "")
        outlet_name = profile.get("outlet_name") or ""

    if is_json:
        payload = await request.json()
        header = payload.get("header") or {}
        no_form = str(header.get("no_form") or "")
        purpose_id = str(header.get("purpose_id") or "")
        purpose_name = str(header.get("purpose_name") or "")
        outlet_id = str(header.get("outlet_id") or outlet_id or "")
        outlet_name = str(header.get("outlet_name") or outlet_name or "")
        dibuat_list = _normalize_names(header.get("dibuat_oleh") or [])
        disetujui_list = _normalize_names(header.get("disetujui_oleh") or [])
        mengetahui_list = _normalize_names(header.get("mengetahui_oleh") or [])
        items = parse_items(payload.get("items") or [])
    else:
        dibuat_list = parse_names(dibuat_oleh)
        disetujui_list = parse_names(disetujui_oleh)
        mengetahui_list = parse_names(mengetahui_oleh)
        items = parse_items(items_json)

    valid, message = validate_form(
        no_form.strip(),
        purpose_id.strip(),
        purpose_name.strip(),
        outlet_id.strip(),
        dibuat_list,
        disetujui_list,
        mengetahui_list,
        items,
    )
    if not valid:
        return JSONResponse({"error": message}, status_code=400)

    safe_no_form = re.sub(r"[^A-Za-z0-9_-]+", "_", no_form.strip()) or "draft"
    pdf_file_name = f"BeritaAcara-{safe_no_form}.pdf"
    try:
        pdf_bytes = await run_in_threadpool(
            build_berita_acara_pdf,
            no_form=no_form.strip(),
            purpose_name=purpose_name.strip(),
            outlet_name=outlet_name.strip(),
            dibuat_oleh=dibuat_list,
            disetujui_oleh=disetujui_list,
            mengetahui_oleh=mengetahui_list,
            items=items,
            logo_path="static/img/faviconHWGBeritaAcara.png",
        )
        pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
        return JSONResponse({"pdf_base64": pdf_base64, "pdf_file_name": pdf_file_name})
    except Exception as exc:
        return JSONResponse({"error": f"Gagal membuat PDF: {exc}"}, status_code=500)


@router.post("/berita-acara/submit")
async def submit(
    request: Request,
    no_form: str = Form(""),
    purpose_id: str = Form(""),
    purpose_name: str = Form(""),
    outlet_id: str = Form(""),
    outlet_name: str = Form(""),
    dibuat_oleh: str = Form(""),
    disetujui_oleh: str = Form(""),
    mengetahui_oleh: str = Form(""),
    items_json: str = Form(""),
    supabase=Depends(get_supabase_client),
):
    is_json = _is_json_request(request)
    user = await run_in_threadpool(get_current_user, request, supabase)
    if not user:
        if is_json or _wants_json_response(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return redirect_to_login(request)
    profile = await run_in_threadpool(get_profile_for_user, user)
    if profile:
        outlet_id = str(profile.get("outlet_id") or "")
        outlet_name = profile.get("outlet_name") or ""

    if is_json:
        payload = await request.json()
        header = payload.get("header") or {}
        no_form = str(header.get("no_form") or "")
        purpose_id = str(header.get("purpose_id") or "")
        purpose_name = str(header.get("purpose_name") or "")
        outlet_id = str(header.get("outlet_id") or outlet_id or "")
        outlet_name = str(header.get("outlet_name") or outlet_name or "")
        dibuat_list = _normalize_names(header.get("dibuat_oleh") or [])
        disetujui_list = _normalize_names(header.get("disetujui_oleh") or [])
        mengetahui_list = _normalize_names(header.get("mengetahui_oleh") or [])
        items = parse_items(payload.get("items") or [])
    else:
        items = parse_items(items_json)
        dibuat_list = parse_names(dibuat_oleh)
        disetujui_list = parse_names(disetujui_oleh)
        mengetahui_list = parse_names(mengetahui_oleh)

    if not supabase:
        if is_json or _wants_json_response(request):
            return JSONResponse(
                {"error": "Supabase belum dikonfigurasi."}, status_code=503
            )
        return await run_in_threadpool(
            _render_form,
            request,
            profile=profile,
            user_email=user.email,
            message="Supabase belum dikonfigurasi. Lengkapi SUPABASE_URL dan SUPABASE_KEY.",
            status="error",
            no_form=no_form,
        )

    repo = BeritaAcaraRepository(supabase)
    if not no_form.strip():
        try:
            no_form = await run_blocking(generate_no_form, repo)
        except Exception:
            no_form = f"BA/{date.today().strftime('%Y%m%d')}/001"

    valid, message = validate_form(
        no_form.strip(),
        purpose_id.strip(),
        purpose_name.strip(),
        outlet_id.strip(),
        dibuat_list,
        disetujui_list,
        mengetahui_list,
        items,
    )
    if not valid:
        if is_json or _wants_json_response(request):
            return JSONResponse({"error": message}, status_code=400)
        return await run_in_threadpool(
            _render_form,
            request,
            profile=profile,
            user_email=user.email,
            message=message,
            status="error",
            no_form=no_form,
        )

    try:
        def _process():
            header_payload = {
                "no_form": no_form.strip(),
                "purpose_id": purpose_id.strip(),
                "purpose_name": purpose_name.strip(),
                "outlet_id": outlet_id.strip(),
                "outlet_name": outlet_name.strip(),
                "dibuat_oleh": dibuat_list,
                "disetujui_oleh": disetujui_list,
                "mengetahui_oleh": mengetahui_list,
                "status": "SUBMITTED",
            }
            header_row = repo.create_header(header_payload)
            if not header_row:
                raise RuntimeError("Gagal menyimpan header Berita Acara.")
            lines_payload = build_lines_payload(items, header_row["id"])
            if lines_payload:
                repo.create_lines(lines_payload)

        await run_in_threadpool(_process)
    except Exception as exc:
        if is_json or _wants_json_response(request):
            return JSONResponse({"error": str(exc)}, status_code=500)
        return await run_in_threadpool(
            _render_form,
            request,
            profile=profile,
            user_email=user.email,
            message=f"Gagal menyimpan data: {exc}",
            status="error",
            no_form=no_form,
        )

    if is_json or _wants_json_response(request):
        return JSONResponse(
            {
                "ok": True,
                "redirect_url": "/berita-acara?status=success&message=Data%20berhasil%20disimpan.",
            }
        )
    return _render_success_redirect("Data berhasil disimpan.")
