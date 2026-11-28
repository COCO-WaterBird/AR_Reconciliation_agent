"""Data Import - choose one data source, validate it, preview it, run the reconciliation."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ar_reconciliation.config import AR_FILE, BANK_FILE

from app.ui import components as c
from app.ui import layout, services, state
from app.ui import fmt

layout.header("Data Import", "Select the data to reconcile, validate it and run the reconciliation engine.")

SOURCES = ["Upload My Files", "Use Synthetic Sample Data", "Use Synthetic Enriched Demo"]
source = st.radio("Data source", SOURCES, horizontal=True, key="data_source",
                  help="The two options are mutually exclusive. The run uses only the source selected here.")

checks: list[services.FileCheck] = []
uploads: dict[str, tuple[str, bytes]] = {}
structured: dict[str, tuple[str, bytes]] = {}

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
        with st.expander("Optional Structured Inputs (CSV or Excel)"):
            st.caption("Structured facts are available to the matching engine before it runs. They drive an "
                       "automatic match only when amounts tie exactly and nothing conflicts; deductions always "
                       "go to a reviewer.")
            cols = st.columns(3)
            for col, kind in zip(cols, services.STRUCTURED_KINDS):
                upload = col.file_uploader(kind, type=["csv", "xlsx"], key=f"struct_{kind}")
                if upload:
                    structured[kind] = (upload.name, upload.getvalue())
    elif source == "Use Synthetic Enriched Demo":
        st.markdown("### Synthetic Enriched Demo")
        c.callout("The sample bank and AR workbooks plus a synthetic Customer Master, Contract Schedule and "
                  "Remittance Advice. The run is also repeated without them, so the Enrichment Impact page can "
                  "compare the two. No real customer data.", "warn")
        for role, path in (("Bank Statement", BANK_FILE), ("Open AR Details", AR_FILE)):
            checks.append(services.check_source(role, path.name, path.read_bytes()))
        structured = services.enriched_demo_files()
    else:
        st.markdown("### Synthetic Sample Data")
        c.callout("The run will use the synthetic sample dataset that ships with the application - not your own "
                  "files. It contains no real customer data.", "warn")
        for role, path in (("Bank Statement", BANK_FILE), ("Open AR Details", AR_FILE)):
            checks.append(services.check_source(role, path.name, path.read_bytes()))

structured_rows = []
for kind, (name, data) in structured.items():
    source_row, issues = services.check_structured(kind, name, data)
    structured_rows.append({
        "Role": kind, "File Name": name, "File Size (KB)": round(len(data) / 1024, 1),
        "Records": source_row.get("Rows", 0), "Validation Status": source_row.get("Status", "Invalid"),
        "Missing Required Fields": "; ".join(i["Issue"] for i in issues if "Missing required" in i["Issue"]) or "None",
        "Duplicate Records": 0,
        "Data Quality Issues": "; ".join(i["Issue"] for i in issues if "Missing required" not in i["Issue"]) or "None",
    })

if checks:
    st.markdown("### File Validation")
    table = pd.DataFrame([{
        "Role": ch.role, "File Name": ch.name, "File Type": "Excel Workbook", "File Size (KB)": round(ch.size / 1024, 1),
        "Records": ch.records, "Validation Status": ch.status,
        "Missing Required Fields": ", ".join(ch.missing) or "None",
        "Duplicate Records": ch.duplicates,
        "Data Quality Issues": "; ".join(ch.issues) or "None",
    } for ch in checks] + structured_rows)
    st.dataframe(table, hide_index=True, use_container_width=True, column_config={
        "Records": c.count_col("Records"), "Duplicate Records": c.count_col("Duplicate Records"),
        "Data Quality Issues": st.column_config.TextColumn(width="large"),
        "File Type": None, "Role": st.column_config.TextColumn(width="medium"),
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

structured_ok = all(r["Validation Status"] != "Invalid" for r in structured_rows)
ready = structured_ok and (
    (source in services.SAMPLE_SOURCES and all(ch.ok for ch in checks))
    or (source == "Upload My Files" and len(uploads) == 2 and all(ch.ok for ch in checks))
)

st.write("")
with st.container(border=True):
    left, right = st.columns([3, 1])
    with left:
        st.markdown("### Run Reconciliation")
        if source == "Use Synthetic Sample Data":
            st.caption(f"Data source: Synthetic Sample Data ({BANK_FILE.name}, {AR_FILE.name}).")
        elif source == "Use Synthetic Enriched Demo":
            st.caption("Data source: Synthetic Enriched Demo (sample bank and AR plus Customer Master, Contract "
                       "Schedule and Remittance Advice).")
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
            ok, message = services.run(uploads.get("Bank Statement"), uploads.get("Open AR Details"), source,
                                       structured or None)
            status.update(label=message, state="complete" if ok else "error")
        if ok:
            if structured:
                state.flash(message + " The baseline and enriched runs are compared below.")
                st.switch_page("views/enrichment.py")
            state.flash(message + " The executive summary is below.")
            st.switch_page("views/overview.py")
        else:
            st.error(message)
