import base64
from pathlib import Path

import streamlit as st


def _data_url(path: Path) -> str:
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _css_url(data_url: str) -> str:
    return f"url('{data_url}')" if data_url else "none"


def apply_odoo_enterprise_theme():
    css_path = Path(__file__).with_name("styles.css")
    if not css_path.exists():
        return
    assets_dir = Path(__file__).with_name("assets")
    print_icon = _data_url(assets_dir / "iconPrint.png")
    submit_icon = _data_url(assets_dir / "iconSubmit.png")
    reset_icon = _data_url(assets_dir / "iconReset.png")
    icon_css = (
        "\n:root {\n"
        f"  --icon-print: {_css_url(print_icon)};\n"
        f"  --icon-submit: {_css_url(submit_icon)};\n"
        f"  --icon-reset: {_css_url(reset_icon)};\n"
        "}\n"
    )
    st.markdown(
        f"<style>{css_path.read_text(encoding='utf-8')}{icon_css}</style>",
        unsafe_allow_html=True,
    )
