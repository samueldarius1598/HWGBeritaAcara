from __future__ import annotations

from typing import Iterable

from supabase import Client


class MutasiRepository:
    def __init__(self, db: Client):
        self.db = db

    def list_headers(self, start_date, end_date):
        return (
            self.db.table("mutasi_header")
            .select("*")
            .gte("tanggal", start_date.isoformat())
            .lte("tanggal", end_date.isoformat())
        )

    def get_header(self, mutasi_id: str):
        resp = self.db.table("mutasi_header").select("*").eq("id", mutasi_id).execute()
        return resp.data[0] if resp.data else None

    def get_lines(self, mutasi_id: str, movement_type: str | None = None):
        query = (
            self.db.table("mutasi_lines")
            .select("*")
            .eq("header_id", mutasi_id)
            .order("id", desc=False)
        )
        if movement_type:
            query = query.eq("movement_type", movement_type)
        return query.execute().data or []

    def insert_header(self, payload: dict):
        try:
            resp = self.db.table("mutasi_header").insert(payload).execute()
        except Exception:
            fallback_payload = {
                key: value
                for key, value in payload.items()
                if key not in ("status", "outlet_pengirim_id", "outlet_penerima_id")
            }
            resp = self.db.table("mutasi_header").insert(fallback_payload).execute()
        return resp.data[0] if resp.data else None

    def insert_lines(self, lines_payload: Iterable[dict]):
        if not lines_payload:
            return None
        return self.db.table("mutasi_lines").insert(list(lines_payload)).execute()

    def update_receive(self, mutasi_id: str, updates, update_payload, fallback_payload):
        if updates:
            for payload in updates:
                self.db.table("mutasi_lines").update(
                    {"qty_received": payload["qty_received"]}
                ).eq("id", payload["id"]).eq("header_id", mutasi_id).execute()
        try:
            self.db.table("mutasi_header").update(update_payload).eq(
                "id", mutasi_id
            ).execute()
        except Exception:
            self.db.table("mutasi_header").update(fallback_payload).eq(
                "id", mutasi_id
            ).execute()
