"""Data Import - choose one data source, validate it, preview it, run the reconciliation."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ar_reconciliation.config import AR_FILE, BANK_FILE

from app.ui import components as c
from app.ui import layout, services, state
from app.ui import fmt

layout.header("Data Import", "Select the data to reconcile, validate it and run the reconciliation engine.")

SOURCES = ["Upload My Files", "Use Synthetic Sample Data"]
source = st.radio("Data source", SOURCES, horizontal=True, key="data_source",
                  help="The two options are mutually exclusive. The run uses only the source selected here.")

checks: list[services.FileCheck] = []
uploads: dict[str, tuple[str, bytes]] = {}

with st.container(border=True):
    if source == "Upload My Files":
        st.markdown("### Source Files")
        left, right = st.columns(2)
        bank_file = left.file_uploader("Bank Statement (.xlsx, sheet 'Bank Receipts')", type=["xlsx"], key="bank_upload")
        ar_file = right.file_uploader("Open AR Details (.xlsx, sheet 'Open AR')", type=["xlsx"], key="ar_upload")
        if bank_file:
            uploads["Bank Statement"] = (bank_file.name, bank_file.getvalue())
        if ar_file:
            uploads["Open AR Details"] = (ar_file.name, ar_file.getvalue())
        for role, (name, data) in uploads.items():
            checks.append(services.check_source(role, name, data))
        if not uploads:
            st.caption("Upload both workbooks to validate and preview them. Supporting documents (contracts, "
                       "emails, remittance advice) are managed in the Evidence Center.")
    else:
        st.markdown("### Synthetic Sample Data")
        c.callout("The run will use the synthetic sample dataset that ships with the application - not your own "
                  "files. It contains no real customer data.", "warn")
        for role, path in (("Bank Statement", BANK_FILE), ("Open AR Details", AR_FILE)):
            checks.append(services.check_source(role, path.name, path.read_bytes()))

if checks:
    st.markdown("### File Validation")
    table = pd.DataFrame([{
        "Role": ch.role, "File Name": ch.name, "File Type": "Excel Workbook", "File Size (KB)": round(ch.size / 1024, 1),
        "Records": ch.records, "Validation Status": ch.status,
        "Missing Required Fields": ", ".join(ch.missing) or "None",
        "Duplicate Records": ch.duplicates,
        "Data Quality Issues": "; ".join(ch.issues) or "None",
    } for ch in checks])
    st.dataframe(table, hide_index=True, use_container_width=True, column_config={
        "Records": c.count_col("Records"), "Duplicate Records": c.count_col("Duplicate Records"),
        "Data Quality Issues": st.column_config.TextColumn(width="large"),
        "File Type": None, "Role": st.column_config.TextColumn(width="small"),
    })
    for ch in checks:
        if ch.missing:
            st.error(f"{ch.name} is missing required fields: {', '.join(ch.missing)}. Add them and upload again.")

    with st.expander("Data Preview", expanded=False):
        tabs = st.tabs([ch.role for ch in checks])
        for tab, ch in zip(tabs, checks):
            with tab:
                cols = st.columns(3)
                cols[0].metric("Records", fmt.count(ch.records))
                cols[1].metric("Customers", fmt.count(ch.customers) if ch.customers else "Not Available")
                cols[2].metric("Currencies", " · ".join(f"{k} {v}" for k, v in ch.currencies.items()) or "Not Available")
                st.dataframe(ch.preview, hide_index=True, use_container_width=True, height=260)

ready = (source == "Use Synthetic Sample Data" and all(ch.ok for ch in checks)) or (
    source == "Upload My Files" and len(uploads) == 2 and all(ch.ok for ch in checks)
)

st.write("")
with st.container(border=True):
    left, right = st.columns([3, 1])
    with left:
        st.markdown("### Run Reconciliation")
        if source == "Use Synthetic Sample Data":
            st.caption(f"Data source: Synthetic Sample Data ({BANK_FILE.name}, {AR_FILE.name}).")
        elif len(uploads) == 2:
            st.caption(f"Data source: Uploaded Files ({uploads['Bank Statement'][0]}, {uploads['Open AR Details'][0]}).")
        else:
            st.caption("Upload both workbooks to enable the run.")
        if state.has_run():
            st.caption("Running again replaces the current run's results in this session.")
    with right:
        clicked = st.button("Run Reconciliation", type="primary", disabled=not ready or st.session_state.busy,
                            use_container_width=True)
    if clicked:
        with st.status("Running the reconciliation engine...", expanded=False) as status:
            ok, message = services.run(uploads.get("Bank Statement"), uploads.get("Open AR Details"), source)
            status.update(label=message, state="complete" if ok else "error")
        if ok:
            state.flash(message + " The executive summary is below.")
            st.switch_page("views/overview.py")
        else:
            st.error(message)
