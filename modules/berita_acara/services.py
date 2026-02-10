import json
import time
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import List

import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.config import get_setting

PURPOSES_CACHE_TTL = 300
PRODUCTS_CACHE_TTL = 600

_PURPOSES_CACHE = {"expires": 0.0, "data": []}
_PRODUCTS_CACHE = {"expires": 0.0, "data": []}


def _coerce_int(value, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_gas_config():
    gas_url = (
        get_setting("BA_GAS_URL")
        or get_setting("GAS_URL")
        or get_setting("CREDENTIALS_GAS_URL")
        or ""
    ).strip()
    api_secret = (
        get_setting("BA_GAS_SECRET")
        or get_setting("GAS_API_SECRET")
        or get_setting("CREDENTIALS_GAS_SECRET")
        or ""
    ).strip()
    gid = str(get_setting("BA_GAS_GID") or get_setting("CREDENTIALS_GID") or "")
    sheet = (get_setting("BA_GAS_SHEET") or "").strip()
    timeout = _coerce_int(get_setting("BA_GAS_TIMEOUT"), 15)
    return {
        "gas_url": gas_url,
        "api_secret": api_secret,
        "gid": gid,
        "sheet": sheet,
        "timeout": timeout,
    }


def _fetch_gas_values(
    a1_range: str,
    *,
    value_type: str = "raw",
    gid_override: str | None = None,
) -> List[List[str]]:
    config = _get_gas_config()
    if not config["gas_url"]:
        raise RuntimeError("BA_GAS_URL belum dikonfigurasi.")
    if not config["api_secret"]:
        raise RuntimeError("BA_GAS_SECRET belum dikonfigurasi.")

    params = {"key": config["api_secret"], "range": a1_range, "type": value_type}
    gid_value = gid_override if gid_override not in (None, "") else config["gid"]
    if gid_value:
        params["gid"] = str(gid_value)
    if config["sheet"]:
        params["sheet"] = config["sheet"]

    resp = requests.get(config["gas_url"], params=params, timeout=config["timeout"])
    resp.raise_for_status()
    payload = resp.json() or {}
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "Google AppScript API error.")
    return payload.get("values") or []


def _normalize_purposes(values: List[List[str]]):
    purposes = []
    for row in values or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        purpose_id = str(row[0] or "").strip()
        name = str(row[1] or "").strip()
        if not purpose_id or not name:
            continue
        purposes.append({"id": purpose_id, "name": name})
    return purposes


def _normalize_products(values: List[List[str]]):
    products = []
    for row in values or []:
        if not isinstance(row, list) or len(row) < 4:
            continue
        product_id = str(row[0] or "").strip()
        name = str(row[1] or "").strip()
        code = str(row[2] or "").strip()
        uom = str(row[3] or "").strip()
        if not product_id or not name:
            continue
        products.append({"id": product_id, "name": name, "code": code, "uom": uom})
    return products


def get_master_purposes():
    ttl = _coerce_int(get_setting("BA_PURPOSES_CACHE_TTL"), PURPOSES_CACHE_TTL)
    now = time.time()
    if _PURPOSES_CACHE["data"] and now < _PURPOSES_CACHE["expires"]:
        return _PURPOSES_CACHE["data"]
    range_name = get_setting("BA_PURPOSES_RANGE") or "F2:G"
    purpose_gid = get_setting("BA_PURPOSES_GID") or "348037279"
    values = _fetch_gas_values(range_name, value_type="raw", gid_override=purpose_gid)
    purposes = _normalize_purposes(values)
    _PURPOSES_CACHE["data"] = purposes
    _PURPOSES_CACHE["expires"] = now + max(ttl, 0)
    return purposes


def get_master_products():
    ttl = _coerce_int(get_setting("BA_PRODUCTS_CACHE_TTL"), PRODUCTS_CACHE_TTL)
    now = time.time()
    if _PRODUCTS_CACHE["data"] and now < _PRODUCTS_CACHE["expires"]:
        return _PRODUCTS_CACHE["data"]
    range_name = get_setting("BA_PRODUCTS_RANGE") or "Products!A2:D"
    values = _fetch_gas_values(range_name, value_type="raw")
    products = _normalize_products(values)
    _PRODUCTS_CACHE["data"] = products
    _PRODUCTS_CACHE["expires"] = now + max(ttl, 0)
    return products


def parse_names(raw_value: str):
    return [name.strip() for name in (raw_value or "").split(",") if name.strip()]


def parse_items(items_payload):
    if not items_payload:
        return []
    if isinstance(items_payload, list):
        data = items_payload
    else:
        try:
            data = json.loads(items_payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    items = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            qty = float(item.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        items.append(
            {
                "product_id": str(item.get("product_id") or "").strip(),
                "nama_item": str(item.get("nama_item") or "").strip(),
                "kode_item": str(item.get("kode_item") or "").strip(),
                "uom": str(item.get("uom") or "").strip(),
                "qty": qty,
                "remarks": str(item.get("remarks") or "").strip(),
            }
        )
    return items


def validate_form(
    no_form: str,
    purpose_id: str,
    purpose_name: str,
    outlet_id: str,
    dibuat_oleh: List[str],
    disetujui_oleh: List[str],
    mengetahui_oleh: List[str],
    items: List[dict],
):
    missing = []
    if not no_form:
        missing.append("No Form")
    if not purpose_id or not purpose_name:
        missing.append("Purpose")
    if not outlet_id:
        missing.append("Outlet")
    if not dibuat_oleh:
        missing.append("Dibuat Oleh")
    if not disetujui_oleh:
        missing.append("Disetujui Oleh")
    if not mengetahui_oleh:
        missing.append("Mengetahui Oleh")

    non_empty = [
        item
        for item in items
        if item.get("nama_item") or float(item.get("qty") or 0) > 0
    ]
    if not non_empty:
        missing.append("Minimal 1 item")
    else:
        valid_items = all(
            item.get("nama_item") and float(item.get("qty") or 0) > 0
            for item in non_empty
        )
        if not valid_items:
            missing.append("Lengkapi Nama Item dan Qty di semua baris")

    if missing:
        return False, "Lengkapi dulu: " + ", ".join(missing)
    return True, ""


def build_lines_payload(items: List[dict], header_id: str):
    payloads = []
    row_no = 1
    for item in items:
        qty = float(item.get("qty") or 0)
        name = str(item.get("nama_item") or "").strip()
        if not name or qty <= 0:
            continue
        payloads.append(
            {
                "header_id": header_id,
                "row_no": row_no,
                "nama_item": name,
                "kode_item": item.get("kode_item") or "",
                "uom": item.get("uom") or "",
                "qty": qty,
                "product_id": item.get("product_id") or "",
                "remarks": item.get("remarks") or "",
            }
        )
        row_no += 1
    return payloads


def generate_no_form(repo) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"BA/{today}/"
    count = repo.count_by_no_form_prefix(prefix)
    seq = f"{count + 1:03d}"
    return f"{prefix}{seq}"


def build_berita_acara_pdf(
    *,
    no_form: str,
    purpose_name: str,
    outlet_name: str,
    dibuat_oleh: List[str],
    disetujui_oleh: List[str],
    mengetahui_oleh: List[str],
    items: List[dict],
    logo_path: str | None = None,
):
    def safe_text(value):
        if value is None:
            return "-"
        text = str(value).strip()
        return text if text else "-"

    def join_names(values):
        names = [name.strip() for name in (values or []) if name.strip()]
        return ", ".join(names) if names else "-"

    def format_qty_value(value):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "-"
        if abs(num - int(num)) < 1e-6:
            return f"{int(num):,}"
        return f"{num:,.2f}"

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#2f2b3a"),
    )
    muted_style = ParagraphStyle(
        "Muted",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#6b6780"),
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading3"],
        fontSize=11,
        textColor=colors.HexColor("#2f2b3a"),
        spaceBefore=8,
        spaceAfter=4,
    )

    elements = []
    logo_flowable = ""
    if logo_path and Path(logo_path).exists():
        logo_flowable = Image(str(logo_path), width=18 * mm, height=18 * mm)
    header_text = Paragraph(
        "<b>Form Berita Acara</b><br/><font color='#6b6780' size='9'>"
        "Dikelola dan diperiksa sepenuhnya oleh Cost Control Dept."
        "</font>",
        body_style,
    )
    header_table = Table(
        [[logo_flowable, header_text]],
        colWidths=[20 * mm, 150 * mm],
        hAlign="LEFT",
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("Informasi Umum", section_style))
    info_data = [
        [Paragraph("No Form", muted_style), Paragraph(safe_text(no_form), body_style)],
        [
            Paragraph("Purpose", muted_style),
            Paragraph(safe_text(purpose_name), body_style),
        ],
        [
            Paragraph("Outlet", muted_style),
            Paragraph(safe_text(outlet_name), body_style),
        ],
    ]
    info_table = Table(info_data, colWidths=[30 * mm, 130 * mm])
    info_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f1fb")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e6def5")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6def5")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(info_table)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("Detail Item", section_style))

    # Group items by remarks value. Each group gets its own table.
    grouped = {}
    for item in items or []:
        name = (item or {}).get("nama_item", "")
        qty = float((item or {}).get("qty") or 0)
        if not name and qty <= 0:
            continue
        remark_key = str((item or {}).get("remarks") or "").strip() or "-"
        grouped.setdefault(remark_key, []).append(item)

    if not grouped:
        grouped = {"-": []}

    for remark, group_items in grouped.items():
        elements.append(
            Paragraph(f"Remarks: {safe_text(remark)}", muted_style)
        )
        item_rows = [["No", "Nama Item", "Kode Item", "Satuan", "Qty", "Remarks"]]
        total_qty = 0.0
        row_index = 1
        for item in group_items or []:
            name = (item or {}).get("nama_item", "")
            qty = float((item or {}).get("qty") or 0)
            if not name and qty <= 0:
                continue
            total_qty += qty
            item_rows.append(
                [
                    str(row_index),
                    Paragraph(safe_text(name), body_style),
                    safe_text((item or {}).get("kode_item")),
                    safe_text((item or {}).get("uom")),
                    format_qty_value(qty),
                    safe_text((item or {}).get("remarks") or remark),
                ]
            )
            row_index += 1

        if len(item_rows) == 1:
            item_rows.append(["-", "Belum ada item", "-", "-", "-", "-"])

        item_rows.append(
            ["", "", "", "Total", format_qty_value(total_qty), ""]
        )

        item_table = Table(
            item_rows,
            colWidths=[8 * mm, 62 * mm, 26 * mm, 18 * mm, 18 * mm, 26 * mm],
            hAlign="LEFT",
        )
        item_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#efe9ff")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#2f2b3a")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6def5")),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (4, 1), (4, -2), "RIGHT"),
                    ("ALIGN", (4, -1), (4, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f7f1fb")),
                    ("FONTNAME", (3, -1), (-1, -1), "Helvetica-Bold"),
                ]
            )
        )
        elements.append(item_table)
        elements.append(Spacer(1, 10))

    elements.append(Paragraph("Personel", section_style))
    personel_data = [
        [Paragraph("Dibuat Oleh", muted_style), Paragraph(join_names(dibuat_oleh), body_style)],
        [
            Paragraph("Disetujui Oleh", muted_style),
            Paragraph(join_names(disetujui_oleh), body_style),
        ],
        [
            Paragraph("Mengetahui Oleh", muted_style),
            Paragraph(join_names(mengetahui_oleh), body_style),
        ],
    ]
    personel_table = Table(personel_data, colWidths=[30 * mm, 130 * mm])
    personel_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f1fb")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e6def5")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6def5")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(personel_table)
    elements.append(Spacer(1, 6))

    printed_on = datetime.now().strftime("%d-%m-%Y %H:%M")
    elements.append(Paragraph(f"Dicetak pada: {printed_on}", muted_style))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
