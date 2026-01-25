import base64
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from core.config import get_setting
from core.database import get_supabase_client
from core.factory import templates
from core.masterdata import (
    get_master_outlets,
    get_master_products,
    normalize_outlet_id,
    resolve_outlet_id,
)
from core.security import (
    get_current_user,
    get_profile_for_request,
    get_profile_for_user,
    is_superadmin_user,
    redirect_to_login,
)
from .repository import MutasiRepository
from .services import (
    build_line_payload,
    build_mutasi_pdf,
    format_idr,
    format_qty,
    parse_date_value,
    parse_decimal,
    parse_items,
    parse_names,
    status_meta,
    upload_file_to_supabase,
    validate_form,
)

router = APIRouter(tags=["mutasi"])

MAX_UPLOAD_MB = 200


def _render_form(request, message=None, status=None, profile=None, user_email=None):
    outlets = get_master_outlets()
    if user_email is None:
        user = get_current_user(request)
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
            "profile": profile or get_profile_for_request(request),
            "user_email": user_email,
        },
    )


def _render_success(request, message, profile=None, user_email=None):
    if user_email is None:
        user = get_current_user(request)
        user_email = user.email if user else None
    return templates.TemplateResponse(
        "success.html",
        {
            "request": request,
            "message": message,
            "profile": profile or get_profile_for_request(request),
            "user_email": user_email,
        },
    )


@router.get("/mutasi")
def mutasi_list(
    request: Request,
    start: str | None = None,
    end: str | None = None,
):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile = get_profile_for_user(user)
    is_superadmin = is_superadmin_user(user)
    outlet_id = profile.get("outlet_id") if profile else None
    outlet_name = profile.get("outlet_name") if profile else ""
    outlet_name_value = outlet_name.strip() if outlet_name else ""
    outlet_id_value = str(outlet_id) if outlet_id not in (None, "") else ""

    today = date.today()
    start_date = parse_date_value(start, today - timedelta(days=14))
    end_date = parse_date_value(end, today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    message = request.query_params.get("message")
    status = request.query_params.get("status")
    receive_pending_rows = []
    receive_rows = []
    send_rows = []

    supabase = get_supabase_client()
    if not supabase:
        message = message or "Supabase belum dikonfigurasi."
        status = status or "error"
    elif not is_superadmin and not outlet_id_value and not outlet_name:
        message = message or "Outlet Anda belum ditentukan. Perbarui profil Anda."
        status = status or "error"
    else:
        repo = MutasiRepository(supabase)
        try:
            pending_statuses = {"SENT"}

            def _base_query(use_date_filter: bool):
                if use_date_filter:
                    return repo.list_headers(start_date, end_date)
                return supabase.table("mutasi_header").select("*")

            def _fetch_headers(field_id: str, field_name: str, use_date_filter: bool):
                resp = None
                if outlet_id_value and not is_superadmin:
                    try:
                        resp = (
                            _base_query(use_date_filter)
                            .eq(field_id, outlet_id_value)
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
                    query = _base_query(use_date_filter)
                    if outlet_name_value and not is_superadmin:
                        query = query.ilike(field_name, outlet_name_value)
                    resp = query.order("tanggal", desc=True).execute()
                return resp.data or []

            def _format_rows(raw_rows, allow_statuses=None, exclude_ids=None):
                rows = []
                exclude_ids = exclude_ids or set()
                for row in raw_rows or []:
                    row_id = row.get("id")
                    if row_id in exclude_ids:
                        continue
                    meta = status_meta(row.get("status"))
                    if allow_statuses and meta["key"] not in allow_statuses:
                        continue
                    dibuat_oleh = row.get("dibuat_oleh") or "-"
                    if isinstance(dibuat_oleh, list):
                        dibuat_oleh = ", ".join(
                            str(name) for name in dibuat_oleh if str(name).strip()
                        )
                    rows.append(
                        {
                            "id": row_id,
                            "no_form": row.get("no_form") or "-",
                            "tanggal": row.get("tanggal") or "-",
                            "outlet_pengirim": row.get("outlet_pengirim") or "-",
                            "outlet_penerima": row.get("outlet_penerima") or "-",
                            "status_key": meta["key"],
                            "status_label": meta["label"],
                            "status_class": meta["class"],
                            "dibuat_oleh": dibuat_oleh or "-",
                        }
                    )
                return rows

            raw_receive_pending = _fetch_headers(
                "outlet_penerima_id", "outlet_penerima", use_date_filter=False
            )
            receive_pending_rows = _format_rows(
                raw_receive_pending, allow_statuses=pending_statuses
            )
            pending_ids = {
                row["id"] for row in receive_pending_rows if row.get("id")
            }

            raw_receive_filtered = _fetch_headers(
                "outlet_penerima_id", "outlet_penerima", use_date_filter=True
            )
            receive_rows = _format_rows(raw_receive_filtered, exclude_ids=pending_ids)

            raw_send_filtered = _fetch_headers(
                "outlet_pengirim_id", "outlet_pengirim", use_date_filter=True
            )
            send_rows = _format_rows(raw_send_filtered)
        except Exception as exc:
            message = message or f"Gagal memuat data mutasi: {exc}"
            status = status or "error"

    return templates.TemplateResponse(
        "mutasi_list.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user.email,
            "receive_pending_rows": receive_pending_rows,
            "receive_rows": receive_rows,
            "send_rows": send_rows,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "message": message,
            "status": status,
        },
    )


@router.get("/mutasi/form")
def mutasi_form(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile = get_profile_for_user(user)
    status = request.query_params.get("status")
    message = request.query_params.get("message")
    return _render_form(
        request,
        message=message,
        status=status,
        profile=profile,
        user_email=user.email,
    )


@router.get("/mutasi/{mutasi_id}")
def mutasi_detail(request: Request, mutasi_id: str):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    profile = get_profile_for_user(user)
    is_superadmin = is_superadmin_user(user)
    message = request.query_params.get("message")
    status = request.query_params.get("status")

    supabase = get_supabase_client()
    if not supabase:
        return RedirectResponse(
            url="/mutasi?status=error&message=Supabase%20belum%20dikonfigurasi.",
            status_code=303,
        )

    repo = MutasiRepository(supabase)
    header = repo.get_header(mutasi_id)
    if not header:
        return RedirectResponse(
            url="/mutasi?status=error&message=Data%20mutasi%20tidak%20ditemukan.",
            status_code=303,
        )

    outlet_pengirim_id = resolve_outlet_id(
        header.get("outlet_pengirim_id"), header.get("outlet_pengirim")
    )
    outlet_penerima_id = resolve_outlet_id(
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

    meta = status_meta(header.get("status"))
    try:
        lines_raw = repo.get_lines(mutasi_id, movement_type="masuk")
    except Exception:
        lines_raw = []

    if not lines_raw:
        try:
            lines_raw = repo.get_lines(mutasi_id)
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
            line_data["harga_display"] = format_idr(harga)
            line_data["subtotal_display"] = format_idr(subtotal)
        lines.append(line_data)

    can_receive = is_receiver and meta["key"] != "RECEIVED"
    receiver_name = ""
    if profile:
        receiver_name = profile.get("full_name") or user.email
    totals = {
        "qty_sent": format_qty(total_qty_sent),
        "qty_received": format_qty(total_qty_received),
        "qty_missing": format_qty(max(total_qty_sent - total_qty_received, 0)),
    }
    if is_superadmin:
        totals["total_harga"] = format_idr(total_value)

    return templates.TemplateResponse(
        "mutasi_detail.html",
        {
            "request": request,
            "profile": profile,
            "user_email": user.email,
            "header": header,
            "status_meta": meta,
            "lines": lines,
            "is_superadmin": is_superadmin,
            "can_receive": can_receive,
            "receiver_name": receiver_name,
            "totals": totals,
            "message": message,
            "status": status,
        },
    )


@router.post("/mutasi/{mutasi_id}/receive")
async def mutasi_receive(
    request: Request,
    mutasi_id: str,
    supabase=Depends(get_supabase_client),
):
    user = await run_in_threadpool(get_current_user, request, supabase)
    if not user:
        return redirect_to_login(request)
    profile = await run_in_threadpool(get_profile_for_user, user)
    user_outlet_id = (
        str(profile.get("outlet_id")) if profile and profile.get("outlet_id") else ""
    )

    if not supabase:
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message=Supabase%20belum%20dikonfigurasi.",
            status_code=303,
        )

    repo = MutasiRepository(supabase)

    def _fetch_header_and_lines():
        header = repo.get_header(mutasi_id)
        if not header:
            return None, []
        lines = (
            supabase.table("mutasi_lines")
            .select("id,qty,qty_received,movement_type")
            .eq("header_id", mutasi_id)
            .eq("movement_type", "masuk")
            .order("id", desc=False)
            .execute()
        )
        return header, lines.data or []

    header, lines = await run_in_threadpool(_fetch_header_and_lines)
    if not header:
        return RedirectResponse(
            url="/mutasi?status=error&message=Data%20mutasi%20tidak%20ditemukan.",
            status_code=303,
        )

    outlet_penerima_id = await run_in_threadpool(
        resolve_outlet_id,
        header.get("outlet_penerima_id"),
        header.get("outlet_penerima"),
    )
    if not user_outlet_id or user_outlet_id != outlet_penerima_id:
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message=Akses%20ditolak.",
            status_code=303,
        )

    current_status = status_meta(header.get("status"))["key"]
    if current_status == "RECEIVED":
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=warning&message=Mutasi%20ini%20sudah%20diterima.",
            status_code=303,
        )

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
        qty_received = parse_decimal(raw_value)
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

    status_key = "RECEIVED" if abs(total_received - total_sent) < 1e-6 else "PARTIAL"
    receiver_name = ""
    if profile:
        receiver_name = profile.get("full_name") or user.email
    update_payload = {
        "status": status_key,
        "received_by": receiver_name or user.email,
        "received_at": datetime.utcnow().isoformat(),
    }
    fallback_payload = {
        key: value
        for key, value in update_payload.items()
        if key not in ("received_by", "received_at")
    }

    def _apply_updates():
        repo.update_receive(mutasi_id, updates, update_payload, fallback_payload)

    try:
        await run_in_threadpool(_apply_updates)
    except Exception as exc:
        return RedirectResponse(
            url=f"/mutasi/{mutasi_id}?status=error&message={quote(str(exc))}",
            status_code=303,
        )

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


@router.get("/api/products")
def api_products(request: Request, outlet_id: str | None = None):
    if not get_current_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not outlet_id:
        return JSONResponse([])
    try:
        company_id = int(outlet_id)
    except ValueError:
        return JSONResponse([])
    products = get_master_products(company_id)
    return JSONResponse(products)


@router.get("/success")
def success(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_to_login(request)
    message = request.query_params.get("message") or "Data berhasil disimpan."
    profile = get_profile_for_user(user)
    return _render_success(request, message, profile=profile, user_email=user.email)


@router.post("/preview")
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
    supabase=Depends(get_supabase_client),
):
    user = await run_in_threadpool(get_current_user, request, supabase)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    profile = await run_in_threadpool(get_profile_for_user, user)
    locked_outlet_id = profile.get("outlet_id") if profile else None
    if locked_outlet_id not in (None, ""):
        outlet_pengirim_id = str(locked_outlet_id)
    items = parse_items(items_json)
    dibuat_list = parse_names(dibuat_oleh)
    diterima_list = parse_names(diterima_oleh)
    disetujui_list = parse_names(disetujui_oleh)

    valid, message = await run_in_threadpool(
        validate_form,
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

    outlets = await run_in_threadpool(get_master_outlets)
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
        pdf_bytes = await run_in_threadpool(
            build_mutasi_pdf,
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


@router.post("/submit")
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
    supabase=Depends(get_supabase_client),
):
    user = await run_in_threadpool(get_current_user, request, supabase)
    if not user:
        return redirect_to_login(request)
    profile = await run_in_threadpool(get_profile_for_user, user)
    locked_outlet_id = profile.get("outlet_id") if profile else None
    if locked_outlet_id not in (None, ""):
        outlet_pengirim_id = str(locked_outlet_id)
    items = parse_items(items_json)
    dibuat_list = parse_names(dibuat_oleh)
    diterima_list = parse_names(diterima_oleh)
    disetujui_list = parse_names(disetujui_oleh)

    valid, message = await run_in_threadpool(
        validate_form,
        no_form.strip(),
        outlet_pengirim_id,
        outlet_penerima_id,
        tanggal,
        dibuat_list,
        diterima_list,
        items,
    )
    if not valid:
        return await run_in_threadpool(
            _render_form,
            request,
            message=message,
            status="error",
            profile=profile,
            user_email=user.email,
        )

    outlets = await run_in_threadpool(get_master_outlets)
    outlet_map = {
        str(outlet["id"]): outlet["name"]
        for outlet in outlets
        if outlet.get("id") and outlet.get("name")
    }
    outlet_pengirim = outlet_map.get(outlet_pengirim_id, "")
    outlet_penerima = outlet_map.get(outlet_penerima_id, "")

    if not supabase:
        return await run_in_threadpool(
            _render_form,
            request,
            message="Supabase belum dikonfigurasi. Lengkapi SUPABASE_URL dan SUPABASE_KEY.",
            status="error",
            profile=profile,
            user_email=user.email,
        )

    file_bytes = b""
    content_type = ""
    original_name = ""
    if file_upload:
        original_name = file_upload.filename or ""
        content_type = file_upload.content_type or ""
        file_bytes = await run_in_threadpool(file_upload.file.read)
        if len(file_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
            return await run_in_threadpool(
                _render_form,
                request,
                message=f"Ukuran file melebihi {MAX_UPLOAD_MB}MB.",
                status="error",
                profile=profile,
                user_email=user.email,
            )

    try:
        bucket_name = get_setting("SUPABASE_BUCKET", "mutasi-files")

        def _process_submission():
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
                "outlet_pengirim_id": normalize_outlet_id(outlet_pengirim_id),
                "outlet_penerima_id": normalize_outlet_id(outlet_penerima_id),
            }
            repo = MutasiRepository(supabase)
            header_row = repo.insert_header(header_payload)
            if not header_row:
                raise RuntimeError("Gagal menyimpan header mutasi.")

            header_payload["id"] = header_row["id"]
            lines_payload = build_line_payload(items, header_payload)
            if lines_payload:
                repo.insert_lines(lines_payload)

        await run_in_threadpool(_process_submission)

        message = "Data berhasil disimpan."
        return RedirectResponse(
            url=f"/mutasi?status=success&message={quote(message)}",
            status_code=303,
        )
    except Exception as exc:
        return await run_in_threadpool(
            _render_form,
            request,
            message=f"Gagal menyimpan data: {exc}",
            status="error",
            profile=profile,
            user_email=user.email,
        )
