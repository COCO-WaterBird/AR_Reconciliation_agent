"""Reports - the Excel reporting package and per-report CSV extracts."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import components as c
from app.ui import layout, services, state

layout.header("Reports", "Download the Excel reporting package or individual report extracts.", show_status=False)
if not layout.require_run():
    st.stop()

model = services.model()
period = str(model.as_of or "Not Available")

with st.container(border=True):
    left, right = st.columns([4, 1.3])
    with left:
        st.markdown("### Excel Reporting Package")
        st.caption("The complete workbook: Executive Dashboard, AR Aging & Customer Risk, Exception Analysis, "
                   "Action & Review Queue, Cash Application Results, Control Reconciliation, AI Decision & Evidence, "
                   "approval records, audit trail, metric definitions and run metadata.")
        c.kv([("Reporting Period (As-of)", period), ("Run ID", model.run_id), ("Generated At (UTC)", model.generated_at)])
    with right:
        st.download_button("Download Excel Package", state.result_path().read_bytes(), type="primary",
                           file_name=f"AR_Reconciliation_{model.run_id}.xlsx", use_container_width=True,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

has_due_dates = not model.aging_detail.empty or model.metrics.open_ar_by_currency == {}
reports = [
    ("Reconciliation Results", "Every receipt with cash disposition and match, review and posting status.", model.cash_results, ""),
    ("Exception Analysis", "Exceptions by category and currency (unresolved amounts per currency).", model.exception_summary, ""),
    ("Action & Review Queue", "Open items by priority, with facts and AI suggestions in separate columns.", model.queue, ""),
    ("AR Aging Analysis", "Remaining AR by aging bucket and currency as of the reporting date.", model.aging_summary,
     "" if has_due_dates else "Not available: the AR data has no usable due dates."),
    ("Customer Risk Analysis", "Customer follow-up priority (operational, not a credit rating).", model.customer_risk, ""),
    ("Financial Control Report", "Control matrix by currency.", model.controls.drop(columns=["_unit"]), ""),
    ("Audit Trail", "Balance changes from applied resolutions.", services.sheets().get("Audit Trail", pd.DataFrame()),
     "No resolutions have been applied in this run yet."),
]
for sheet, description in (("Enrichment Comparison", "Baseline (bank + AR) vs. enriched run, measured with the same definitions."),
                           ("Proposed Deductions", "Deductions stated in remittance advice, authorization and reviewer decision."),
                           ("Data Sources & Enrichment", "Inputs used in this run, their validation and row counts.")):
    if sheet in services.sheets():
        reports.append((sheet, description, services.sheets()[sheet], ""))
st.markdown("### Report Extracts")
for name, description, frame, limitation in reports:
    with st.container(border=True):
        cols = st.columns([3, 2.2, 1.3])
        cols[0].markdown(f"**{name}**  \n<span class='muted'>{description}</span>", unsafe_allow_html=True)
        cols[1].markdown(f"<span class='muted'>Period {period} · Run {model.run_id} · {len(frame):,} rows</span>",
                         unsafe_allow_html=True)
        if frame is None or frame.empty:
            cols[2].caption(limitation or "No data in this run.")
        else:
            data = frame.drop(columns=[x for x in frame.columns if str(x).startswith("_") or x == "key"], errors="ignore")
            cols[2].download_button("Download CSV", data.to_csv(index=False).encode(),
                                    file_name=f"{name.lower().replace(' ', '_').replace('&', 'and')}_{model.run_id}.csv",
                                    mime="text/csv", key=f"dl_{name}", use_container_width=True)
st.caption("Amount columns in the extracts are in integer cents where the column is a model figure "
           "(for example Exception Analysis and Aging); the Excel package shows formatted amounts.")
