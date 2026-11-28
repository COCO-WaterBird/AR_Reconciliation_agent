"""Overview - the executive dashboard (default page)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import charts, layout, services
from app.ui import components as c
from app.ui import fmt

layout.header("Overview", "Executive summary of the current reconciliation run: cash, exceptions and controls.")
if not layout.require_run():
    st.stop()

model = services.model()
m = model.metrics
cash = m.cash_by_currency
policies = services.policies()

# --- KPI cards ----------------------------------------------------------------
row1 = st.columns(4)
with row1[0]:
    c.kpi("Total Bank Receipts", fmt.count(m.total_receipts),
          " · ".join(f"{cur} {n}" for cur, n in model.statuses.groupby("Currency").size().items()))
with row1[1]:
    c.kpi("Matched Receipts", fmt.count(m.matched_receipts),
          f"{m.match_counts['Fully Matched']} fully · {m.match_counts['Partially Matched']} partially matched")
with row1[2]:
    c.kpi("Auto-Match Rate", fmt.pct(m.auto_match_rate),
          f"{m.auto_matched} of {m.in_scope_receipts} in-scope receipts matched by rules")
with row1[3]:
    c.kpi("Pending Review", fmt.count(m.pending_review), "Receipts awaiting a reviewer decision")
st.write("")
row2 = st.columns(4)
with row2[0]:
    c.kpi("AR Invoices with Cash Applied", fmt.count(m.invoices_with_cash),
          f"{m.invoice_applications} invoice applications" if m.invoice_applications is not None else "Distinct invoices")
with row2[1]:
    rates = [f"{r['Currency']} {fmt.pct(r['Cash Application Rate'])}" for _, r in cash.iterrows()]
    c.kpi("Cash Application Rate", rates, "Applied ÷ positive cash received, per currency")
with row2[2]:
    c.kpi("Outstanding AR Balance", fmt.amounts_by_currency(m.open_ar_by_currency),
          "After this run's cash and adjustments")
with row2[3]:
    c.kpi("Financial Controls", model.control_status,
          "Books tie in every currency" if model.control_status.startswith("Passed") else "See Financial Controls")

st.write("")
left, right = st.columns([5, 7])

# --- Reconciliation progress -----------------------------------------------------
with left:
    with st.container(border=True):
        st.markdown("### Reconciliation Status")
        st.caption("Match status is mutually exclusive: each receipt is counted once.")
        donut, legend = st.columns([1, 1])
        donut.plotly_chart(charts.match_donut(m.match_counts), use_container_width=True,
                           config={"displayModeBar": False})
        with legend:
            for status, n in m.match_counts.items():
                share = n / m.total_receipts if m.total_receipts else 0
                st.markdown(f"{c.badge(status)} &nbsp;**{n}** <span class='muted'>({share:.1%})</span>",
                            unsafe_allow_html=True)
        st.markdown("**Review status** <span class='muted'>(independent of match status)</span>",
                    unsafe_allow_html=True)
        review = {k: v for k, v in m.review_counts.items() if v}
        st.markdown(" &nbsp; ".join(f"{c.badge(k)} **{v}**" for k, v in review.items()), unsafe_allow_html=True)

# --- Cash application -------------------------------------------------------------
with right:
    with st.container(border=True):
        st.markdown("### Cash Application by Currency")
        table = pd.DataFrame({
            "Currency": cash["Currency"],
            "Cash Received": cash["Gross Receipts"] / 100,
            "Applied to AR": cash["Applied to AR"] / 100,
            "Unapplied Cash": cash["Unapplied Cash"] / 100,
            "Held for Review": cash["Held for Review"] / 100,
            "Application Rate": cash["Cash Application Rate"],
        })
        st.dataframe(table, hide_index=True, use_container_width=True, column_config={
            **{k: c.money_col(k) for k in
               ["Cash Received", "Applied to AR", "Unapplied Cash", "Held for Review"]},
            "Application Rate": c.pct_col("Application Rate"),
        })
        st.caption("Positive receipts only; refunds and reversals are listed in Financial Controls. "
                   "Amounts are never added across currencies.")

st.write("")
ex_col, ctrl_col = st.columns([7, 5])

# --- Exceptions -------------------------------------------------------------------
with ex_col:
    with st.container(border=True):
        st.markdown("### Exception Overview")
        exceptions = model.exceptions
        open_items = exceptions[exceptions["Review Status"] != "Approved"] if not exceptions.empty else exceptions
        evidence = (open_items["Supporting Evidence Reference"] != "No supporting document provided").sum() if not open_items.empty else 0
        blocked = sum(1 for p in policies.values() if p.overridden_by_policy or p.final_decision == "Hold / Escalate")
        cards = st.columns(3) + st.columns(3)
        stats = [
            ("Pending Exceptions", len(open_items)),
            ("High Priority", int((open_items["Priority"] == "High").sum()) if not open_items.empty else 0),
            ("Evidence Available", int(evidence)),
            ("Evidence Missing", int(len(open_items) - evidence)),
            ("Pending Approval", m.review_counts.get("Approved - Not Yet Applied", 0)),
            ("Blocked by Policy", blocked),
        ]
        for col, (label, value) in zip(cards, stats):
            with col:
                c.kpi(label, f"{value:,}")
        if not model.exception_summary.empty:
            counts = model.exception_summary.groupby("Exception Category")["Receipt Count"].sum().sort_values(ascending=False).head(10)
            st.plotly_chart(charts.count_bars(list(counts.index), [int(v) for v in counts.values]),
                            use_container_width=True, config={"displayModeBar": False})
            st.caption("Exception category = the engine's rule-based classification of the remittance memo.")

# --- Controls -----------------------------------------------------------------------
with ctrl_col:
    with st.container(border=True):
        st.markdown("### Financial Control Summary")
        matrix = model.controls
        names = {"Cash Control": "Cash Conservation", "Bank-to-AR Applied Cash": "Bank-to-AR Reconciliation",
                 "AR Roll-forward": "AR Roll-forward", "Duplicate Transaction Check": "Duplicate Prevention",
                 "Data Quality Control": "Data Quality Checks"}
        for control, label in names.items():
            rows = matrix[matrix["Control"] == control]
            if rows.empty:
                status = "Not Evaluated"
            elif (rows["Status"] == "FAIL").any():
                status = "FAIL"
            elif (rows["Status"] == "WARNING").any():
                status = "WARNING"
            else:
                status = "PASS"
            scope = ", ".join(rows["Currency"]) if not rows.empty else ""
            st.markdown(f"{c.badge(status)} &nbsp;**{label}** <span class='muted'>{fmt.esc(scope)}</span>",
                        unsafe_allow_html=True)
        currencies = sorted(set(matrix.loc[matrix["Currency"] != "All", "Currency"]))
        failed = sorted(set(matrix.loc[(matrix["Currency"] != "All") & (matrix["Status"] == "FAIL"), "Currency"]))
        st.markdown(f"{c.badge('FAIL' if failed else 'PASS')} &nbsp;**Currency-Level Controls** "
                    f"<span class='muted'>{len(currencies)} currencies evaluated separately</span>",
                    unsafe_allow_html=True)
        st.caption("PASS means the books tie to the cent. It does not mean every exception is resolved.")

# --- Management attention -------------------------------------------------------------
st.write("")
with st.container(border=True):
    st.markdown("### Management Attention")
    st.caption("Generated by deterministic rules from this run's data. Select an item to open its transactions "
               "in the Exception Workbench.")
    attention = model.attention
    if attention.empty:
        st.success("No items require management attention.")
    else:
        view = pd.DataFrame({
            "Priority": attention["Priority"], "Issue": attention["Issue"], "Currency": attention["Currency"],
            "Exposure": attention["Financial Exposure"].astype(float) / 100,
            "Items": attention["Items"], "Recommended Action": attention["Recommended Action"],
            "Responsible Team": attention["Suggested Responsible Team"],
        })
        event = st.dataframe(view, hide_index=True, use_container_width=True, on_select="rerun",
                             selection_mode="single-row", key="attention_table", column_config={
                                 "Exposure": c.money_col("Exposure"),
                                 "Priority": st.column_config.TextColumn(width="small"),
                                 "Currency": st.column_config.TextColumn(width="small"),
                                 "Items": c.count_col("Items", width="small"),
                                 "Issue": st.column_config.TextColumn(width="medium"),
                                 "Recommended Action": st.column_config.TextColumn(width="large"),
                             })
        rows = event.selection.rows if event and event.selection else []
        if rows:
            refs = str(attention.iloc[rows[0]]["References"])
            ids = [r.strip().split(" ")[0] for r in refs.split(",") if r.strip().startswith(("BTRX", "INV"))]
            if ids:
                st.markdown(f"Selected: **{fmt.esc(attention.iloc[rows[0]]['Issue'])}** — {len(ids)} referenced items.")
                if st.button("Open in Exception Workbench", type="primary"):
                    st.session_state.wb_filters = {"search_ids": ids}
                    st.switch_page("views/workbench.py")
            else:
                st.info("This item refers to an aggregate; open Financial Controls for details.")
