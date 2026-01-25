from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import requests

from ..config import get_setting


def _coerce_int(value, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class GoogleSheetCredentialsConfig:
    gas_url: str
    api_secret: str
    gid: str = ""
    sheet_name: str = ""
    timeout: int = 15

    @classmethod
    def from_settings(
        cls,
        *,
        default_gid: str = "",
        default_sheet_name: str = "",
        default_timeout: int = 15,
    ) -> "GoogleSheetCredentialsConfig":
        gas_url = (get_setting("CREDENTIALS_GAS_URL") or get_setting("GAS_URL") or "").strip()
        api_secret = (
            get_setting("CREDENTIALS_GAS_SECRET")
            or get_setting("GAS_API_SECRET")
            or ""
        ).strip()
        gid = str(get_setting("CREDENTIALS_GID") or default_gid or "")
        sheet_name = get_setting("CREDENTIALS_SHEET_NAME") or default_sheet_name or ""
        timeout = _coerce_int(get_setting("CREDENTIALS_GAS_TIMEOUT"), default_timeout)
        return cls(
            gas_url=gas_url,
            api_secret=api_secret,
            gid=gid,
            sheet_name=sheet_name,
            timeout=timeout,
        )


class GoogleSheetCredentialsStore:
    def __init__(self, config: GoogleSheetCredentialsConfig) -> None:
        self.config = config

    def _build_params(self, a1_range: str, value_type: str) -> Dict[str, str]:
        params = {"key": self.config.api_secret, "range": a1_range, "type": value_type}
        if self.config.sheet_name:
            params["sheet"] = self.config.sheet_name
        if self.config.gid:
            params["gid"] = str(self.config.gid)
        return params

    def _ensure_ready(self) -> None:
        if not self.config.gas_url:
            raise RuntimeError("CREDENTIALS_GAS_URL belum dikonfigurasi.")
        if not self.config.api_secret:
            raise RuntimeError("CREDENTIALS_GAS_SECRET belum dikonfigurasi.")

    def fetch_range(self, a1_range: str, *, value_type: str = "raw") -> List[List[str]]:
        self._ensure_ready()
        params = self._build_params(a1_range, value_type)
        resp = requests.get(self.config.gas_url, params=params, timeout=self.config.timeout)
        resp.raise_for_status()
        payload = resp.json() or {}
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error") or "Google Sheet API returned ok=false.")
        return payload.get("values") or []

    def fetch_cell(self, a1_cell: str, *, value_type: str = "raw") -> str:
        values = self.fetch_range(a1_cell, value_type=value_type)
        if not values or not values[0]:
            return ""
        return values[0][0]

    def fetch_fields(
        self, field_map: Dict[str, str], *, value_type: str = "raw"
    ) -> Dict[str, str]:
        results: Dict[str, str] = {}
        for key, cell in field_map.items():
            results[key] = self.fetch_cell(cell, value_type=value_type)
        return results

    def set_range(self, a1_range: str, values: List[List[str]]) -> None:
        self._ensure_ready()
        if not isinstance(values, list) or (values and not isinstance(values[0], list)):
            raise ValueError("values must be a 2D list.")
        params = {"key": self.config.api_secret}
        if self.config.sheet_name:
            params["sheet"] = self.config.sheet_name
        if self.config.gid:
            params["gid"] = str(self.config.gid)
        body = {"range": a1_range, "values": values}
        resp = requests.post(
            self.config.gas_url, params=params, json=body, timeout=self.config.timeout
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error") or "Google Sheet API returned ok=false.")
