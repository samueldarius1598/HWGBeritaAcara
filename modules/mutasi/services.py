import json
import os
import re
import uuid
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.masterdata import get_outlet_by_id


def parse_names(raw_value):
    return [name.strip() for name in (raw_value or "").split(",") if name.strip()]


def parse_items(items_json):
    if not items_json:
        return []
    try:
        data = json.loads(items_json)
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
        try:
            harga = float(item.get("harga") or 0)
        except (TypeError, ValueError):
            harga = 0.0
        items.append(
            {
                "product_name": str(item.get("product_name", "")).strip(),
                "kode_item": str(item.get("kode_item", "")).strip(),
                "uom": str(item.get("uom", "")).strip(),
                "qty": qty,
                "harga": harga,
            }
        )
    return items


def parse_date_value(raw_value, fallback):
    if not raw_value:
        return fallback
    try:
        return date.fromisoformat(str(raw_value))
    except ValueError:
        return fallback


def parse_decimal(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return 0.0


def normalize_status(value):
    text = str(value or "").strip().upper()
    return text or "SENT"


def status_meta(status):
    status_key = normalize_status(status)
    labels = {
        "DRAFT": "Draft",
        "SENT": "Terkirim",
        "RECEIVED": "Diterima",
        "PARTIAL": "Diterima Sebagian",
    }
    return {
        "key": status_key,
        "label": labels.get(status_key, status_key.title()),
        "class": f"status-{status_key.lower()}",
    }


def format_idr(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    formatted = f"{amount:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"Rp. {formatted}"


def format_qty(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(amount - int(amount)) < 1e-6:
        return str(int(amount))
    formatted = f"{amount:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def validate_form(
    no_form,
    outlet_pengirim_id,
    outlet_penerima_id,
    tanggal,
    dibuat_list,
    diterima_list,
    items,
):
    missing = []
    if not no_form:
        missing.append("No Form")
    if not outlet_pengirim_id:
        missing.append("Outlet Pengirim")
    if not outlet_penerima_id:
        missing.append("Outlet Penerima")
    if not tanggal:
        missing.append("Tanggal Kirim")
    if not dibuat_list:
        missing.append("Dibuat Oleh")
    if not diterima_list:
        missing.append("Diterima Oleh")

    if outlet_pengirim_id and outlet_pengirim_id == outlet_penerima_id:
        return False, "Outlet pengirim dan penerima tidak boleh sama."

    if outlet_pengirim_id and not get_outlet_by_id(outlet_pengirim_id):
        return False, "Outlet pengirim tidak ditemukan. Perbarui profil Anda."
    if outlet_penerima_id and not get_outlet_by_id(outlet_penerima_id):
        return False, "Outlet penerima tidak ditemukan."

    non_empty_items = [
        item
        for item in items
        if item.get("product_name") or float(item.get("qty") or 0) > 0
    ]
    if not non_empty_items:
        missing.append("Minimal 1 item")
    else:
        items_valid = all(
            item.get("product_name") and float(item.get("qty") or 0) > 0
            for item in non_empty_items
        )
        if not items_valid:
            missing.append("Lengkapi Nama Item dan Kuantiti di semua baris")

    if missing:
        return False, "Lengkapi dulu: " + ", ".join(missing)
    return True, ""


def upload_file_to_supabase(
    supabase, file_bytes, file_name, content_type, bucket_name
):
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
                format_qty_value(qty),
            ]
        )
        row_index += 1

    if len(item_rows) == 1:
        item_rows.append(["-", "Belum ada item", "-", "-", "-"])

    item_rows.append(["", "", "", "Total", format_qty_value(total_qty)])

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
