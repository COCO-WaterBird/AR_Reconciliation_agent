"""Shared page layout: the sidebar context panel and the page header."""

from __future__ import annotations

import streamlit as st

from app.ui import components as c
from app.ui import services, state
from app.ui.fmt import esc


def sidebar_context() -> None:
    run = st.session_state.get("run")
    llm_ok, llm_text = services.llm_status()
    ai_mode = "AI-Assisted (LLM)" if st.session_state.get("use_llm") and llm_ok else "Deterministic"
    reviewer = st.session_state.get("reviewer") or "Not set"
    lines = [
        f"<b>Workspace</b><br>Session workspace",
        f"<b>Current run</b><br>{esc(services.model().run_id) if state.has_run() else 'No run yet'}",
        f"<b>Data source</b><br>{esc(run['source']) if run else 'Not selected'}",
        f"<b>Reviewer</b><br>{esc(reviewer)}",
        f"<b>Investigation mode</b><br>{esc(ai_mode)} · LLM {esc(llm_text)}",
    ]
    st.sidebar.markdown(f'<div class="side-meta">{"<br>".join(lines)}</div>', unsafe_allow_html=True)


def workflow_statuses() -> list[tuple[str, str]]:
    """The seven workflow statuses, all derived from backend state."""
    if not state.has_run():
        return [("Data Import", "Not Started"), ("Processing", "Not Started"),
                ("Financial Controls", "Not Evaluated"), ("AI Investigation", "Not Started"),
                ("Exception Review", "Not Started"), ("Approval", "Not Started"),
                ("ERP Posting", "Not Integrated")]
    model = services.model()
    review = services.sheets().get("AI Exception Review")
    investigated = review is not None and not review.empty
    counts = model.metrics.review_counts
    awaiting = counts.get("Approved - Not Yet Applied", 0)
    approved = counts.get("Approved", 0)
    if awaiting:
        approval = f"Pending ({awaiting} to apply)"
    elif approved:
        approval = f"Completed ({approved} applied)"
    else:
        approval = "Not Started"
    return [
        ("Data Import", "Completed"),
        ("Processing", model.processing_status),
        ("Financial Controls", model.control_status),
        ("AI Investigation", f"Completed ({len(review)} receipts)" if investigated else "Not Started"),
        ("Exception Review", model.review_status),
        ("Approval", approval),
        ("ERP Posting", "Not Integrated"),
    ]


def header(title: str, description: str, show_status: bool = True) -> None:
    meta = {}
    if state.has_run():
        model = services.model()
        meta = {"As-of Date": str(model.as_of or "Not Available"), "Run ID": model.run_id,
                "Data Source": st.session_state.run["source"]}
    c.page_header(title, description, meta)
    state.show_flash()
    if show_status:
        c.status_strip(workflow_statuses())


def require_run() -> bool:
    if state.has_run():
        return True
    c.empty_state("No reconciliation has been run in this session.",
                  "Go to Data Import, choose a data source and run the reconciliation.")
    if st.button("Go to Data Import", type="primary"):
        st.switch_page("views/data_import.py")
    return False
