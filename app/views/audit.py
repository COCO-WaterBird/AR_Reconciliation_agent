"""Audit History - runs, investigations, decisions and balance changes recorded in this session."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import components as c
from app.ui import layout, services

layout.header("Audit History", "Events recorded for the current reconciliation workbook.", show_status=False)
c.callout("Audit records are stored in the result workbook of this session (Approval Records, Audit Trail, "
          "AI Exception Log). There is no persistent, cross-session audit database; download the Excel package to "
          "retain them.", "warn")
if not layout.require_run():
    st.stop()

sheets = services.sheets()
model = services.model()
events: list[dict] = []
for run in st.session_state.run_history:
    events.append({"Timestamp": run["ran_at"], "Run ID": model.run_id if run is st.session_state.run else "",
                   "Transaction ID": "", "Event Type": "Reconciliation Run", "Actor": "System",
                   "Decision / Outcome": f"Data source: {run['source']}", "Evidence Reference": f"{run['bank_file']}, {run['ar_file']}"})
log = sheets.get("AI Exception Log", pd.DataFrame())
for _, row in log.iterrows():
    outcome = str(row.get("Outcome", ""))
    kind = {"Validation": "Financial Policy Validation", "Model Error": "AI Investigation",
            "Evidence": "AI Investigation", "Applied": "Execution", "Rejected": "Execution",
            "Skipped": "Execution", "Adjusted": "Execution"}.get(outcome, "AI Investigation")
    events.append({"Timestamp": "", "Run ID": model.run_id, "Transaction ID": row.get("Bank Transaction ID", ""),
                   "Event Type": kind, "Actor": "System", "Decision / Outcome": f"{outcome}: {row.get('Detail', '')}",
                   "Evidence Reference": ""})
records = sheets.get("Approval Records", pd.DataFrame())
for _, row in records.iterrows():
    events.append({"Timestamp": row.get("Decision Timestamp", ""), "Run ID": model.run_id,
                   "Transaction ID": row.get("Bank Transaction ID", ""), "Event Type": "Human Review",
                   "Actor": row.get("Reviewer", ""), "Decision / Outcome": f"{row.get('Decision', '')} - {row.get('Outcome', '')}",
                   "Evidence Reference": row.get("Resolution ID", "")})
trail = sheets.get("Audit Trail", pd.DataFrame())
for _, row in trail.iterrows():
    events.append({"Timestamp": row.get("Timestamp", ""), "Run ID": model.run_id,
                   "Transaction ID": row.get("Bank Transaction ID", ""), "Event Type": "Execution",
                   "Actor": row.get("Reviewer", ""),
                   "Decision / Outcome": f"{row.get('Event', '')}: {row.get('Invoice Number', '')} open "
                                         f"{row.get('Open Before', '')} → {row.get('Open After', '')}",
                   "Evidence Reference": f"{row.get('Resolution ID', '')} {row.get('Supporting Documents', '')}"})
frame = pd.DataFrame(events)
if frame.empty:
    c.empty_state("No events recorded yet.")
    st.stop()
f = st.columns([2, 2, 2])
types = f[0].multiselect("Event Type", sorted(frame["Event Type"].unique()), key="audit_types")
txn = f[1].text_input("Transaction ID", key="audit_txn")
actor = f[2].text_input("Actor", key="audit_actor")
if types:
    frame = frame[frame["Event Type"].isin(types)]
if txn:
    frame = frame[frame["Transaction ID"].astype(object).fillna("").astype(str).str.contains(txn.strip(), case=False, regex=False)]
if actor:
    frame = frame[frame["Actor"].astype(object).fillna("").astype(str).str.contains(actor.strip(), case=False, regex=False)]
st.dataframe(frame, hide_index=True, use_container_width=True, height=520,
             column_config={"Decision / Outcome": st.column_config.TextColumn(width="large")})
st.caption("Investigation and validation events carry no individual timestamp in the current backend log.")
