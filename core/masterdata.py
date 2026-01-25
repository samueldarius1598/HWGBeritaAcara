import time
import xmlrpc.client

from .config import get_setting
from .esb_service import EsbService

OUTLETS_CACHE_TTL = 300
PRODUCTS_CACHE_TTL = 1800

_OUTLETS_CACHE = {"expires": 0, "data": []}
_PRODUCTS_CACHE = {}
_ESB_SERVICE = None


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


def _fetch_products_from_esb():
    global _ESB_SERVICE
    if _ESB_SERVICE is None:
        _ESB_SERVICE = EsbService()
    return _ESB_SERVICE.fetch_all_products()


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


def get_master_products(company_id):
    if company_id is None:
        return []

    now = time.time()
    cache_key = str(company_id)
    cache_entry = _PRODUCTS_CACHE.get(cache_key)
    if cache_entry and now < cache_entry["expires"]:
        return cache_entry["data"]

    creds, missing = get_odoo_credentials()
    odoo_products = []
    if not missing:
        try:
            odoo_products = _fetch_products_from_odoo(creds, company_id)
        except Exception:
            odoo_products = []

    esb_products = []
    try:
        esb_products = _fetch_products_from_esb()
    except Exception:
        esb_products = []

    if not odoo_products and not esb_products:
        products = [
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
        _PRODUCTS_CACHE[cache_key] = {
            "expires": now + PRODUCTS_CACHE_TTL,
            "data": products,
        }
        return products

    def _product_key(item):
        code = str(item.get("default_code") or "").strip().lower()
        if code:
            return code
        return str(item.get("name") or "").strip().lower()

    merged = {}
    for item in odoo_products:
        key = _product_key(item)
        if key:
            merged[key] = item
    for item in esb_products:
        key = _product_key(item)
        if key and key not in merged:
            merged[key] = item

    products = list(merged.values()) or [
        {
            "id": 1,
            "name": "Produk Dummy 1",
            "default_code": "PRD-001",
            "uom_name": "PCS",
            "harga": 0,
        }
    ]
    _PRODUCTS_CACHE[cache_key] = {
        "expires": now + PRODUCTS_CACHE_TTL,
        "data": products,
    }
    return products


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
