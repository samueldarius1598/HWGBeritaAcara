from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from supabase import Client


class BeritaAcaraRepository:
    def __init__(self, db: Client):
        self.db = db

    def create_header(self, payload: dict) -> Optional[dict]:
        resp = self.db.table("berita_acara_header").insert(payload).execute()
        return resp.data[0] if resp.data else None

    def create_lines(self, payloads: Iterable[dict]) -> Optional[List[dict]]:
        items = list(payloads)
        if not items:
            return None
        resp = self.db.table("berita_acara_lines").insert(items).execute()
        return resp.data or []

    def get_list(self, *, limit: int = 200) -> List[dict]:
        resp = (
            self.db.table("berita_acara_header")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    def get_detail(self, header_id: str) -> Tuple[Optional[dict], List[dict]]:
        header_resp = (
            self.db.table("berita_acara_header")
            .select("*")
            .eq("id", header_id)
            .execute()
        )
        header = header_resp.data[0] if header_resp.data else None
        if not header:
            return None, []
        lines_resp = (
            self.db.table("berita_acara_lines")
            .select("*")
            .eq("header_id", header_id)
            .order("row_no", desc=False)
            .execute()
        )
        return header, lines_resp.data or []

    def count_by_no_form_prefix(self, prefix: str) -> int:
        resp = (
            self.db.table("berita_acara_header")
            .select("id", count="exact")
            .like("no_form", f"{prefix}%")
            .execute()
        )
        if getattr(resp, "count", None) is not None:
            return int(resp.count or 0)
        return len(resp.data or [])
