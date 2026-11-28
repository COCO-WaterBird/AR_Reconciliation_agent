"""Reconciliation - every receipt with its result; select one for detail."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import components as c
from app.ui import fmt, layout, services

layout.header("Reconciliation", "Results of the deterministic matching engine for every bank receipt.", show_status=False)
if not layout.require_run():
    st.stop()

model = services.model()
results = model.cash_results.copy()
results["Remaining Amount"] = results["Unapplied Cash"] + results["Held for Review"]

VIEWS = {
    "All Transactions": lambda f: f,
    "Fully Matched": lambda f: f[f["Match Status"] == "Fully Matched"],
    "Partially Matched": lambda f: f[f["Match Status"] == "Partially Matched"],
    "Unmatched": lambda f: f[f["Match Status"].isin(["Unmatched", "Refund / Reversal"])],
    "Needs Review": lambda f: f[f["Review Status"].isin(["Pending Review", "Approved - Not Yet Applied",
                                                        "More Evidence Requested", "Rejected"])],
}
counts = {name: len(fn(results)) for name, fn in VIEWS.items()}
view = st.segmented_control("View", [f"{k} ({v})" for k, v in counts.items()], default=f"All Transactions ({counts['All Transactions']})",
                            key="recon_view", label_visibility="collapsed")
view_name = (view or "All Transactions").rsplit(" (", 1)[0]

filters = st.columns([3, 1.2, 1.4])
search = filters[0].text_input("Search", placeholder="Search by transaction ID, customer or invoice number",
                               label_visibility="collapsed", key="recon_search")
currency = filters[1].selectbox("Currency", ["All Currencies"] + sorted(results["Currency"].unique()),
                                label_visibility="collapsed", key="recon_currency")
method = filters[2].selectbox("Match Method", ["All Match Methods"] + sorted(m for m in results["Match Method"].unique() if m),
                              label_visibility="collapsed", key="recon_method")

frame = VIEWS[view_name](results)
if search:
    needle = search.strip().lower()
    frame = frame[frame.apply(lambda r: needle in " ".join(str(r[k]) for k in
                                                           ["Bank Receipt ID", "Customer Name", "Invoices Applied"]).lower(), axis=1)]
if currency != "All Currencies":
    frame = frame[frame["Currency"] == currency]
if method != "All Match Methods":
    frame = frame[frame["Match Method"] == method]

display = frame[["Bank Receipt ID", "Customer Name", "Receipt Date", "Currency", "Gross Receipt Amount", "Applied to AR",
                 "Remaining Amount", "Invoices Applied", "Match Method", "Match Status", "Review Status"]].copy()
display["Receipt Date"] = pd.to_datetime(display["Receipt Date"], errors="coerce")
display["Invoices Applied"] = display["Invoices Applied"].replace("", "None")

st.caption(f"{len(display):,} of {len(results):,} receipts. Select a row to see its detail.")
event = st.dataframe(display, hide_index=True, use_container_width=True, height=420, on_select="rerun",
                     selection_mode="single-row", key="recon_table", column_config={
                         "Receipt Date": c.date_col("Receipt Date"),
                         "Gross Receipt Amount": c.money_col("Gross Amount"),
                         "Applied to AR": c.money_col("Applied Amount"),
                         "Remaining Amount": c.money_col("Remaining Amount"),
                         "Invoices Applied": st.column_config.TextColumn("Matched Invoice(s)", width="medium"),
                         "Match Method": st.column_config.TextColumn(width="medium"),
                     })
st.download_button("Download Filtered Results (CSV)", display.to_csv(index=False).encode(),
                   file_name=f"reconciliation_{view_name.lower().replace(' ', '_')}_{model.run_id}.csv",
                   mime="text/csv")

rows = event.selection.rows if event and event.selection else []
if rows:
    txn = display.iloc[rows[0]]["Bank Receipt ID"]
    detail = services.detail(txn)
    bank = detail.bank
    cur = fmt.text(bank.get("Currency"))
    st.write("")
    with st.container(border=True):
        top = st.columns([4, 1])
        top[0].markdown(f"### {fmt.esc(txn)} — {fmt.esc(bank.get('Customer Name'))}", unsafe_allow_html=True)
        if detail.status_row is not None and detail.status_row["Match Status"] in {"Unmatched", "Partially Matched", "Refund / Reversal"} \
                or (detail.status_row is not None and detail.status_row["Review Status"] != "Not Required"):
            if top[1].button("Open Investigation", type="primary", use_container_width=True):
                st.session_state.selected_txn = txn
                st.switch_page("views/investigation.py")
        a, b, d = st.columns(3)
        with a:
            st.markdown("**Original Bank Receipt**")
            c.kv([
                ("Receipt Date", fmt.day(bank.get("Value Date"))),
                ("Customer ID", fmt.text(bank.get("ERP Customer ID"))),
                ("Gross Amount", fmt.money(bank.get("Gross Receipt Amount"), cur)),
                ("Bank Fees", fmt.money(bank.get("Bank Fees"), cur)),
                ("Net Amount", fmt.money(bank.get("Net Amount"), cur)),
                ("Remittance", fmt.text(bank.get("Memo / Remittance Detail"))),
            ])
        with b:
            st.markdown("**Matching Logic**")
            s = detail.status_row
            c.kv([
                ("Match Method", fmt.text(bank.get("Match Method"), "Not Matched")),
                ("Match Status", s["Match Status"] if s is not None else "Not Available"),
                ("Review Status", s["Review Status"] if s is not None else "Not Available"),
                ("Applied Amount", fmt.money(bank.get("AR Applied Amount Total"), cur, missing=f"{cur} 0.00")),
                ("Unapplied Cash", fmt.money(bank.get("Unapplied Amount"), cur, missing=f"{cur} 0.00")),
                ("Held for Review", fmt.money(bank.get("Held For Review Amount"), cur, missing=f"{cur} 0.00")),
                ("Posting Status", "Not Posted (ERP/GL Not Integrated)"),
            ])
        with d:
            st.markdown("**Engine Notes**")
            st.markdown(f"<div class='muted'>{fmt.esc(fmt.text(bank.get('Processing Notes')))}</div>", unsafe_allow_html=True)
        st.markdown("**Related AR Invoices (cash applied)**")
        inv = detail.applied_invoices
        if inv.empty:
            st.caption("No invoice received cash from this receipt.")
        else:
            st.dataframe(pd.DataFrame({
                "Invoice Number": inv["Invoice Number"], "Due Date": pd.to_datetime(inv["Due Date"], errors="coerce"),
                "Currency": inv["Currency"], "Open Amount": inv["Open Amount"], "Applied Amount": inv["Applied Amount"],
                "Remaining Balance": inv["Residual Amount"], "Status": inv["Suggested Recon Status"],
            }), hide_index=True, use_container_width=True, column_config={
                "Due Date": c.date_col("Due Date"), "Open Amount": c.money_col("Open Amount"),
                "Applied Amount": c.money_col("Applied Amount"), "Remaining Balance": c.money_col("Remaining Balance"),
            })
