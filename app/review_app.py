"""
AR Intelligence - AI-Powered AR Reconciliation & Financial Intelligence Platform.

    python -m pip install -r requirements.txt -r requirements-ui.txt
    streamlit run app/review_app.py

A multipage Streamlit application over the existing services.  The pages
only display backend results and collect user actions; every financial
calculation, rule and authorization decision is made by the backend
(`ar_reconciliation`).  Each browser session works in a private temporary
workspace; the repository's data folders are never written.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
for path in (_ROOT / "src", _ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

st.set_page_config(page_title="AR Intelligence", page_icon="◆", layout="wide", initial_sidebar_state="expanded")

from app.ui import layout, state  # noqa: E402
from app.ui.theme import apply_theme  # noqa: E402

state.init()
apply_theme()

# Widget values are normally discarded when their page is not shown.  Keeping
# them in session state preserves the reviewer, the data source and every
# filter across page navigation.
_PERSIST_PREFIXES = ("reviewer", "use_llm", "data_source", "wb_", "recon_", "ec_", "audit_")
for _key in list(st.session_state.keys()):
    if isinstance(_key, str) and _key.startswith(_PERSIST_PREFIXES) and not _key.endswith(("_table", "_upload")):
        st.session_state[_key] = st.session_state[_key]

_ASSETS = Path(__file__).parent / "assets"
st.logo(str(_ASSETS / "logo.svg"), size="large", icon_image=str(_ASSETS / "icon.svg"))

_HERE = Path(__file__).parent / "views"
pages = {
    "Workspace": [
        st.Page(str(_HERE / "overview.py"), title="Overview", icon=":material/dashboard:", default=True),
        st.Page(str(_HERE / "enrichment.py"), title="Enrichment Impact", icon=":material/insights:"),
        st.Page(str(_HERE / "data_import.py"), title="Data Import", icon=":material/upload_file:"),
        st.Page(str(_HERE / "reconciliation.py"), title="Reconciliation", icon=":material/account_balance:"),
        st.Page(str(_HERE / "workbench.py"), title="Exception Workbench", icon=":material/rule:"),
        st.Page(str(_HERE / "investigation.py"), title="Exception Investigation", icon=":material/manage_search:"),
        st.Page(str(_HERE / "evidence.py"), title="Evidence Center", icon=":material/folder_open:"),
        st.Page(str(_HERE / "controls.py"), title="Financial Controls", icon=":material/verified_user:"),
        st.Page(str(_HERE / "reports.py"), title="Reports", icon=":material/description:"),
    ],
    "Administration": [
        st.Page(str(_HERE / "audit.py"), title="Audit History", icon=":material/history:"),
        st.Page(str(_HERE / "settings.py"), title="Settings", icon=":material/settings:"),
    ],
}
navigation = st.navigation(pages)
layout.sidebar_context()
navigation.run()
