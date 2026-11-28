"""Financial Controls - control matrix and policy outcomes for controllers and auditors."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import components as c
from app.ui import fmt, layout, services

layout.header("Financial Controls", "Control results by currency and the outcome of the financial policy rules.")
if not layout.require_run():
    st.stop()

model = services.model()
matrix = model.controls
policies = services.policies()

counts = matrix["Status"].value_counts().to_dict()
cards = st.columns(5)
items = [("Overall Control Status", model.control_status), ("Controls Passed", fmt.count(counts.get("PASS", 0))),
         ("Controls Failed", fmt.count(counts.get("FAIL", 0))), ("Warnings", fmt.count(counts.get("WARNING", 0))),
         ("Currencies Evaluated", fmt.count(matrix.loc[matrix["Currency"] != "All", "Currency"].nunique()))]
for col, (label, value) in zip(cards, items):
    with col:
        c.kpi(label, value)
st.write("")

with st.container(border=True):
    st.markdown("### Control Matrix")
    rows = []
    for _, r in matrix.iterrows():
        if r["_unit"] == "amount":
            shown = [fmt.money(r[k], r["Currency"], cents=True) for k in ("Expected", "Actual", "Variance")]
        else:
            shown = [fmt.count(r[k], "Not Applicable") for k in ("Expected", "Actual", "Variance")]
            shown = [f"{v} issues" if v != "Not Applicable" else v for v in shown]
        rows.append({"Control": r["Control"], "Currency": r["Currency"], "Expected": shown[0], "Actual": shown[1],
                     "Variance": shown[2], "Status": r["Status"], "What It Proves": r["What It Proves"]})
    c.html_table(pd.DataFrame(rows), badge_cols=["Status"], right_cols=["Expected", "Actual", "Variance"],
                 widths={"Control": "17%", "Currency": "7%", "Expected": "13%", "Actual": "13%", "Variance": "11%",
                         "Status": "8%"})
    st.caption("PASS = the figures tie to the cent in that currency; a variance in one currency cannot be offset by "
               "another. A PASS does not mean every exception is resolved.")

blocked = [(t, p) for t, p in policies.items() if p.blocking_reasons]
overridden = [(t, p) for t, p in policies.items() if p.overridden_by_policy]
left, right = st.columns([6, 5])
with left:
    with st.container(border=True):
        st.markdown(f"### Transactions Blocked by Policy ({len(blocked)})")
        if blocked:
            st.dataframe(pd.DataFrame([{
                "Transaction ID": t, "Final Decision": p.final_decision,
                "Potential Allocation": fmt.money(p.proposed_allocation_cents, p.currency, cents=True)
                if p.has_proposal else "No Proposal",
                "Required Action": p.required_action,
            } for t, p in blocked]), hide_index=True, use_container_width=True, height=360,
                column_config={"Required Action": st.column_config.TextColumn(width="large"),
                               "Potential Allocation": st.column_config.TextColumn(width="small")})
        else:
            st.caption("No transaction is blocked by the financial policy.")
with right:
    with st.container(border=True):
        st.markdown(f"### AI Recommendations Overridden ({len(overridden)})")
        if overridden:
            c.html_table(pd.DataFrame([{
                "Transaction": t, "AI Proposed": ", ".join(p.candidate_invoices),
                "Proposed": fmt.money(p.proposed_allocation_cents, p.currency, cents=True),
                "Authorized": fmt.money(p.authorized_allocation_cents, p.currency, cents=True),
                "Overriding Rule": p.blocking_reasons[0].split(":")[0] if p.blocking_reasons else "",
            } for t, p in overridden]), right_cols=["Proposed", "Authorized"])
        else:
            st.caption("No AI proposal was overridden by the financial policy.")

with st.container(border=True):
    st.markdown("### Pending Authorization and Data Quality")
    p1, p2 = st.columns(2)
    awaiting = model.metrics.review_counts.get("Approved - Not Yet Applied", 0)
    p1.metric("Approved, Awaiting Application", fmt.count(awaiting))
    dq = services.sheets().get("Data Quality Checks", pd.DataFrame())
    p2.metric("Data Quality Issues", fmt.count(len(dq)))
    if not dq.empty:
        st.dataframe(dq, hide_index=True, use_container_width=True)
