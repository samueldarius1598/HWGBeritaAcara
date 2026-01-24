from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from core.config import get_report_api_key
from core.database import get_supabase_admin_client, get_supabase_client
from core.security import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])


class MutasiReportQuery(BaseModel):
    outlet_id: str = Field(..., min_length=1)
    date_from: date = Field(..., alias="date_from")
    date_to: date = Field(..., alias="date_to")

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be less than or equal to date_to")
        return self

    model_config = {"populate_by_name": True}


def _get_api_key(request: Request):
    return (request.headers.get("X-API-KEY") or "").strip()


def _normalize_movement(value):
    text = str(value or "").strip().lower()
    if text in ("masuk", "in"):
        return "IN"
    if text in ("keluar", "out"):
        return "OUT"
    return text.upper() if text else ""


@router.get("/mutasi")
def report_mutasi(request: Request, params: MutasiReportQuery = Depends()):
    api_key = _get_api_key(request)
    expected_key = get_report_api_key()
    supabase = None
    if api_key:
        if not expected_key or api_key != expected_key:
            raise HTTPException(status_code=401, detail="Unauthorized")
        supabase = get_supabase_admin_client()
        if not supabase:
            raise HTTPException(status_code=503, detail="Supabase belum dikonfigurasi.")
    else:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Unauthorized")
        supabase = get_supabase_client()
        if not supabase:
            raise HTTPException(status_code=503, detail="Supabase belum dikonfigurasi.")

    headers_resp = (
        supabase.table("mutasi_header")
        .select(
            "id,no_form,tanggal,outlet_pengirim,outlet_penerima,status,received_at",
        )
        .gte("tanggal", params.date_from.isoformat())
        .lte("tanggal", params.date_to.isoformat())
        .or_(
            "outlet_pengirim_id.eq."
            f"{params.outlet_id},outlet_penerima_id.eq.{params.outlet_id}"
        )
        .execute()
    )
    headers = headers_resp.data or []
    if not headers:
        return []

    header_map = {row.get("id"): row for row in headers if row.get("id")}
    header_ids = list(header_map.keys())
    if not header_ids:
        return []

    lines_resp = (
        supabase.table("mutasi_lines")
        .select(
            "header_id,nama_item,kode_item,uom,qty_received,harga_cost,movement_type",
        )
        .in_("header_id", header_ids)
        .execute()
    )
    lines = lines_resp.data or []

    payload = []
    for line in lines:
        header = header_map.get(line.get("header_id"))
        if not header:
            continue
        payload.append(
            {
                "no_form": header.get("no_form") or "",
                "tanggal": header.get("tanggal"),
                "outlet_pengirim": header.get("outlet_pengirim") or "",
                "outlet_penerima": header.get("outlet_penerima") or "",
                "status": header.get("status") or "",
                "received_at": header.get("received_at"),
                "nama_item": line.get("nama_item") or "",
                "kode_item": line.get("kode_item") or "",
                "uom": line.get("uom") or "",
                "qty_received": float(line.get("qty_received") or 0),
                "harga_cost": float(line.get("harga_cost") or 0),
                "movement_type": _normalize_movement(line.get("movement_type")),
            }
        )
    return payload
