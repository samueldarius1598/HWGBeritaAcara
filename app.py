import base64
import re

import streamlit as st
from streamlit_tags import st_tags

from services import (
    add_item_row,
    build_mutasi_pdf,
    build_line_payload,
    ensure_items_state,
    get_master_outlets,
    get_master_products,
    get_supabase_client,
    remove_item_row,
    reset_form_state,
    upload_file_to_supabase,
)
from ui import apply_odoo_enterprise_theme


st.set_page_config(
    page_title="Form Berita Acara [Mutasi & Biaya Lainnya]",
    layout="wide",
    page_icon="assets/faviconHWGBeritaAcara.png",
)
apply_odoo_enterprise_theme()
title_cols = st.columns([0.7, 12], vertical_alignment="bottom", gap="small")
with title_cols[0]:
    st.image("assets/faviconHWGBeritaAcara.png", width=56)
with title_cols[1]:
    st.title("Form Berita Acara [Mutasi & Biaya Lainnya]")
st.caption("Dikelola dan diperiksa sepenuhnya oleh Cost Control Dept.")


def handle_reset_form():
    reset_form_state()
    st.session_state["show_reset_dialog"] = False


def close_print_preview():
    st.session_state["show_print_preview"] = False
    st.session_state.pop("preview_pdf_bytes", None)
    st.session_state.pop("preview_pdf_name", None)


def render_pdf_preview(pdf_data):
    if not pdf_data:
        st.warning("PDF belum tersedia untuk pratinjau.")
        return ""
    pdf_base64 = base64.b64encode(pdf_data).decode("ascii")
    preview_html = f"""
<!DOCTYPE html>
<html>
<head>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.min.js"></script>
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      padding: 0;
      background-color: #525659;
      display: flex;
      justify-content: center;
    }}
    #the-canvas {{
      border: 1px solid #111111;
      direction: ltr;
      max-width: 100%;
      height: auto;
      background: #ffffff;
    }}
    #pdf-status {{
      position: absolute;
      top: 12px;
      left: 12px;
      right: 12px;
      padding: 8px 12px;
      background: rgba(255, 255, 255, 0.9);
      color: #2f2b3a;
      font-family: "Manrope", sans-serif;
      font-size: 14px;
      border-radius: 6px;
    }}
  </style>
</head>
<body>
  <div id="pdf-status">Memuat pratinjau PDF...</div>
  <canvas id="the-canvas"></canvas>
  <script>
    const pdfjsLib = window['pdfjs-dist/build/pdf'];
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';

    const base64 = "{pdf_base64}";
    const raw = atob(base64);
    const uint8Array = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) {{
      uint8Array[i] = raw.charCodeAt(i);
    }}

    const loadingTask = pdfjsLib.getDocument({{ data: uint8Array }});
    loadingTask.promise
      .then((pdf) => pdf.getPage(1))
      .then((page) => {{
        const scale = 1.2;
        const viewport = page.getViewport({{ scale }});
        const canvas = document.getElementById('the-canvas');
        const context = canvas.getContext('2d');
        canvas.height = viewport.height;
        canvas.width = viewport.width;
        const statusEl = document.getElementById('pdf-status');
        if (statusEl) {{
          statusEl.style.display = 'none';
        }}
        return page.render({{ canvasContext: context, viewport: viewport }});
      }})
      .catch((error) => {{
        const statusEl = document.getElementById('pdf-status');
        if (statusEl) {{
          statusEl.textContent = 'Gagal memuat pratinjau PDF.';
        }}
        console.error(error);
      }});
  </script>
</body>
</html>
"""
    st.components.v1.html(preview_html, height=700, scrolling=True)
    return f"data:application/pdf;base64,{pdf_base64}"

ensure_items_state()

outlets = get_master_outlets()
outlet_names = [""] + [outlet["name"] for outlet in outlets if outlet.get("name")]
outlet_name_to_id = {
    outlet["name"]: outlet.get("id")
    for outlet in outlets
    if outlet.get("name")
}

with st.container():
    st.subheader("Informasi Umum")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        no_form = st.text_input(
            "No Form",
            key="no_form",
            help="Numerator yang tertera pada Kertas Berita Acara HWG misal 001598",
        )
    with col2:
        outlet_pengirim = st.selectbox(
            "Outlet Pengirim", outlet_names, index=0, key="outlet_pengirim"
        )
    with col3:
        outlet_penerima = st.selectbox(
            "Outlet Penerima", outlet_names, index=0, key="outlet_penerima"
        )
    with col4:
        tanggal = st.date_input("Tanggal Kirim", key="tanggal")

outlet_company_id = outlet_name_to_id.get(outlet_pengirim)
if outlet_pengirim:
    with st.spinner(
        f"Mohon menunggu sedang menarik Product yang tersedia di outlet {outlet_pengirim}"
    ):
        products = get_master_products(outlet_company_id)
else:
    products = get_master_products(outlet_company_id)
product_map = {p["name"]: p for p in products if p.get("name")}
product_names = [""] + sorted(product_map.keys())
product_name_to_index = {name: idx for idx, name in enumerate(product_names)}


def format_product_option(name):
    if not name:
        return ""
    product = product_map.get(name, {})
    code = product.get("default_code") or "-"
    uom = product.get("uom_name") or "-"
    return f"{name}\n{code} - {uom}"

st.subheader("Detail Item")

if not outlet_pengirim:
    st.info("Pilih outlet pengirim terlebih dahulu untuk memuat daftar produk.")

for idx, row in enumerate(st.session_state["items"]):
    cols = st.columns([3, 2, 2, 2, 1])
    with cols[0]:
        selected_name = st.selectbox(
            "Nama Item",
            product_names,
            key=f"item_product_{idx}",
            index=product_name_to_index.get(row.get("product_name", ""), 0),
            format_func=format_product_option,
        )
    if selected_name:
        product = product_map.get(selected_name, {})
        kode_value = product.get("default_code", "")
        uom_value = product.get("uom_name", "")
    else:
        product = {}
        kode_value = row.get("kode_item", "")
        uom_value = row.get("uom", "")
    st.session_state[f"item_kode_{idx}"] = kode_value
    st.session_state[f"item_uom_{idx}"] = uom_value
    with cols[1]:
        st.text_input(
            "Kode Item",
            key=f"item_kode_{idx}",
            disabled=True,
        )
    with cols[2]:
        st.text_input(
            "Satuan",
            key=f"item_uom_{idx}",
            disabled=True,
        )
    with cols[3]:
        qty = st.number_input(
            "Kuantiti",
            min_value=0.0,
            step=1.0,
            key=f"item_qty_{idx}",
            value=float(row.get("qty", 0.0) or 0.0),
        )
    with cols[4]:
        remove_clicked = st.button("Hapus", key=f"remove_{idx}")

    if selected_name:
        st.session_state["items"][idx] = {
            "product_name": selected_name,
            "kode_item": kode_value,
            "uom": uom_value,
            "qty": qty,
            "harga": product.get("harga", 0.0),
        }
    else:
        st.session_state["items"][idx].update(
            {"product_name": "", "kode_item": "", "uom": "", "qty": qty}
        )

    if remove_clicked:
        remove_item_row(idx)
        if hasattr(st, "rerun"):
            st.rerun()
        else:
            st.experimental_rerun()

st.button("Tambah Baris", on_click=add_item_row, key="add_row")

st.subheader("Personel & Upload")
col_a, col_b, col_c = st.columns(3)
with col_a:
    dibuat_oleh = st_tags(
        label="Dibuat Oleh",
        text="Ketik nama lalu Enter",
        value=st.session_state.get("dibuat_tags", []),
        suggestions=[],
        key="dibuat_tags",
    )
with col_b:
    disetujui_oleh = st_tags(
        label="Disetujui Oleh",
        text="Contoh: Darius, Samuel",
        value=st.session_state.get("disetujui_tags", []),
        suggestions=[],
        key="disetujui_tags",
    )
with col_c:
    diterima_oleh = st_tags(
        label="Diterima Oleh",
        text="Ketik nama lalu Enter",
        value=st.session_state.get("diterima_tags", []),
        suggestions=[],
        key="diterima_tags",
    )

file_obj = st.file_uploader(
    "Upload Gambar Form (JPG, PNG, PDF)",
    type=["jpg", "jpeg", "png", "pdf"],
    key="file_upload",
)

dibuat_list = [name.strip() for name in (dibuat_oleh or []) if name.strip()]
diterima_list = [name.strip() for name in (diterima_oleh or []) if name.strip()]

required_fields_filled = all(
    [
        no_form.strip(),
        outlet_pengirim,
        outlet_penerima,
        tanggal,
        dibuat_list,
        diterima_list,
    ]
)
items_valid = all(
    item.get("product_name") and float(item.get("qty") or 0) > 0
    for item in st.session_state["items"]
)
can_submit = required_fields_filled and items_valid and outlet_pengirim != outlet_penerima

if outlet_pengirim == outlet_penerima and outlet_pengirim:
    st.warning("Outlet pengirim dan penerima tidak boleh sama.")

safe_no_form = re.sub(r"[^A-Za-z0-9_-]+", "_", no_form.strip()) or "draft"
pdf_file_name = f"Form-Mutasi-{safe_no_form}.pdf"

def build_preview_pdf_bytes():
    return build_mutasi_pdf(
        no_form=no_form.strip(),
        tanggal=tanggal,
        outlet_pengirim=outlet_pengirim,
        outlet_penerima=outlet_penerima,
        dibuat_oleh=dibuat_list,
        disetujui_oleh=disetujui_oleh or [],
        diterima_oleh=diterima_list,
        items=st.session_state.get("items", []),
        file_obj=file_obj,
        logo_path="assets/faviconHWGBeritaAcara.png",
    )

with st.container():
    action_cols = st.columns([1, 1, 1, 6], vertical_alignment="center", gap="small")
    with action_cols[0]:
        submit_clicked = st.button(
            "Submit",
            disabled=not can_submit,
            key="action_submit",
            type="primary",
        )
    with action_cols[1]:
        print_clicked = st.button("Print", key="action_print", type="secondary")
    with action_cols[2]:
        st.button(
            "Reset",
            on_click=handle_reset_form,
            key="action_reset",
            type="tertiary",
        )

if print_clicked:
    st.session_state["preview_pdf_bytes"] = build_preview_pdf_bytes()
    st.session_state["preview_pdf_name"] = pdf_file_name
    st.session_state["show_print_preview"] = True

if submit_clicked:
    supabase = get_supabase_client()
    if not supabase:
        st.error(
            "Supabase belum dikonfigurasi. Lengkapi SUPABASE_URL dan SUPABASE_KEY di secrets."
        )
    else:
        with st.spinner("Data sedang diproses, mohon menunggu..."):
            try:
                bucket_name = st.secrets.get("SUPABASE_BUCKET", "mutasi-files")
                file_url = upload_file_to_supabase(supabase, file_obj, bucket_name)

                header_payload = {
                    "no_form": no_form.strip(),
                    "tanggal": str(tanggal),
                    "outlet_pengirim": outlet_pengirim,
                    "outlet_penerima": outlet_penerima,
                    "dibuat_oleh": ", ".join(dibuat_list),
                    "disetujui_oleh": [
                        name.strip() for name in disetujui_oleh if name.strip()
                    ],
                    "diterima_oleh": ", ".join(diterima_list),
                    "file_url": file_url,
                }
                header_resp = (
                    supabase.table("mutasi_header")
                    .insert(header_payload)
                    .execute()
                )
                header_row = header_resp.data[0]

                header_payload["id"] = header_row["id"]
                lines_payload = build_line_payload(
                    st.session_state["items"], header_payload
                )
                if lines_payload:
                    supabase.table("mutasi_lines").insert(lines_payload).execute()

                st.success(
                    "Form Mutasi No "
                    f"{no_form.strip()} dari {outlet_pengirim} ke "
                    f"{outlet_penerima}, berhasil di catat. Terimakasih."
                )
                st.session_state["show_reset_dialog"] = True
            except Exception as exc:
                st.error(f"Gagal menyimpan data: {exc}")

if st.session_state.get("show_print_preview"):
    st.session_state["show_print_preview"] = False
    preview_pdf_bytes = st.session_state.get("preview_pdf_bytes")
    preview_pdf_name = st.session_state.get("preview_pdf_name") or pdf_file_name
    if not preview_pdf_bytes:
        preview_pdf_bytes = build_preview_pdf_bytes()
        st.session_state["preview_pdf_bytes"] = preview_pdf_bytes
        st.session_state["preview_pdf_name"] = preview_pdf_name

    if hasattr(st, "dialog"):

        @st.dialog("Preview Form Mutasi")
        def show_print_preview():
            st.markdown('<span class="pdf-preview-marker"></span>', unsafe_allow_html=True)
            st.write("Pratinjau Form Mutasi")
            render_pdf_preview(preview_pdf_bytes)
            st.write("")
            action_cols = st.columns([6, 1, 1], gap="small")
            with action_cols[1]:
                st.download_button(
                    "Print",
                    data=preview_pdf_bytes or b"",
                    file_name=preview_pdf_name,
                    mime="application/pdf",
                    key="preview_download",
                    disabled=not preview_pdf_bytes,
                )
            with action_cols[2]:
                if st.button("Tutup Preview", key="preview_close"):
                    close_print_preview()
                    if hasattr(st, "rerun"):
                        st.rerun()
                    else:
                        st.experimental_rerun()

        show_print_preview()
    else:
        st.subheader("Preview Form Mutasi")
        render_pdf_preview(preview_pdf_bytes)
        st.write("")
        action_cols = st.columns([6, 1, 1], gap="small")
        with action_cols[1]:
            st.download_button(
                "Print",
                data=preview_pdf_bytes or b"",
                file_name=preview_pdf_name,
                mime="application/pdf",
                key="preview_download_fallback",
                disabled=not preview_pdf_bytes,
            )
        with action_cols[2]:
            if st.button("Tutup Preview", key="preview_close_fallback"):
                close_print_preview()
                if hasattr(st, "rerun"):
                    st.rerun()
                else:
                    st.experimental_rerun()

if st.session_state.get("show_reset_dialog"):
    if hasattr(st, "dialog"):

        @st.dialog("Input Form Lagi?")
        def show_reset_dialog():
            st.write("Input Form Lagi?")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Ya", key="dialog_yes"):
                    handle_reset_form()
                    if hasattr(st, "rerun"):
                        st.rerun()
                    else:
                        st.experimental_rerun()
            with col_no:
                if st.button("Tidak", key="dialog_no"):
                    st.session_state["show_reset_dialog"] = False
                    if hasattr(st, "rerun"):
                        st.rerun()
                    else:
                        st.experimental_rerun()

        show_reset_dialog()
    else:
        st.info("Input Form Lagi?")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Ya", key="dialog_yes_fallback"):
                handle_reset_form()
                if hasattr(st, "rerun"):
                    st.rerun()
                else:
                    st.experimental_rerun()
        with col_no:
            if st.button("Tidak", key="dialog_no_fallback"):
                st.session_state["show_reset_dialog"] = False
                if hasattr(st, "rerun"):
                    st.rerun()
                else:
                    st.experimental_rerun()
