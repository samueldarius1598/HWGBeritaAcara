import json
import threading
import time
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, Callable

from .config import get_setting
from .database import get_supabase_admin_client
from .esb_service import ESB_SHARED_CACHE_KEY, EsbService
from .shared_cache import get_default_shared_cache

OUTLETS_CACHE_TTL = 300
PRODUCTS_CACHE_TTL = 1800

MASTER_PRODUCTS_MODE_DEFAULT = "fast"
MASTER_PRODUCTS_POLICY_DEFAULT = "odoo_first_hybrid_v1"
MASTER_PRODUCTS_ALLOW_ODOO_ONLY_DEFAULT = True
MASTER_PRODUCTS_REQUEST_BUDGET_MS_DEFAULT = 2800
MASTER_PRODUCTS_ODOO_SYNC_TIMEOUT_MS_DEFAULT = 2200
MASTER_PRODUCTS_ESB_SYNC_WAIT_MS_DEFAULT = 300
MASTER_PRODUCTS_REFRESH_INTERVAL_SEC_DEFAULT = 600

MASTER_PRODUCTS_ODOO_SOFT_TTL_DEFAULT = 600
MASTER_PRODUCTS_ODOO_STALE_TTL_DEFAULT = 3600
MASTER_PRODUCTS_ESB_SOFT_TTL_DEFAULT = 600
MASTER_PRODUCTS_ESB_STALE_TTL_DEFAULT = 3600
MASTER_PRODUCTS_MERGED_SOFT_TTL_DEFAULT = 600
MASTER_PRODUCTS_MERGED_STALE_TTL_DEFAULT = 3600

MASTER_PRODUCTS_LOCK_TTL_SEC_DEFAULT = 45
MASTER_PRODUCTS_WAIT_TIMEOUT_SEC_DEFAULT = 20.0
MASTER_PRODUCTS_STALE_WAIT_TIMEOUT_SEC_DEFAULT = 0.35
MASTER_PRODUCTS_JITTER_RATIO_DEFAULT = 0.1

CACHE_KEY_ODOO_PREFIX = "master_products_v2:odoo:"
CACHE_KEY_MERGED_PREFIX = "master_products_v2:merged:"
CACHE_KEY_ACTIVE_OUTLETS = "master_products_v2:active_outlets"
REFRESH_CYCLE_LOCK_KEY = "master_products_v2:refresh_cycle"

_OUTLETS_CACHE = {"expires": 0.0, "data": []}
_ESB_SERVICE: EsbService | None = None
_ESB_SERVICE_GUARD = threading.Lock()

_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()

_BG_TASKS: set[str] = set()
_BG_TASKS_GUARD = threading.Lock()
_BG_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="master-products")

_SCHEDULER_STARTED = False
_SCHEDULER_GUARD = threading.Lock()


def _coerce_int(value, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _setting_int(key: str, default: int) -> int:
    return _coerce_int(get_setting(key), default)


def _setting_float(key: str, default: float) -> float:
    return _coerce_float(get_setting(key), default)


def _setting_bool(key: str, default: bool) -> bool:
    return _coerce_bool(get_setting(key), default)


def _products_mode() -> str:
    value = str(get_setting("MASTER_PRODUCTS_MODE") or MASTER_PRODUCTS_MODE_DEFAULT)
    value = value.strip().lower()
    return value or MASTER_PRODUCTS_MODE_DEFAULT


def _products_policy() -> str:
    value = str(
        get_setting("MASTER_PRODUCTS_POLICY") or MASTER_PRODUCTS_POLICY_DEFAULT
    ).strip()
    value = value.lower()
    return value or MASTER_PRODUCTS_POLICY_DEFAULT


def _allow_odoo_only() -> bool:
    return _setting_bool(
        "MASTER_PRODUCTS_ALLOW_ODOO_ONLY", MASTER_PRODUCTS_ALLOW_ODOO_ONLY_DEFAULT
    )


def _request_budget_ms() -> int:
    return max(
        _setting_int(
            "MASTER_PRODUCTS_REQUEST_BUDGET_MS",
            MASTER_PRODUCTS_REQUEST_BUDGET_MS_DEFAULT,
        ),
        0,
    )


def _odoo_sync_timeout_ms() -> int:
    return max(
        _setting_int(
            "MASTER_PRODUCTS_ODOO_SYNC_TIMEOUT_MS",
            MASTER_PRODUCTS_ODOO_SYNC_TIMEOUT_MS_DEFAULT,
        ),
        0,
    )


def _esb_sync_wait_ms() -> int:
    return max(
        _setting_int(
            "MASTER_PRODUCTS_ESB_SYNC_WAIT_MS",
            MASTER_PRODUCTS_ESB_SYNC_WAIT_MS_DEFAULT,
        ),
        0,
    )


def _refresh_interval_sec() -> int:
    return max(
        _setting_int(
            "MASTER_PRODUCTS_REFRESH_INTERVAL_SEC",
            MASTER_PRODUCTS_REFRESH_INTERVAL_SEC_DEFAULT,
        ),
        30,
    )


def _odoo_soft_ttl() -> int:
    return max(
        _setting_int(
            "MASTER_PRODUCTS_ODOO_SOFT_TTL_SEC", MASTER_PRODUCTS_ODOO_SOFT_TTL_DEFAULT
        ),
        0,
    )


def _odoo_stale_ttl() -> int:
    stale = _setting_int(
        "MASTER_PRODUCTS_ODOO_STALE_TTL_SEC", MASTER_PRODUCTS_ODOO_STALE_TTL_DEFAULT
    )
    return max(stale, _odoo_soft_ttl())


def _esb_soft_ttl() -> int:
    return max(
        _setting_int(
            "MASTER_PRODUCTS_ESB_SOFT_TTL_SEC", MASTER_PRODUCTS_ESB_SOFT_TTL_DEFAULT
        ),
        0,
    )


def _esb_stale_ttl() -> int:
    stale = _setting_int(
        "MASTER_PRODUCTS_ESB_STALE_TTL_SEC", MASTER_PRODUCTS_ESB_STALE_TTL_DEFAULT
    )
    return max(stale, _esb_soft_ttl())


def _merged_soft_ttl() -> int:
    return max(
        _setting_int(
            "MASTER_PRODUCTS_MERGED_SOFT_TTL_SEC",
            MASTER_PRODUCTS_MERGED_SOFT_TTL_DEFAULT,
        ),
        0,
    )


def _merged_stale_ttl() -> int:
    stale = _setting_int(
        "MASTER_PRODUCTS_MERGED_STALE_TTL_SEC",
        MASTER_PRODUCTS_MERGED_STALE_TTL_DEFAULT,
    )
    return max(stale, _merged_soft_ttl())


def _lock_ttl_sec() -> float:
    return max(
        _setting_float(
            "MASTER_PRODUCTS_LOCK_TTL_SEC", MASTER_PRODUCTS_LOCK_TTL_SEC_DEFAULT
        ),
        1.0,
    )


def _wait_timeout_sec() -> float:
    return max(
        _setting_float(
            "MASTER_PRODUCTS_WAIT_TIMEOUT_SEC", MASTER_PRODUCTS_WAIT_TIMEOUT_SEC_DEFAULT
        ),
        0.1,
    )


def _stale_wait_timeout_sec() -> float:
    return max(
        _setting_float(
            "MASTER_PRODUCTS_STALE_WAIT_TIMEOUT_SEC",
            MASTER_PRODUCTS_STALE_WAIT_TIMEOUT_SEC_DEFAULT,
        ),
        0.0,
    )


def _jitter_ratio() -> float:
    return max(
        _setting_float(
            "MASTER_PRODUCTS_TTL_JITTER_RATIO", MASTER_PRODUCTS_JITTER_RATIO_DEFAULT
        ),
        0.0,
    )


def _cache_lock_key(cache_key: str) -> str:
    return f"lock:{cache_key}"


def _get_local_key_lock(cache_key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[cache_key] = lock
        return lock


def _submit_background_task(
    task_key: str, func: Callable[..., Any], *args, **kwargs
) -> bool:
    with _BG_TASKS_GUARD:
        if task_key in _BG_TASKS:
            return False
        _BG_TASKS.add(task_key)

    def _runner():
        try:
            func(*args, **kwargs)
        except Exception as exc:
            print(f"[MasterProducts Warning] Background task {task_key} gagal: {exc}")
        finally:
            with _BG_TASKS_GUARD:
                _BG_TASKS.discard(task_key)

    _BG_EXECUTOR.submit(_runner)
    return True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _normalize_product(item: dict[str, Any], source: str = "") -> dict[str, Any]:
    payload = {
        "id": item.get("id"),
        "name": _safe_str(item.get("name")),
        "default_code": _safe_str(item.get("default_code")),
        "uom_name": _safe_str(item.get("uom_name")),
        "harga": _safe_float(item.get("harga"), 0.0),
    }
    source_value = source or _safe_str(item.get("source"))
    if source_value:
        payload["source"] = source_value
    return payload


def _normalize_products(
    items: list[dict[str, Any]], source: str = ""
) -> list[dict[str, Any]]:
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        product = _normalize_product(item, source=source)
        if not product.get("name") and not product.get("default_code"):
            continue
        normalized.append(product)
    return normalized


def _dummy_products() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "name": "Produk Dummy 1",
            "default_code": "PRD-001",
            "uom_name": "PCS",
            "harga": 0.0,
        },
        {
            "id": 2,
            "name": "Produk Dummy 2",
            "default_code": "PRD-002",
            "uom_name": "PCS",
            "harga": 0.0,
        },
    ]


def _product_key(item: dict[str, Any]) -> str:
    code = _safe_str(item.get("default_code")).lower()
    if code:
        return code
    return _safe_str(item.get("name")).lower()


def _merge_products_hybrid(
    odoo_products: list[dict[str, Any]], esb_products: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    esb_by_key: dict[str, dict[str, Any]] = {}
    for item in esb_products or []:
        key = _product_key(item)
        if not key:
            continue
        if key not in esb_by_key:
            esb_by_key[key] = _normalize_product(item, source="ESB")

    merged: dict[str, dict[str, Any]] = {}
    for item in odoo_products or []:
        odoo_item = _normalize_product(item, source="Odoo")
        key = _product_key(odoo_item)
        if not key:
            continue
        esb_item = esb_by_key.get(key)
        if not esb_item:
            merged[key] = odoo_item
            continue

        price = _safe_float(odoo_item.get("harga"), 0.0)
        if price > 0:
            merged[key] = odoo_item
            continue

        # Hybrid rule: jika harga Odoo <= 0, ambil harga/UOM dari ESB jika ada.
        merged_item = dict(odoo_item)
        merged_item["harga"] = _safe_float(esb_item.get("harga"), 0.0)
        merged_item["uom_name"] = _safe_str(esb_item.get("uom_name")) or _safe_str(
            odoo_item.get("uom_name")
        )
        merged[key] = merged_item

    for key, item in esb_by_key.items():
        if key in merged:
            continue
        merged[key] = item
    return list(merged.values())


def _log_master_products_event(**fields):
    payload = {"event": "get_master_products", **fields}
    print("[MasterProducts] " + json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def _merged_payload(products: list[dict[str, Any]], completeness: str) -> dict[str, Any]:
    return {
        "items": products,
        "completeness": completeness,
        "source": _products_policy(),
        "updated_at": time.time(),
    }


def _extract_merged_payload(payload: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(payload, dict):
        products = _normalize_products(payload.get("items") or [])
        completeness = _safe_str(payload.get("completeness")).lower() or "merged"
        return products, completeness
    if isinstance(payload, list):
        return _normalize_products(payload), "merged"
    return [], "merged"


def _merged_cache_key(company_id: Any) -> str:
    return f"{CACHE_KEY_MERGED_PREFIX}{company_id}"


def _odoo_cache_key(company_id: Any) -> str:
    return f"{CACHE_KEY_ODOO_PREFIX}{company_id}"


def _set_merged_cache(
    company_id: Any, products: list[dict[str, Any]], completeness: str
) -> None:
    if not products:
        return
    try:
        get_default_shared_cache().set_cache_entry(
            _merged_cache_key(company_id),
            _merged_payload(products, completeness),
            soft_ttl=_merged_soft_ttl(),
            stale_ttl=_merged_stale_ttl(),
            jitter_ratio=_jitter_ratio(),
        )
    except Exception as exc:
        print(f"[MasterProducts Warning] Gagal menulis merged cache: {exc}")


def _set_active_outlets_cache(outlet_ids: list[Any]) -> None:
    try:
        get_default_shared_cache().set_cache_entry(
            CACHE_KEY_ACTIVE_OUTLETS,
            list(outlet_ids or []),
            soft_ttl=_refresh_interval_sec(),
            stale_ttl=max(_refresh_interval_sec() * 6, _refresh_interval_sec()),
            jitter_ratio=_jitter_ratio(),
        )
    except Exception as exc:
        print(f"[MasterProducts Warning] Gagal menulis active outlets cache: {exc}")


def _get_esb_service() -> EsbService:
    global _ESB_SERVICE
    if _ESB_SERVICE is not None:
        return _ESB_SERVICE
    with _ESB_SERVICE_GUARD:
        if _ESB_SERVICE is not None:
            return _ESB_SERVICE
        _ESB_SERVICE = EsbService()
    return _ESB_SERVICE


def get_odoo_credentials():
    required = ["ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"]
    missing = [key for key in required if not get_setting(key)]
    if missing:
        return None, missing
    return (
        {
            "url": get_setting("ODOO_URL").rstrip("/"),
            "db": get_setting("ODOO_DB"),
            "username": get_setting("ODOO_USERNAME"),
            "password": get_setting("ODOO_PASSWORD"),
        },
        [],
    )


def _fetch_products_from_odoo(creds, company_id) -> list[dict[str, Any]]:
    common = xmlrpc.client.ServerProxy(f"{creds['url']}/xmlrpc/2/common")
    uid = common.authenticate(creds["db"], creds["username"], creds["password"], {})
    if not uid:
        raise RuntimeError("Autentikasi Odoo gagal.")

    models = xmlrpc.client.ServerProxy(f"{creds['url']}/xmlrpc/2/object")
    company_value = normalize_outlet_id(company_id)
    if isinstance(company_value, str) and company_value.isdigit():
        company_value = int(company_value)

    data = models.execute_kw(
        creds["db"],
        uid,
        creds["password"],
        "product.template",
        "search_read",
        [
            [
                ("qty_available", "!=", 0),
                ("standard_price", ">", 0),
                ("is_holypos", "=", False),
            ]
        ],
        {
            "fields": [
                "name",
                "default_code",
                "uom_id",
                "standard_price",
                "is_holypos",
            ],
            "context": {
                "company_id": company_value,
                "allowed_company_ids": [company_value],
            },
        },
    )

    products = []
    for row in data:
        uom_name = ""
        if row.get("uom_id") and isinstance(row["uom_id"], list):
            uom_name = _safe_str(row["uom_id"][1])
        products.append(
            {
                "id": row.get("id"),
                "name": _safe_str(row.get("name")),
                "default_code": _safe_str(row.get("default_code")),
                "uom_name": uom_name,
                "harga": _safe_float(row.get("standard_price"), 0.0),
                "source": "Odoo",
            }
        )
    return _normalize_products(products, source="Odoo")


def _fetch_products_from_esb(
    *, allow_stale: bool, force_refresh: bool
) -> list[dict[str, Any]]:
    service = _get_esb_service()
    if hasattr(service, "fetch_all_products_full"):
        return service.fetch_all_products_full(
            allow_stale=allow_stale, force_refresh=force_refresh
        )
    return service.fetch_all_products(allow_stale=allow_stale, force_refresh=force_refresh)


def get_master_outlets():
    now = time.time()
    if _OUTLETS_CACHE["data"] and now < _OUTLETS_CACHE["expires"]:
        return _OUTLETS_CACHE["data"]

    creds, missing = get_odoo_credentials()
    if missing:
        outlets = [
            {"id": 1, "name": "Outlet Dummy A"},
            {"id": 2, "name": "Outlet Dummy B"},
            {"id": 3, "name": "Outlet Dummy C"},
        ]
        _OUTLETS_CACHE["data"] = outlets
        _OUTLETS_CACHE["expires"] = now + OUTLETS_CACHE_TTL
        return outlets

    try:
        common = xmlrpc.client.ServerProxy(f"{creds['url']}/xmlrpc/2/common")
        uid = common.authenticate(creds["db"], creds["username"], creds["password"], {})
        if not uid:
            raise RuntimeError("Autentikasi Odoo gagal.")
        models = xmlrpc.client.ServerProxy(f"{creds['url']}/xmlrpc/2/object")
        data = models.execute_kw(
            creds["db"],
            uid,
            creds["password"],
            "res.company",
            "search_read",
            [[]],
            {"fields": ["name"]},
        )
        outlets = [
            {"id": row.get("id"), "name": row.get("name")}
            for row in data
            if row.get("name")
        ]
        if not outlets:
            outlets = [
                {"id": 1, "name": "Outlet Dummy A"},
                {"id": 2, "name": "Outlet Dummy B"},
                {"id": 3, "name": "Outlet Dummy C"},
            ]
        _OUTLETS_CACHE["data"] = outlets
        _OUTLETS_CACHE["expires"] = now + OUTLETS_CACHE_TTL
        return outlets
    except Exception as exc:
        print(f"[MasterProducts Warning] Gagal ambil outlet Odoo: {exc}")
        if _OUTLETS_CACHE["data"]:
            return _OUTLETS_CACHE["data"]
        return [
            {"id": 1, "name": "Outlet Dummy A"},
            {"id": 2, "name": "Outlet Dummy B"},
            {"id": 3, "name": "Outlet Dummy C"},
        ]


def _refresh_cache_sync(
    cache_key: str,
    fetcher: Callable[[], list[dict[str, Any]]],
    *,
    soft_ttl: int,
    stale_ttl: int,
    allow_stale_fallback: bool = True,
    allow_empty_write: bool = True,
) -> list[dict[str, Any]]:
    shared = get_default_shared_cache()
    local_lock = _get_local_key_lock(cache_key)
    if not local_lock.acquire(timeout=_wait_timeout_sec()):
        fallback = shared.get_cache_entry(cache_key, allow_stale=allow_stale_fallback)
        return fallback.get("data") or []

    owner = None
    lock_key = _cache_lock_key(cache_key)
    try:
        current = shared.get_cache_entry(cache_key, allow_stale=allow_stale_fallback)
        if current["state"] == "fresh":
            return current.get("data") or []

        acquired, owner = shared.acquire_lock(
            lock_key,
            lock_ttl_sec=_lock_ttl_sec(),
            wait_timeout_sec=0.0,
        )
        if acquired:
            try:
                data = fetcher() or []
            except Exception as exc:
                print(f"[MasterProducts Warning] Fetch cache key {cache_key} gagal: {exc}")
                data = []
            if data or allow_empty_write:
                shared.set_cache_entry(
                    cache_key,
                    data,
                    soft_ttl=soft_ttl,
                    stale_ttl=stale_ttl,
                    jitter_ratio=_jitter_ratio(),
                )
                return data
            if (
                current["state"] in {"fresh", "stale"}
                and current.get("data") is not None
            ):
                return current.get("data") or []
            return []

        wait_sec = (
            _stale_wait_timeout_sec()
            if current["state"] == "stale"
            else _wait_timeout_sec()
        )
        deadline = time.time() + wait_sec
        latest = current
        while time.time() < deadline:
            time.sleep(0.05)
            latest = shared.get_cache_entry(cache_key, allow_stale=allow_stale_fallback)
            if latest["state"] in {"fresh", "stale"} and latest.get("data") is not None:
                return latest.get("data") or []
        return latest.get("data") or []
    finally:
        if owner:
            shared.release_lock(lock_key, owner)
        local_lock.release()


def _refresh_cache_background(
    cache_key: str,
    fetcher: Callable[[], list[dict[str, Any]]],
    *,
    soft_ttl: int,
    stale_ttl: int,
    allow_empty_write: bool = True,
) -> bool:
    shared = get_default_shared_cache()
    local_lock = _get_local_key_lock(cache_key)
    if not local_lock.acquire(blocking=False):
        return False

    owner = None
    lock_key = _cache_lock_key(cache_key)
    try:
        acquired, owner = shared.acquire_lock(
            lock_key,
            lock_ttl_sec=_lock_ttl_sec(),
            wait_timeout_sec=0.0,
        )
        if not acquired:
            return False

        current = shared.get_cache_entry(cache_key, allow_stale=True)
        try:
            data = fetcher() or []
        except Exception as exc:
            print(f"[MasterProducts Warning] Background refresh {cache_key} gagal: {exc}")
            data = []

        if data or allow_empty_write:
            shared.set_cache_entry(
                cache_key,
                data,
                soft_ttl=soft_ttl,
                stale_ttl=stale_ttl,
                jitter_ratio=_jitter_ratio(),
            )
            return True
        if current["state"] in {"fresh", "stale"} and current.get("data") is not None:
            return False
        return False
    finally:
        if owner:
            shared.release_lock(lock_key, owner)
        local_lock.release()


def _refresh_esb_products_sync() -> list[dict[str, Any]]:
    return _refresh_cache_sync(
        ESB_SHARED_CACHE_KEY,
        lambda: _fetch_products_from_esb(allow_stale=False, force_refresh=True),
        soft_ttl=_esb_soft_ttl(),
        stale_ttl=_esb_stale_ttl(),
        allow_stale_fallback=True,
        allow_empty_write=False,
    )


def _refresh_esb_products_background() -> bool:
    return _refresh_cache_background(
        ESB_SHARED_CACHE_KEY,
        lambda: _fetch_products_from_esb(allow_stale=False, force_refresh=True),
        soft_ttl=_esb_soft_ttl(),
        stale_ttl=_esb_stale_ttl(),
        allow_empty_write=False,
    )


def _fetch_odoo_products_safe(company_id: Any) -> list[dict[str, Any]]:
    creds, missing = get_odoo_credentials()
    if missing:
        return []
    try:
        return _fetch_products_from_odoo(creds, company_id)
    except Exception as exc:
        print(
            f"[MasterProducts Warning] Odoo fetch gagal untuk company {company_id}: {exc}"
        )
        return []


def _refresh_odoo_products_sync(company_id: Any) -> list[dict[str, Any]]:
    return _refresh_cache_sync(
        _odoo_cache_key(company_id),
        lambda: _fetch_odoo_products_safe(company_id),
        soft_ttl=_odoo_soft_ttl(),
        stale_ttl=_odoo_stale_ttl(),
        allow_stale_fallback=True,
        allow_empty_write=False,
    )


def _refresh_odoo_products_background(company_id: Any) -> bool:
    return _refresh_cache_background(
        _odoo_cache_key(company_id),
        lambda: _fetch_odoo_products_safe(company_id),
        soft_ttl=_odoo_soft_ttl(),
        stale_ttl=_odoo_stale_ttl(),
        allow_empty_write=False,
    )


def _schedule_esb_refresh() -> None:
    _submit_background_task("refresh:esb_products_v2", _refresh_esb_products_background)


def _schedule_odoo_refresh(company_id: Any) -> None:
    _submit_background_task(
        f"refresh:odoo_products_v2:{company_id}",
        _refresh_odoo_products_background,
        company_id,
    )


def _wait_cache(cache_key: str, wait_sec: float, *, allow_stale: bool = True) -> dict[str, Any]:
    shared = get_default_shared_cache()
    if wait_sec <= 0:
        return shared.get_cache_entry(cache_key, allow_stale=allow_stale)
    deadline = time.time() + wait_sec
    latest = shared.get_cache_entry(cache_key, allow_stale=allow_stale)
    while time.time() < deadline:
        if latest["state"] in {"fresh", "stale"} and latest.get("data") is not None:
            return latest
        time.sleep(0.05)
        latest = shared.get_cache_entry(cache_key, allow_stale=allow_stale)
    return latest


def _resolve_esb_for_request(deadline: float) -> tuple[list[dict[str, Any]], str]:
    shared = get_default_shared_cache()
    entry = shared.get_cache_entry(ESB_SHARED_CACHE_KEY, allow_stale=True)
    state = entry["state"]
    data = _normalize_products(entry.get("data") or [], source="ESB")

    if state == "fresh" and data:
        return data, "fresh"
    if state == "stale" and data:
        _schedule_esb_refresh()
        return data, "stale"

    _schedule_esb_refresh()
    remaining = max(deadline - time.perf_counter(), 0.0)
    wait_sec = min(_esb_sync_wait_ms() / 1000.0, remaining)
    latest = _wait_cache(ESB_SHARED_CACHE_KEY, wait_sec, allow_stale=True)
    latest_state = latest["state"]
    latest_data = _normalize_products(latest.get("data") or [], source="ESB")
    if latest_state == "stale" and latest_data:
        _schedule_esb_refresh()
    if latest_data:
        return latest_data, latest_state
    return [], "miss"


def _resolve_odoo_for_request(
    company_id: Any,
    deadline: float,
) -> tuple[list[dict[str, Any]], str, str]:
    shared = get_default_shared_cache()
    cache_key = _odoo_cache_key(company_id)
    entry = shared.get_cache_entry(cache_key, allow_stale=True)
    state = entry["state"]
    data = _normalize_products(entry.get("data") or [], source="Odoo")

    if state == "fresh" and data:
        return data, "fresh", "fresh_cache"
    if state == "stale" and data:
        _schedule_odoo_refresh(company_id)
        return data, "stale", "stale_cache"

    remaining = max(deadline - time.perf_counter(), 0.0)
    timeout_sec = min(_odoo_sync_timeout_ms() / 1000.0, remaining)
    if timeout_sec <= 0:
        _schedule_odoo_refresh(company_id)
        return [], "miss", "budget_timeout"

    future = _BG_EXECUTOR.submit(_refresh_odoo_products_sync, company_id)
    try:
        refreshed = _normalize_products(future.result(timeout=timeout_sec), source="Odoo")
        if refreshed:
            return refreshed, "fresh", "sync_refresh"
    except TimeoutError:
        _schedule_odoo_refresh(company_id)
        fallback = shared.get_cache_entry(cache_key, allow_stale=True)
        fallback_data = _normalize_products(fallback.get("data") or [], source="Odoo")
        if fallback_data:
            return fallback_data, fallback["state"], "timeout_fallback"
        return [], "miss", "sync_timeout"
    except Exception as exc:
        print(
            f"[MasterProducts Warning] Odoo sync refresh gagal untuk company {company_id}: {exc}"
        )
        _schedule_odoo_refresh(company_id)

    latest = shared.get_cache_entry(cache_key, allow_stale=True)
    latest_data = _normalize_products(latest.get("data") or [], source="Odoo")
    if latest_data:
        return latest_data, latest["state"], "post_refresh_cache"
    return [], "miss", "miss"


def _derive_cache_state(odoo_state: str, esb_state: str, completeness: str) -> str:
    if completeness == "odoo_only":
        if odoo_state == "stale":
            return "stale"
        if odoo_state == "fresh":
            return "fresh"
        return "miss"
    if completeness == "esb_only":
        if esb_state == "stale":
            return "stale"
        if esb_state == "fresh":
            return "fresh"
        return "miss"
    if "fresh" in {odoo_state, esb_state}:
        return "fresh"
    if "stale" in {odoo_state, esb_state}:
        return "stale"
    return "miss"


def _build_products(
    odoo_products: list[dict[str, Any]],
    esb_products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    if odoo_products and esb_products:
        return _merge_products_hybrid(odoo_products, esb_products), "merged"
    if odoo_products:
        if _allow_odoo_only():
            return odoo_products, "odoo_only"
        return _merge_products_hybrid(odoo_products, esb_products), "merged"
    if esb_products:
        return esb_products, "esb_only"
    return [], "esb_only"


def _rebuild_merged_cache_for_company(company_id: Any) -> tuple[list[dict[str, Any]], str]:
    shared = get_default_shared_cache()
    odoo_entry = shared.get_cache_entry(_odoo_cache_key(company_id), allow_stale=True)
    esb_entry = shared.get_cache_entry(ESB_SHARED_CACHE_KEY, allow_stale=True)
    odoo_products = _normalize_products(odoo_entry.get("data") or [], source="Odoo")
    esb_products = _normalize_products(esb_entry.get("data") or [], source="ESB")
    products, completeness = _build_products(odoo_products, esb_products)
    if products:
        _set_merged_cache(company_id, products, completeness)
    return products, completeness


def _refresh_company_background(company_id: Any) -> None:
    _refresh_odoo_products_sync(company_id)
    shared = get_default_shared_cache()
    esb_entry = shared.get_cache_entry(ESB_SHARED_CACHE_KEY, allow_stale=True)
    if esb_entry["state"] != "fresh":
        _refresh_esb_products_sync()
    _rebuild_merged_cache_for_company(company_id)


def _schedule_company_refresh(company_id: Any) -> None:
    _submit_background_task(
        f"refresh:company_products_v2:{company_id}",
        _refresh_company_background,
        company_id,
    )


def _fetch_active_outlet_ids() -> list[Any]:
    supabase = get_supabase_admin_client()
    if not supabase:
        return []
    try:
        response = supabase.table("profiles").select("outlet_id").execute()
        rows = response.data or []
    except Exception as exc:
        print(f"[MasterProducts Warning] Gagal membaca profiles.outlet_id: {exc}")
        return []

    active_ids = []
    seen = set()
    for row in rows:
        outlet_id = normalize_outlet_id((row or {}).get("outlet_id"))
        if outlet_id in (None, ""):
            continue
        key = str(outlet_id)
        if key in seen:
            continue
        seen.add(key)
        active_ids.append(outlet_id)
    return active_ids


def _run_refresh_cycle() -> None:
    shared = get_default_shared_cache()
    lock_key = _cache_lock_key(REFRESH_CYCLE_LOCK_KEY)
    cycle_ttl = max(min(float(_refresh_interval_sec()), 300.0), 60.0)
    acquired, owner = shared.acquire_lock(
        lock_key,
        lock_ttl_sec=cycle_ttl,
        wait_timeout_sec=0.0,
    )
    if not acquired:
        return

    started = time.perf_counter()
    esb_count = 0
    outlet_count = 0
    try:
        esb_products = _refresh_esb_products_sync()
        esb_count = len(esb_products or [])

        active_outlets = _fetch_active_outlet_ids()
        if active_outlets:
            _set_active_outlets_cache(active_outlets)
        else:
            cached = shared.get_cache_entry(CACHE_KEY_ACTIVE_OUTLETS, allow_stale=True)
            active_outlets = cached.get("data") or []

        for outlet_id in active_outlets:
            try:
                _refresh_odoo_products_sync(outlet_id)
                _rebuild_merged_cache_for_company(outlet_id)
                outlet_count += 1
            except Exception as exc:
                print(
                    f"[MasterProducts Warning] Refresh outlet {outlet_id} gagal: {exc}"
                )
    finally:
        shared.release_lock(lock_key, owner)
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        _log_master_products_event(
            mode=_products_mode(),
            source=_products_policy(),
            phase="refresh_cycle",
            duration_ms=duration_ms,
            esb_count=esb_count,
            outlet_count=outlet_count,
        )


def _refresh_scheduler_loop() -> None:
    _submit_background_task("refresh:cycle:start", _run_refresh_cycle)
    while True:
        time.sleep(max(_refresh_interval_sec(), 30))
        _submit_background_task("refresh:cycle:periodic", _run_refresh_cycle)


def get_master_products_result(company_id):
    total_start = time.perf_counter()
    policy = _products_policy()
    mode = _products_mode()

    if company_id is None:
        return {
            "data": [],
            "cache_state": "miss",
            "completeness": "esb_only",
            "mode": mode,
            "source": policy,
        }

    normalized_company_id = normalize_outlet_id(company_id)
    if normalized_company_id in ("", None):
        return {
            "data": [],
            "cache_state": "miss",
            "completeness": "esb_only",
            "mode": mode,
            "source": policy,
        }

    company_key = normalized_company_id
    shared = get_default_shared_cache()
    merged_key = _merged_cache_key(company_key)
    merged_entry = shared.get_cache_entry(merged_key, allow_stale=True)
    merged_state = merged_entry["state"]
    merged_products, merged_completeness = _extract_merged_payload(
        merged_entry.get("data")
    )

    if merged_state == "fresh" and merged_products:
        total_ms = round((time.perf_counter() - total_start) * 1000.0, 2)
        _log_master_products_event(
            mode=mode,
            source=policy,
            company_id=str(company_key),
            cache_state="fresh",
            completeness=merged_completeness,
            esb_fetch_ms=0.0,
            odoo_fetch_ms=0.0,
            merge_ms=0.0,
            total_ms=total_ms,
            esb_count=0,
            odoo_count=0,
        )
        return {
            "data": merged_products,
            "cache_state": "fresh",
            "completeness": merged_completeness,
            "mode": mode,
            "source": policy,
        }

    if merged_state == "stale" and merged_products:
        _schedule_company_refresh(company_key)
        total_ms = round((time.perf_counter() - total_start) * 1000.0, 2)
        _log_master_products_event(
            mode=mode,
            source=policy,
            company_id=str(company_key),
            cache_state="stale",
            completeness=merged_completeness,
            esb_fetch_ms=0.0,
            odoo_fetch_ms=0.0,
            merge_ms=0.0,
            total_ms=total_ms,
            esb_count=0,
            odoo_count=0,
        )
        return {
            "data": merged_products,
            "cache_state": "stale",
            "completeness": merged_completeness,
            "mode": mode,
            "source": policy,
        }

    budget_ms = _request_budget_ms()
    deadline = time.perf_counter() + (budget_ms / 1000.0)

    odoo_start = time.perf_counter()
    odoo_products, odoo_state, odoo_status = _resolve_odoo_for_request(
        company_key, deadline
    )
    odoo_ms = (time.perf_counter() - odoo_start) * 1000.0

    esb_start = time.perf_counter()
    esb_products, esb_state = _resolve_esb_for_request(deadline)
    esb_ms = (time.perf_counter() - esb_start) * 1000.0

    merge_start = time.perf_counter()
    products, completeness = _build_products(odoo_products, esb_products)
    merge_ms = (time.perf_counter() - merge_start) * 1000.0

    if products:
        cache_state = _derive_cache_state(odoo_state, esb_state, completeness)
        _set_merged_cache(company_key, products, completeness)
        if completeness == "odoo_only":
            _schedule_esb_refresh()
            _schedule_company_refresh(company_key)
        elif completeness == "esb_only":
            _schedule_odoo_refresh(company_key)
            _schedule_company_refresh(company_key)
    else:
        products = _dummy_products()
        cache_state = "miss"
        completeness = "esb_only"
        _schedule_company_refresh(company_key)

    total_ms = round((time.perf_counter() - total_start) * 1000.0, 2)
    _log_master_products_event(
        mode=mode,
        source=policy,
        company_id=str(company_key),
        cache_state=cache_state,
        completeness=completeness,
        esb_fetch_ms=round(esb_ms, 2),
        odoo_fetch_ms=round(odoo_ms, 2),
        merge_ms=round(merge_ms, 2),
        total_ms=total_ms,
        esb_count=len(esb_products),
        odoo_count=len(odoo_products),
        odoo_state=odoo_state,
        esb_state=esb_state,
        odoo_status=odoo_status,
    )
    return {
        "data": products,
        "cache_state": cache_state,
        "completeness": completeness,
        "mode": mode,
        "source": policy,
    }


def get_master_products(company_id):
    result = get_master_products_result(company_id)
    return result.get("data") or []


def prewarm_master_products_async():
    if not _setting_bool("MASTER_PRODUCTS_PREWARM_ON_STARTUP", True):
        return
    global _SCHEDULER_STARTED
    with _SCHEDULER_GUARD:
        if _SCHEDULER_STARTED:
            return
        _SCHEDULER_STARTED = True
        thread = threading.Thread(
            target=_refresh_scheduler_loop,
            name="master-products-scheduler",
            daemon=True,
        )
        thread.start()


def normalize_outlet_id(outlet_id):
    value = str(outlet_id or "").strip()
    if not value:
        return ""
    if value.isdigit():
        return int(value)
    return value


def resolve_outlet_id(outlet_id, outlet_name):
    if outlet_id not in (None, ""):
        return str(outlet_id)
    if not outlet_name:
        return ""
    target = str(outlet_name).strip().lower()
    outlets = get_master_outlets()
    for outlet in outlets:
        if str(outlet.get("name", "")).strip().lower() == target:
            return str(outlet.get("id") or "")
    return ""


def get_outlet_by_id(outlet_id):
    if outlet_id in (None, ""):
        return None
    target = str(outlet_id)
    outlets = get_master_outlets()
    for outlet in outlets:
        if str(outlet.get("id")) == target:
            return outlet
    return None
