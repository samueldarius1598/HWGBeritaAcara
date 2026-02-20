import json
import threading
import time
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from .config import get_setting
from .esb_service import EsbService
from .shared_cache import get_default_shared_cache

OUTLETS_CACHE_TTL = 300
PRODUCTS_CACHE_TTL = 1800

MASTER_PRODUCTS_MODE_DEFAULT = "fast"
MASTER_PRODUCTS_SOFT_TTL_DEFAULT = 3600
MASTER_PRODUCTS_STALE_TTL_DEFAULT = 86400
MASTER_PRODUCTS_ODOO_BG_TIMEOUT_MS_DEFAULT = 700
MASTER_PRODUCTS_LOCK_TTL_SEC_DEFAULT = 45
MASTER_PRODUCTS_WAIT_TIMEOUT_SEC_DEFAULT = 20.0
MASTER_PRODUCTS_STALE_WAIT_TIMEOUT_SEC_DEFAULT = 0.35
MASTER_PRODUCTS_JITTER_RATIO_DEFAULT = 0.1
ESB_PRODUCTS_SOFT_TTL_DEFAULT = 1800
ESB_PRODUCTS_STALE_TTL_DEFAULT = 86400

ESB_PRODUCTS_CACHE_KEY = "master_products:esb:v1"
ODOO_PRODUCTS_CACHE_PREFIX = "master_products:odoo:v1:"

_OUTLETS_CACHE = {"expires": 0, "data": []}
_ESB_SERVICE = None
_KEY_LOCKS = {}
_KEY_LOCKS_GUARD = threading.Lock()
_BG_TASKS = set()
_BG_TASKS_GUARD = threading.Lock()
_BG_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="master-products")


def _coerce_int(value, default):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value, default):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _setting_int(key, default):
    return _coerce_int(get_setting(key), default)


def _setting_float(key, default):
    return _coerce_float(get_setting(key), default)


def _setting_bool(key, default):
    return _coerce_bool(get_setting(key), default)


def _products_mode():
    value = str(get_setting("MASTER_PRODUCTS_MODE") or MASTER_PRODUCTS_MODE_DEFAULT)
    value = value.strip().lower()
    return value or MASTER_PRODUCTS_MODE_DEFAULT


def _master_soft_ttl():
    return max(_setting_int("MASTER_PRODUCTS_SOFT_TTL_SEC", MASTER_PRODUCTS_SOFT_TTL_DEFAULT), 0)


def _master_stale_ttl():
    stale = _setting_int("MASTER_PRODUCTS_STALE_TTL_SEC", MASTER_PRODUCTS_STALE_TTL_DEFAULT)
    return max(stale, _master_soft_ttl())


def _esb_soft_ttl():
    return max(_setting_int("ESB_LIST_SOFT_TTL_SEC", ESB_PRODUCTS_SOFT_TTL_DEFAULT), 0)


def _esb_stale_ttl():
    stale = _setting_int("ESB_LIST_STALE_TTL_SEC", ESB_PRODUCTS_STALE_TTL_DEFAULT)
    return max(stale, _esb_soft_ttl())


def _odoo_bg_timeout_sec():
    timeout_ms = _setting_int(
        "MASTER_PRODUCTS_ODOO_BG_TIMEOUT_MS",
        MASTER_PRODUCTS_ODOO_BG_TIMEOUT_MS_DEFAULT,
    )
    return max(float(timeout_ms) / 1000.0, 0.0)


def _lock_ttl_sec():
    return max(
        _setting_float("MASTER_PRODUCTS_LOCK_TTL_SEC", MASTER_PRODUCTS_LOCK_TTL_SEC_DEFAULT),
        1.0,
    )


def _wait_timeout_sec():
    return max(
        _setting_float(
            "MASTER_PRODUCTS_WAIT_TIMEOUT_SEC", MASTER_PRODUCTS_WAIT_TIMEOUT_SEC_DEFAULT
        ),
        0.1,
    )


def _stale_wait_timeout_sec():
    return max(
        _setting_float(
            "MASTER_PRODUCTS_STALE_WAIT_TIMEOUT_SEC",
            MASTER_PRODUCTS_STALE_WAIT_TIMEOUT_SEC_DEFAULT,
        ),
        0.0,
    )


def _jitter_ratio():
    return max(
        _setting_float("MASTER_PRODUCTS_TTL_JITTER_RATIO", MASTER_PRODUCTS_JITTER_RATIO_DEFAULT),
        0.0,
    )


def _cache_lock_key(cache_key):
    return f"lock:{cache_key}"


def _get_local_key_lock(cache_key):
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[cache_key] = lock
        return lock


def _submit_background_task(task_key, func, *args, **kwargs):
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


def _dummy_products():
    return [
        {
            "id": 1,
            "name": "Produk Dummy 1",
            "default_code": "PRD-001",
            "uom_name": "PCS",
            "harga": 0,
        },
        {
            "id": 2,
            "name": "Produk Dummy 2",
            "default_code": "PRD-002",
            "uom_name": "PCS",
            "harga": 0,
        },
    ]


def _product_key(item):
    code = str(item.get("default_code") or "").strip().lower()
    if code:
        return code
    return str(item.get("name") or "").strip().lower()


def _merge_products(odoo_products, esb_products):
    merged = {}
    for item in odoo_products or []:
        key = _product_key(item)
        if key:
            merged[key] = item
    for item in esb_products or []:
        key = _product_key(item)
        if key and key not in merged:
            merged[key] = item
    return list(merged.values())


def _log_master_products_event(**fields):
    payload = {"event": "get_master_products", **fields}
    print(
        "[MasterProducts] "
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    )


def get_odoo_credentials():
    required = ["ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"]
    missing = [key for key in required if not get_setting(key)]
    if missing:
        return None, missing
    return {
        "url": get_setting("ODOO_URL").rstrip("/"),
        "db": get_setting("ODOO_DB"),
        "username": get_setting("ODOO_USERNAME"),
        "password": get_setting("ODOO_PASSWORD"),
    }, []


def _fetch_products_from_odoo(creds, company_id):
    common = xmlrpc.client.ServerProxy(f"{creds['url']}/xmlrpc/2/common")
    uid = common.authenticate(creds["db"], creds["username"], creds["password"], {})
    if not uid:
        raise RuntimeError("Autentikasi Odoo gagal.")
    models = xmlrpc.client.ServerProxy(f"{creds['url']}/xmlrpc/2/object")
    data = models.execute_kw(
        creds["db"],
        uid,
        creds["password"],
        "product.template",
        "search_read",
        [
            [
                ["standard_price", ">", 0],
                ["qty_available", "!=", 0],
            ]
        ],
        {
            "fields": ["name", "default_code", "uom_id", "standard_price"],
            "context": {
                "company_id": company_id,
                "allowed_company_ids": [company_id],
            },
        },
    )
    products = []
    for row in data:
        uom_name = ""
        if row.get("uom_id") and isinstance(row["uom_id"], list):
            uom_name = row["uom_id"][1]
        products.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "default_code": row.get("default_code", ""),
                "uom_name": uom_name,
                "harga": float(row.get("standard_price") or 0),
            }
        )
    return products


def _fetch_products_from_esb(*, allow_stale=True, force_refresh=False):
    global _ESB_SERVICE
    if _ESB_SERVICE is None:
        _ESB_SERVICE = EsbService()
    return _ESB_SERVICE.fetch_all_products(
        allow_stale=allow_stale,
        force_refresh=force_refresh,
    )


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
        uid = common.authenticate(
            creds["db"], creds["username"], creds["password"], {}
        )
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
        outlets = outlets or [
            {"id": 1, "name": "Outlet Dummy A"},
            {"id": 2, "name": "Outlet Dummy B"},
        ]
        _OUTLETS_CACHE["data"] = outlets
        _OUTLETS_CACHE["expires"] = now + OUTLETS_CACHE_TTL
        return outlets
    except Exception:
        outlets = [
            {"id": 1, "name": "Outlet Dummy A"},
            {"id": 2, "name": "Outlet Dummy B"},
            {"id": 3, "name": "Outlet Dummy C"},
        ]
        _OUTLETS_CACHE["data"] = outlets
        _OUTLETS_CACHE["expires"] = now + OUTLETS_CACHE_TTL
        return outlets


def _refresh_cache_sync(
    cache_key,
    fetcher,
    *,
    soft_ttl,
    stale_ttl,
    allow_stale_fallback=True,
    allow_empty_write=True,
):
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
            data = fetcher() or []
            if data or allow_empty_write:
                shared.set_cache_entry(
                    cache_key,
                    data,
                    soft_ttl=soft_ttl,
                    stale_ttl=stale_ttl,
                    jitter_ratio=_jitter_ratio(),
                )
                return data
            if current["state"] in {"fresh", "stale"} and current.get("data") is not None:
                return current.get("data") or []
            return []

        deadline = time.time() + _wait_timeout_sec()
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
    cache_key,
    fetcher,
    *,
    soft_ttl,
    stale_ttl,
    allow_empty_write=True,
):
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
        data = fetcher() or []
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


def _refresh_esb_products_sync():
    return _refresh_cache_sync(
        ESB_PRODUCTS_CACHE_KEY,
        lambda: _fetch_products_from_esb(allow_stale=False, force_refresh=True),
        soft_ttl=_esb_soft_ttl(),
        stale_ttl=_esb_stale_ttl(),
        allow_stale_fallback=True,
        allow_empty_write=False,
    )


def _refresh_esb_products_background():
    return _refresh_cache_background(
        ESB_PRODUCTS_CACHE_KEY,
        lambda: _fetch_products_from_esb(allow_stale=False, force_refresh=True),
        soft_ttl=_esb_soft_ttl(),
        stale_ttl=_esb_stale_ttl(),
        allow_empty_write=False,
    )


def _odoo_cache_key(company_id):
    return f"{ODOO_PRODUCTS_CACHE_PREFIX}{company_id}"


def _fetch_odoo_products_safe(company_id):
    creds, missing = get_odoo_credentials()
    if missing:
        return []
    try:
        return _fetch_products_from_odoo(creds, company_id)
    except Exception as exc:
        print(f"[MasterProducts Warning] Odoo fetch gagal untuk company {company_id}: {exc}")
        return []


def _refresh_odoo_products_sync(company_id):
    return _refresh_cache_sync(
        _odoo_cache_key(company_id),
        lambda: _fetch_odoo_products_safe(company_id),
        soft_ttl=_master_soft_ttl(),
        stale_ttl=_master_stale_ttl(),
        allow_stale_fallback=True,
    )


def _refresh_odoo_products_background(company_id):
    return _refresh_cache_background(
        _odoo_cache_key(company_id),
        lambda: _fetch_odoo_products_safe(company_id),
        soft_ttl=_master_soft_ttl(),
        stale_ttl=_master_stale_ttl(),
    )


def _schedule_esb_refresh():
    return _submit_background_task("refresh:esb_products", _refresh_esb_products_background)


def _schedule_odoo_refresh(company_id):
    return _submit_background_task(
        f"refresh:odoo_products:{company_id}",
        _refresh_odoo_products_background,
        company_id,
    )


def _resolve_esb_products():
    shared = get_default_shared_cache()
    entry = shared.get_cache_entry(ESB_PRODUCTS_CACHE_KEY, allow_stale=True)
    state = entry["state"]
    if state == "fresh":
        return entry.get("data") or [], "fresh"

    if state == "stale" and entry.get("data") is not None:
        _schedule_esb_refresh()
        return entry.get("data") or [], "stale"

    data = _refresh_esb_products_sync()
    if data:
        return data, "miss"

    fallback = shared.get_cache_entry(ESB_PRODUCTS_CACHE_KEY, allow_stale=True)
    if fallback["state"] in {"fresh", "stale"} and fallback.get("data") is not None:
        if fallback["state"] == "stale":
            _schedule_esb_refresh()
        return fallback.get("data") or [], fallback["state"]

    return [], "miss"


def _resolve_odoo_overlay(company_id, mode):
    shared = get_default_shared_cache()
    cache_key = _odoo_cache_key(company_id)
    entry = shared.get_cache_entry(cache_key, allow_stale=True)
    state = entry["state"]
    data = entry.get("data") or []

    if mode == "fast":
        if state == "fresh":
            return data, state
        _schedule_odoo_refresh(company_id)
        return [], state

    if state == "fresh":
        return data, state

    refreshed = _refresh_odoo_products_sync(company_id)
    if refreshed:
        return refreshed, "miss"
    fallback = shared.get_cache_entry(cache_key, allow_stale=True)
    return fallback.get("data") or [], fallback["state"]


def _resolve_odoo_when_esb_missing(company_id, current_state, current_data):
    if current_data:
        return current_data, current_state
    timeout_sec = _odoo_bg_timeout_sec()
    if timeout_sec <= 0:
        return [], current_state
    future = _BG_EXECUTOR.submit(_refresh_odoo_products_sync, company_id)
    try:
        data = future.result(timeout=timeout_sec)
        if data:
            return data, "miss"
    except TimeoutError:
        pass
    except Exception:
        pass
    return [], current_state


def get_master_products_result(company_id):
    total_start = time.perf_counter()
    if company_id is None:
        return {
            "data": [],
            "cache_state": "miss",
            "completeness": "esb_only",
            "mode": _products_mode(),
        }

    mode = _products_mode()
    esb_ms = 0.0
    odoo_ms = 0.0
    merge_ms = 0.0

    esb_start = time.perf_counter()
    esb_products, esb_cache_state = _resolve_esb_products()
    esb_ms = (time.perf_counter() - esb_start) * 1000.0

    odoo_start = time.perf_counter()
    odoo_products, odoo_cache_state = _resolve_odoo_overlay(company_id, mode)
    odoo_ms = (time.perf_counter() - odoo_start) * 1000.0

    completeness = "esb_only"
    cache_state = esb_cache_state

    if not esb_products:
        odoo_fallback_start = time.perf_counter()
        odoo_products, odoo_cache_state = _resolve_odoo_when_esb_missing(
            company_id, odoo_cache_state, odoo_products
        )
        odoo_ms += (time.perf_counter() - odoo_fallback_start) * 1000.0
        if odoo_products:
            total_ms = (time.perf_counter() - total_start) * 1000.0
            _log_master_products_event(
                mode=mode,
                company_id=str(company_id),
                cache_state=odoo_cache_state,
                completeness="merged",
                esb_fetch_ms=round(esb_ms, 2),
                odoo_fetch_ms=round(odoo_ms, 2),
                merge_ms=0.0,
                total_ms=round(total_ms, 2),
                esb_count=0,
                odoo_count=len(odoo_products),
            )
            return {
                "data": odoo_products,
                "cache_state": odoo_cache_state,
                "completeness": "merged",
                "mode": mode,
            }

        dummy = _dummy_products()
        total_ms = (time.perf_counter() - total_start) * 1000.0
        _log_master_products_event(
            mode=mode,
            company_id=str(company_id),
            cache_state="miss",
            completeness="esb_only",
            esb_fetch_ms=round(esb_ms, 2),
            odoo_fetch_ms=round(odoo_ms, 2),
            merge_ms=0.0,
            total_ms=round(total_ms, 2),
            esb_count=0,
            odoo_count=0,
        )
        return {
            "data": dummy,
            "cache_state": "miss",
            "completeness": "esb_only",
            "mode": mode,
        }

    merge_start = time.perf_counter()
    merged = _merge_products(odoo_products, esb_products)
    merge_ms = (time.perf_counter() - merge_start) * 1000.0
    if odoo_products:
        completeness = "merged"

    products = merged or esb_products
    total_ms = (time.perf_counter() - total_start) * 1000.0
    _log_master_products_event(
        mode=mode,
        company_id=str(company_id),
        cache_state=cache_state,
        completeness=completeness,
        esb_fetch_ms=round(esb_ms, 2),
        odoo_fetch_ms=round(odoo_ms, 2),
        merge_ms=round(merge_ms, 2),
        total_ms=round(total_ms, 2),
        esb_count=len(esb_products),
        odoo_count=len(odoo_products),
        odoo_cache_state=odoo_cache_state,
    )
    return {
        "data": products,
        "cache_state": cache_state,
        "completeness": completeness,
        "mode": mode,
    }


def get_master_products(company_id):
    return get_master_products_result(company_id)["data"]


def prewarm_master_products_async():
    if not _setting_bool("MASTER_PRODUCTS_PREWARM_ON_STARTUP", True):
        return

    def _prewarm():
        entry = get_default_shared_cache().get_cache_entry(
            ESB_PRODUCTS_CACHE_KEY, allow_stale=True
        )
        if entry["state"] == "fresh":
            return
        _refresh_esb_products_background()

    _submit_background_task("prewarm:master_products:esb", _prewarm)


def normalize_outlet_id(outlet_id):
    value = str(outlet_id or "").strip()
    if not value:
        return ""
    return int(value) if value.isdigit() else value


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
