import time
from datetime import datetime
from typing import Optional

import requests


class ESBConfigGAS:
    def __init__(
        self,
        gas_url: str,
        api_secret: str,
        *,
        sheet_name: Optional[str] = None,
        gid: Optional[str] = None,
        load_range: str = "E2:E11",
        token_range: str = "E9:E11",
        session_range: str = "E7:E11",
        timeout: int = 15,
    ):
        self.gas_url = gas_url.rstrip("/")
        self.api_secret = api_secret
        self.sheet_name = sheet_name or ""
        self.gid = str(gid) if gid not in (None, "") else ""
        self.load_range = load_range
        self.token_range = token_range
        self.session_range = session_range
        self.timeout = timeout

        self.username: str = ""
        self.password: str = ""
        self.company_code: str = ""
        self.company_name: str = ""
        self.access_token: str = ""
        self.refresh_token: str = ""
        self.token_timestamp_epoch: float = 0.0

    def load_config(self) -> bool:
        params = {
            "key": self.api_secret,
            "range": self._prefixed_range(self.load_range),
            "type": "raw",
        }
        if self.sheet_name:
            params["sheet"] = self.sheet_name
        if self.gid:
            params["gid"] = self.gid
        try:
            resp = requests.get(self.gas_url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json() or {}
            if not payload.get("ok"):
                return False
            values = payload.get("values") or []
        except Exception:
            return False

        flat_vals = [row[0] if row else "" for row in values]
        while len(flat_vals) < 10:
            flat_vals.append("")

        self.username = (str(flat_vals[0]) + str(flat_vals[1])).strip()
        self.password = str(flat_vals[2]).strip()
        self.company_code = str(flat_vals[5]).strip()
        self.company_name = str(flat_vals[6]).strip()
        self.access_token = str(flat_vals[7]).strip()
        self.refresh_token = str(flat_vals[8]).strip()
        ts_str = str(flat_vals[9]).strip()
        self.token_timestamp_epoch = self._parse_timestamp(ts_str)
        return True

    def update_tokens(self, access_token: str, refresh_token: str) -> bool:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = {
            "range": self._prefixed_range(self.token_range),
            "values": [
                [access_token],
                [refresh_token],
                [now_str],
            ],
        }
        params = {"key": self.api_secret}
        if self.sheet_name:
            params["sheet"] = self.sheet_name
        if self.gid:
            params["gid"] = self.gid
        try:
            resp = requests.post(
                self.gas_url, params=params, json=body, timeout=self.timeout
            )
            resp.raise_for_status()
            payload = resp.json() or {}
        except Exception:
            return False
        if not payload.get("ok"):
            return False
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_timestamp_epoch = time.time()
        return True

    def update_session(
        self,
        company_code: str,
        company_name: str,
        access_token: str,
        refresh_token: str,
        timestamp_str: str,
    ) -> bool:
        now_str = timestamp_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = {
            "range": self._prefixed_range(self.session_range),
            "values": [
                [company_code],
                [company_name],
                [access_token],
                [refresh_token],
                [now_str],
            ],
        }
        params = {"key": self.api_secret}
        if self.sheet_name:
            params["sheet"] = self.sheet_name
        if self.gid:
            params["gid"] = self.gid
        try:
            resp = requests.post(
                self.gas_url, params=params, json=body, timeout=self.timeout
            )
            resp.raise_for_status()
            payload = resp.json() or {}
        except Exception:
            return False
        if not payload.get("ok"):
            return False
        self.company_code = company_code
        self.company_name = company_name
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_timestamp_epoch = time.time()
        return True

    def _prefixed_range(self, a1_range: str) -> str:
        if self.sheet_name:
            return f"{self.sheet_name}!{a1_range}"
        return a1_range

    @staticmethod
    def _parse_timestamp(value: str) -> float:
        if not value:
            return 0.0
        try:
            if "T" in value:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.timestamp()
        except Exception:
            pass
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            return dt.timestamp()
        except Exception:
            return 0.0
