import os
import time
import uuid
from io import BytesIO
from pathlib import Path
from datetime import datetime
import xmlrpc.client

from supabase import create_client
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config import get_setting

_OUTLETS_CACHE = {"expires": 0, "data": []}
_PRODUCTS_CACHE = {}


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
        _OUTLETS_CACHE["expires"] = now + 1800
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
        _OUTLETS_CACHE["expires"] = now + 1800
        return outlets
    except Exception:
        outlets = [
            {"id": 1, "name": "Outlet Dummy A"},
            {"id": 2, "name": "Outlet Dummy B"},
            {"id": 3, "name": "Outlet Dummy C"},
        ]
        _OUTLETS_CACHE["data"] = outlets
        _OUTLETS_CACHE["expires"] = now + 1800
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
    if missing:
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
        _PRODUCTS_CACHE[cache_key] = {"expires": now + 1800, "data": products}
        return products

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
            "product.template",
            "search_read",
            [
                [
                    ["standard_price", ">", 0],
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
        products = products or [
            {
                "id": 1,
                "name": "Produk Dummy 1",
                "default_code": "PRD-001",
                "uom_name": "PCS",
                "harga": 0,
            }
        ]
        _PRODUCTS_CACHE[cache_key] = {"expires": now + 1800, "data": products}
        return products
    except Exception:
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
        _PRODUCTS_CACHE[cache_key] = {"expires": now + 1800, "data": products}
        return products


def get_supabase_client():
    url = get_setting("SUPABASE_URL")
    key = get_setting("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def get_supabase_admin_client():
    url = get_setting("SUPABASE_URL")
    service_key = get_setting("SUPABASE_SERVICE_KEY") or get_setting(
        "SUPABASE_SERVICE_ROLE_KEY"
    )
    if not url or not service_key:
        return None
    return create_client(url, service_key)


def upload_file_to_supabase(supabase, file_bytes, file_name, content_type, bucket_name):
    if not file_bytes or not file_name:
        return ""
    file_ext = os.path.splitext(file_name)[1].lower()
    file_name = f"{datetime.utcnow().strftime('%Y%m%d')}/{uuid.uuid4().hex}{file_ext}"
    supabase.storage.from_(bucket_name).upload(
        file_name,
        file_bytes,
        {"content-type": content_type or "application/octet-stream"},
    )
    public_url = supabase.storage.from_(bucket_name).get_public_url(file_name)
    return public_url


def build_line_payload(items, header):
    lines = []
    for idx, item in enumerate(items, start=1):
        line_pair_id = f"{header['no_form']}-{idx}"
        qty = float(item.get("qty") or 0)
        harga = float(item.get("harga") or 0)
        if qty <= 0:
            continue
        base = {
            "header_id": header["id"],
            "nama_item": item.get("product_name", ""),
            "kode_item": item.get("kode_item", ""),
            "uom": item.get("uom", ""),
            "qty": qty,
            "harga_cost": harga,
            "line_pair_id": line_pair_id,
        }
        lines.append(
            {
                **base,
                "movement_type": "keluar",
                "outlet_name": header["outlet_pengirim"],
            }
        )
        lines.append(
            {
                **base,
                "movement_type": "masuk",
                "outlet_name": header["outlet_penerima"],
            }
        )
    return lines


def build_mutasi_pdf(
    no_form,
    tanggal,
    outlet_pengirim,
    outlet_penerima,
    dibuat_oleh,
    disetujui_oleh,
    diterima_oleh,
    items,
    file_name=None,
    logo_path=None,
):
    def safe_text(value):
        if value is None:
            return "-"
        text = str(value).strip()
        return text if text else "-"

    def join_names(values):
        names = [name.strip() for name in (values or []) if name.strip()]
        return ", ".join(names) if names else "-"

    def format_qty(value):
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
        "<b>Form Berita Acara Mutasi</b><br/><font color='#6b6780' size='9'>"
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
    tanggal_text = safe_text(tanggal)
    info_data = [
        [Paragraph("No Form", muted_style), Paragraph(safe_text(no_form), body_style)],
        [Paragraph("Tanggal Kirim", muted_style), Paragraph(tanggal_text, body_style)],
        [
            Paragraph("Outlet Pengirim", muted_style),
            Paragraph(safe_text(outlet_pengirim), body_style),
        ],
        [
            Paragraph("Outlet Penerima", muted_style),
            Paragraph(safe_text(outlet_penerima), body_style),
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
    item_rows = [["No", "Nama Item", "Kode Item", "Satuan", "Qty"]]
    total_qty = 0.0
    row_index = 1
    for item in items or []:
        name = (item or {}).get("product_name", "")
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
                format_qty(qty),
            ]
        )
        row_index += 1

    if len(item_rows) == 1:
        item_rows.append(["-", "Belum ada item", "-", "-", "-"])

    item_rows.append(["", "", "", "Total", format_qty(total_qty)])

    item_table = Table(
        item_rows,
        colWidths=[8 * mm, 82 * mm, 30 * mm, 20 * mm, 20 * mm],
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
        [
            Paragraph("Dibuat Oleh", muted_style),
            Paragraph(join_names(dibuat_oleh), body_style),
        ],
        [
            Paragraph("Disetujui Oleh", muted_style),
            Paragraph(join_names(disetujui_oleh), body_style),
        ],
        [
            Paragraph("Diterima Oleh", muted_style),
            Paragraph(join_names(diterima_oleh), body_style),
        ],
        [
            Paragraph("Lampiran", muted_style),
            Paragraph(safe_text(file_name or "-"), body_style),
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
