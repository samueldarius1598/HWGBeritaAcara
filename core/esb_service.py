import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import get_setting
from .credentials import (
    DEFAULT_CREDENTIALS_GID,
    DEFAULT_CREDENTIALS_SHEET,
    ESB_CREDENTIAL_RANGE,
    ESB_TOKEN_WRITE_RANGE,
    GoogleSheetCredentialsConfig,
    GoogleSheetCredentialsStore,
    build_esb_credentials,
)
from .esb_config import ESBConfigGAS

DEFAULT_ESB_BASE_URL = "https://services.esb.co.id/core"


def _coerce_int(value, default):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value: str) -> float:
    if not value:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.timestamp()
    except Exception:
        pass
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.timestamp()
        except Exception:
            continue
    return 0.0


def _mask_token(token: str, prefix: int = 6, suffix: int = 4) -> str:
    text = str(token or "")
    if not text:
        return ""
    if len(text) <= prefix + suffix:
        if len(text) <= prefix:
            return "*" * len(text)
        return f"{text[:prefix]}...{text[-suffix:]}"
    return f"{text[:prefix]}...{text[-suffix:]}"


class EsbService:
    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        config_manager: Optional[ESBConfigGAS] = None,
        sheet_store: Optional[GoogleSheetCredentialsStore] = None,
        timeout: int = 15,
        list_limit: int = 100,
        login_timeout: int = 10,
        detail_timeout: int = 5,
        token_ttl_sec: int = 3600,
        token_buffer_sec: int = 300,
        refresh_ttl_sec: int = 86400,
        product_detail_ttl_sec: int = 3600,
        product_list_ttl_sec: int = 600,
        flag_active: int = 1,
    ):
        self.base_url = (
            base_url
            or get_setting("ESB_BASE_URL")
            or get_setting("ESB_URL")
            or DEFAULT_ESB_BASE_URL
        ).rstrip("/")
        self.username = username or get_setting("ESB_USERNAME") or ""
        self.password = password or get_setting("ESB_PASSWORD") or ""
        self.config_manager = config_manager
        self.sheet_store = sheet_store

        self.timeout = timeout
        self.list_limit = list_limit
        self.login_timeout = login_timeout
        self.detail_timeout = detail_timeout
        self.token_ttl_sec = token_ttl_sec
        self.token_buffer_sec = token_buffer_sec
        self.refresh_ttl_sec = refresh_ttl_sec
        self.product_detail_ttl_sec = product_detail_ttl_sec
        self.product_list_ttl_sec = product_list_ttl_sec
        self.flag_active = flag_active

        self.session = requests.Session()
        self.token: Optional[str] = None
        self.token_expiry = 0.0
        self.company_code = ""
        self.company_name = ""
        self.access_token = ""
        self.refresh_token = ""
        self.token_timestamp_epoch = 0.0
        self.headers = {"Content-Type": "application/json"}
        self._config_loaded = False
        self._product_detail_cache: Dict[int, Dict[str, Any]] = {}
        self._product_list_cache: Dict[str, Any] = {"expires": 0.0, "data": []}

    def _get_sheet_store(self) -> Optional[GoogleSheetCredentialsStore]:
        if self.sheet_store is not None:
            return self.sheet_store
        config = GoogleSheetCredentialsConfig.from_settings(
            default_gid=DEFAULT_CREDENTIALS_GID,
            default_sheet_name=DEFAULT_CREDENTIALS_SHEET,
        )
        if not config.gas_url or not config.api_secret:
            return None
        self.sheet_store = GoogleSheetCredentialsStore(config)
        return self.sheet_store

    def _load_sheet_credentials(
        self,
    ) -> Tuple[Dict[str, str], Optional[GoogleSheetCredentialsStore]]:
        store = self._get_sheet_store()
        if not store:
            return {}, None
        try:
            values = store.fetch_range(ESB_CREDENTIAL_RANGE, value_type="raw")
            flat_vals = [row[0] if row else "" for row in values]
            while len(flat_vals) < 10:
                flat_vals.append("")
            raw_values = {
                "username_part1": flat_vals[0],
                "username_part2": flat_vals[1],
                "password": flat_vals[2],
                "company_code": flat_vals[5],
                "company_name": flat_vals[6],
                "access_token": flat_vals[7],
                "refresh_token": flat_vals[8],
                "token_timestamp": flat_vals[9],
            }
            creds = build_esb_credentials(raw_values)
            return creds, store
        except Exception as exc:
            print(f"[ESB Warning] Gagal ambil credentials dari Google Sheet: {exc}")
            return {}, store

    def _load_config_if_needed(self) -> None:
        if not self.config_manager or self._config_loaded:
            return
        self._config_loaded = True
        try:
            if not self.config_manager.load_config():
                return
        except Exception:
            return
        self.username = self.username or getattr(self.config_manager, "username", "") or ""
        self.password = self.password or getattr(self.config_manager, "password", "") or ""
        self.company_code = (
            getattr(self.config_manager, "company_code", "") or self.company_code
        )
        self.company_name = (
            getattr(self.config_manager, "company_name", "") or self.company_name
        )
        self.access_token = (
            getattr(self.config_manager, "access_token", "") or self.access_token
        )
        self.refresh_token = (
            getattr(self.config_manager, "refresh_token", "") or self.refresh_token
        )
        self.token_timestamp_epoch = (
            getattr(self.config_manager, "token_timestamp_epoch", 0.0)
            or self.token_timestamp_epoch
        )

    @staticmethod
    def _extract_session_payload(payload: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(payload, dict):
            raise RuntimeError("Response ESB tidak valid.")
        if payload.get("errors"):
            raise RuntimeError(f"ESB error: {payload.get('errors')}")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        access_token = result.get("accessToken") or result.get("access_token") or ""
        refresh_token = result.get("refreshToken") or result.get("refresh_token") or ""
        if not access_token:
            raise RuntimeError("Access Token tidak ditemukan dalam response ESB.")
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "company_code": result.get("companyCode") or "",
            "company_name": result.get("companyName") or "",
            "username": result.get("username") or "",
        }

    def _persist_session(
        self, target: Optional[object], session: Dict[str, str], timestamp_str: str
    ) -> None:
        if not target:
            return
        try:
            if isinstance(target, GoogleSheetCredentialsStore):
                values = [
                    [session.get("company_code", "")],
                    [session.get("company_name", "")],
                    [session.get("access_token", "")],
                    [session.get("refresh_token", "")],
                    [timestamp_str],
                ]
                target.set_range(ESB_TOKEN_WRITE_RANGE, values)
                return
            if isinstance(target, ESBConfigGAS):
                if hasattr(target, "update_session"):
                    target.update_session(
                        session.get("company_code", ""),
                        session.get("company_name", ""),
                        session.get("access_token", ""),
                        session.get("refresh_token", ""),
                        timestamp_str,
                    )
                else:
                    target.update_tokens(
                        session.get("access_token", ""), session.get("refresh_token", "")
                    )
        except Exception as exc:
            print(f"[ESB Warning] Gagal update session ke Google Sheet: {exc}")

    def _apply_session(
        self, session: Dict[str, str], persist_target: Optional[object] = None
    ) -> None:
        access_token = session.get("access_token") or ""
        if not access_token:
            raise RuntimeError("Access Token kosong.")
        refresh_token = session.get("refresh_token") or self.refresh_token
        company_code = session.get("company_code") or self.company_code
        company_name = session.get("company_name") or self.company_name
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.company_code = company_code
        self.company_name = company_name
        self.token = access_token
        self.headers["Authorization"] = f"Bearer {access_token}"

        ttl = _coerce_int(self.token_ttl_sec, 3600)
        buffer_sec = _coerce_int(self.token_buffer_sec, 300)
        self.token_expiry = time.time() + max(ttl - buffer_sec, 0)
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.token_timestamp_epoch = time.time()
        self._persist_session(
            persist_target,
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "company_code": company_code,
                "company_name": company_name,
            },
            timestamp_str,
        )

    def _login(self, username: str, password: str) -> Dict[str, str]:
        if not self.base_url:
            raise RuntimeError("ESB base URL belum dikonfigurasi.")
        url = f"{self.base_url}/auth/login"
        payload = {"username": username, "password": password}
        response = self.session.post(url, json=payload, timeout=self.login_timeout)
        response.raise_for_status()
        return self._extract_session_payload(response.json() or {})

    def _refresh(self, refresh_token: str) -> Dict[str, str]:
        if not self.base_url:
            raise RuntimeError("ESB base URL belum dikonfigurasi.")
        url = f"{self.base_url}/auth/refresh"
        headers = {
            "Authorization": f"Bearer {refresh_token}",
            "Content-Type": "application/json",
        }
        response = self.session.get(url, headers=headers, timeout=self.login_timeout)
        if response.status_code == 405:
            response = self.session.post(url, headers=headers, timeout=self.login_timeout)
        response.raise_for_status()
        return self._extract_session_payload(response.json() or {})

    def _ensure_access_token(self, *, force_login: bool = False) -> None:
        if not force_login and self.token and time.time() < self.token_expiry:
            return

        creds, persist_target = self._load_sheet_credentials()
        if creds:
            self.username = creds.get("username") or self.username
            self.password = creds.get("password") or self.password
            self.company_code = creds.get("company_code") or self.company_code
            self.company_name = creds.get("company_name") or self.company_name
            self.access_token = creds.get("access_token") or self.access_token
            self.refresh_token = creds.get("refresh_token") or self.refresh_token
            token_ts = creds.get("token_timestamp", "")
            self.token_timestamp_epoch = _parse_timestamp(token_ts) or self.token_timestamp_epoch
        else:
            self._load_config_if_needed()
            if not persist_target and self.config_manager and self._config_loaded:
                persist_target = self.config_manager

        username = self.username
        password = self.password
        access_token = self.access_token or self.token
        refresh_token = self.refresh_token
        ts_epoch = self.token_timestamp_epoch
        now = time.time()
        age = now - ts_epoch if ts_epoch else None

        ttl = _coerce_int(self.token_ttl_sec, 3600)
        buffer_sec = _coerce_int(self.token_buffer_sec, 300)
        access_valid_sec = max(ttl - buffer_sec, 0)

        if not force_login and access_token and age is not None:
            if 0 <= age < access_valid_sec:
                self.token = access_token
                self.headers["Authorization"] = f"Bearer {access_token}"
                remaining = max(access_valid_sec - age, 0)
                self.token_expiry = now + remaining
                return

        if not force_login and refresh_token:
            if age is None or age < self.refresh_ttl_sec:
                try:
                    session = self._refresh(refresh_token)
                    self._apply_session(session, persist_target)
                    return
                except Exception as exc:
                    print(f"[ESB Warning] Refresh token gagal: {exc}")

        if not username or not password:
            raise RuntimeError("ESB credentials not configured.")

        session = self._login(username, password)
        self._apply_session(session, persist_target)

    def _ensure_token(self) -> None:
        self._ensure_access_token()

    def _build_token_status(
        self,
        *,
        source: str,
        access_token: str,
        refresh_token: str,
        timestamp_epoch: float,
    ) -> Dict[str, Any]:
        now = time.time()
        age_sec = None
        if timestamp_epoch:
            age_sec = max(now - timestamp_epoch, 0)

        ttl = _coerce_int(self.token_ttl_sec, 3600)
        buffer_sec = _coerce_int(self.token_buffer_sec, 300)
        access_valid_sec = max(ttl - buffer_sec, 0)
        refresh_valid_sec = _coerce_int(self.refresh_ttl_sec, 86400)

        access_valid = bool(access_token) and age_sec is not None and age_sec < access_valid_sec
        refresh_valid = bool(refresh_token) and age_sec is not None and age_sec < refresh_valid_sec

        timestamp_text = ""
        if timestamp_epoch:
            timestamp_text = datetime.fromtimestamp(timestamp_epoch).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        return {
            "source": source,
            "timestamp_epoch": timestamp_epoch or 0.0,
            "timestamp_text": timestamp_text,
            "age_sec": age_sec,
            "access_valid_sec": access_valid_sec,
            "refresh_valid_sec": refresh_valid_sec,
            "access_token_present": bool(access_token),
            "refresh_token_present": bool(refresh_token),
            "access_token_masked": _mask_token(access_token),
            "refresh_token_masked": _mask_token(refresh_token),
            "access_valid": access_valid,
            "refresh_valid": refresh_valid,
        }

    def get_token_status(self, *, auto_refresh: bool = False) -> Dict[str, Any]:
        if auto_refresh:
            self._ensure_access_token()
            return self._build_token_status(
                source="runtime",
                access_token=self.access_token or self.token,
                refresh_token=self.refresh_token,
                timestamp_epoch=self.token_timestamp_epoch,
            )

        creds, _ = self._load_sheet_credentials()
        if creds:
            ts_epoch = _parse_timestamp(creds.get("token_timestamp", ""))
            return self._build_token_status(
                source="sheet",
                access_token=creds.get("access_token", ""),
                refresh_token=creds.get("refresh_token", ""),
                timestamp_epoch=ts_epoch,
            )

        self._load_config_if_needed()
        source = "config" if self._config_loaded else "memory"
        return self._build_token_status(
            source=source,
            access_token=self.access_token or self.token,
            refresh_token=self.refresh_token,
            timestamp_epoch=self.token_timestamp_epoch,
        )

    def get_product_detail(self, product_id: int) -> Dict[str, Any]:
        """
        Mengambil detail produk by ID untuk mendapatkan UOM dan Harga.
        """
        if not product_id:
            return {"uom_name": "", "price": 0.0}
        cached = self._product_detail_cache.get(product_id)
        if cached and time.time() < cached.get("expires", 0):
            return cached.get("data", {"uom_name": "", "price": 0.0})
        self._ensure_access_token()
        url = f"{self.base_url}/product/{product_id}"
        try:
            for attempt in range(2):
                try:
                    resp = self.session.get(
                        url, headers=self.headers, timeout=self.detail_timeout
                    )
                    if resp.status_code in (401, 403) and attempt == 0:
                        self._ensure_access_token(force_login=True)
                        continue
                    resp.raise_for_status()
                    data = resp.json() or {}
                    result = data.get("result", {}) or {}

                    details = result.get("productDetails", []) or []
                    selected_detail = {}
                    if details:
                        selected_detail = next(
                            (item for item in details if item.get("flagDefault")),
                            details[0],
                        )

                    result_payload = {
                        "uom_name": selected_detail.get("uomName", ""),
                        "price": float(selected_detail.get("basePrice", 0) or 0),
                    }
                    if self.product_detail_ttl_sec > 0:
                        self._product_detail_cache[product_id] = {
                            "expires": time.time() + self.product_detail_ttl_sec,
                            "data": result_payload,
                        }
                    return result_payload
                except requests.exceptions.RequestException:
                    if attempt == 1:
                        raise
                    time.sleep(1)
        except Exception as exc:
            print(f"[ESB Warning] Gagal ambil detail ID {product_id}: {exc}")
        return {"uom_name": "", "price": 0.0}

    def fetch_all_products(self) -> List[Dict[str, Any]]:
        """
        Menarik seluruh data produk (pagination loop + detail lookup).
        """
        now = time.time()
        if (
            self._product_list_cache["data"]
            and now < self._product_list_cache["expires"]
        ):
            return self._product_list_cache["data"]
        self._ensure_access_token()
        all_products: List[Dict[str, Any]] = []
        page = 1
        limit = self.list_limit

        while True:
            url = f"{self.base_url}/product/list"
            params = {"page": page, "limit": limit}
            if self.flag_active is not None:
                params["flagActive"] = self.flag_active

            try:
                resp = self.session.get(
                    url, headers=self.headers, params=params, timeout=self.timeout
                )
                if resp.status_code in (401, 403):
                    self._ensure_access_token(force_login=True)
                    resp = self.session.get(
                        url, headers=self.headers, params=params, timeout=self.timeout
                    )
                resp.raise_for_status()
                payload = resp.json() or {}
                result = payload.get("result", {}) or {}
                data_list = result.get("data", []) or []

                if not data_list:
                    break

                for item in data_list:
                    product_id = item.get("productID")
                    if not product_id:
                        continue
                    detail_info = self.get_product_detail(product_id)
                    all_products.append(
                        {
                            "id": product_id,
                            "name": item.get("productName"),
                            "default_code": item.get("productCode"),
                            "uom_name": detail_info.get("uom_name", ""),
                            "harga": detail_info.get("price", 0.0),
                            "source": "ESB",
                        }
                    )

                if len(data_list) < limit:
                    break
                page += 1
            except Exception as exc:
                print(f"[ESB Error] Fetch list page {page} failed: {str(exc)}")
                break

        if self.product_list_ttl_sec > 0:
            self._product_list_cache = {
                "expires": time.time() + self.product_list_ttl_sec,
                "data": all_products,
            }
        return all_products
