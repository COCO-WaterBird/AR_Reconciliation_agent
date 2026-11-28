"""
Session state: the work a user has done survives page navigation.

Everything lives in ``st.session_state`` for the browser session: the
private workspace folder (result workbook and document store), the current
run's metadata, the reviewer name, the selected transaction and the
workbench filters.  Nothing here is financial state; balances and decisions
are always read back from the workbook through :mod:`app.ui.services`.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

_DEFAULTS: dict[str, Any] = {
    "run": None,               # metadata of the current reconciliation run
    "data_source": "Use Synthetic Sample Data",
    "reviewer": "",
    "use_llm": False,
    "selected_txn": None,
    "queue_order": [],         # the workbench's current ordering, for previous / next
    "wb_filters": {},
    "busy": False,
    "run_history": [],
    "flash": None,
}


def init() -> None:
    if "workspace" not in st.session_state:
        st.session_state.workspace = Path(tempfile.mkdtemp(prefix="ar_review_"))
    for key, value in _DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value if not isinstance(value, (list, dict)) else type(value)(value)


def workspace() -> Path:
    init()
    return st.session_state.workspace


def result_path() -> Path:
    return workspace() / "Reconciliation_Result.xlsx"


def evidence_dir() -> Path:
    return workspace() / "evidence"


def has_run() -> bool:
    return bool(st.session_state.get("run")) and result_path().exists()


def reset_workspace() -> None:
    shutil.rmtree(workspace(), ignore_errors=True)
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def flash(message: str, kind: str = "success") -> None:
    """A message shown once on the next page render (survives a rerun)."""
    st.session_state.flash = (kind, message)


def show_flash() -> None:
    item = st.session_state.get("flash")
    if item:
        kind, message = item
        getattr(st, kind if kind in {"success", "info", "warning", "error"} else "info")(message)
        st.session_state.flash = None
