"""Exception Workbench - the queue of receipts that need attention."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import components as c
from app.ui import fmt, layout, services, state

layout.header("Exception Workbench", "Investigate, review and resolve the receipts that were not fully settled.",
              show_status=False)
if not layout.require_run():
    st.stop()

model = services.model()
exceptions = model.exceptions
if exceptions.empty:
    st.success("There are no exceptions in this run.")
    st.stop()

policies = services.policies()
review = services.sheets().get("AI Exception Review", pd.DataFrame())
log = services.sheets().get("AI Exception Log", pd.DataFrame())
analysed = {}
if not review.empty:
    analysed = dict(zip(review["Bank Transaction ID"].astype(object).fillna("").astype(str), review["Analysed By"].astype(object).fillna("").astype(str)))
model_errors = set(log.loc[log["Outcome"] == "Model Error", "Bank Transaction ID"].astype(object).fillna("").astype(str)) if not log.empty else set()


engine_matched = set(exceptions.loc[exceptions["Match Status"].isin(["Fully Matched", "Partially Matched"]),
                                    "Bank Receipt ID"])


def ai_status(txn: str) -> str:
    if txn in engine_matched and txn not in analysed:
        return "Not Applicable (Engine Match)"
    if txn in model_errors:
        return "Deterministic Fallback"
    return services.ai_source(analysed.get(txn, "")).replace(" (LLM Unavailable)", "")


def validation_status(txn: str) -> str:
    policy = policies.get(txn)
    if policy is None or policy.ai_recommendation == "Not analyzed":
        return "Not Evaluated"
    if policy.execution_allowed:
        return "Eligible for Approval"
    if policy.final_decision == "Hold / Escalate":
        return "Hold / Escalate"
    if policy.blocking_reasons:
        return "Blocked by Financial Policy"
    return "No Executable Proposal"


queue = pd.DataFrame({
    "Priority": exceptions["Priority"],
    "Transaction ID": exceptions["Bank Receipt ID"],
    "Customer": exceptions["Customer Name"],
    "Currency": exceptions["Currency"],
    "Unresolved Amount": exceptions["unresolved_cents"] / 100,
    "Exception Category": exceptions["Exception Category"],
    "AI Investigation": [ai_status(t) for t in exceptions["Bank Receipt ID"]],
    "Evidence": ["Available" if r != "No supporting document provided" else "Missing"
                 for r in exceptions["Supporting Evidence Reference"]],
    "Financial Validation": [validation_status(t) for t in exceptions["Bank Receipt ID"]],
    "Review Status": exceptions["Review Status"],
    "Required Action": [policies[t].required_action if t in policies else a
                        for t, a in zip(exceptions["Bank Receipt ID"], exceptions["Recommended Action"])],
    "Receipt Date": pd.to_datetime(exceptions["Receipt Date"], errors="coerce"),
    "Receipt Amount": exceptions["receipt_cents"] / 100,
    "_invoices": exceptions["Related Invoice ID"].astype(object).fillna("").astype(str) + " " + exceptions["AI Proposed Allocation"].astype(object).fillna("").astype(str),
})

# --- Summary cards -------------------------------------------------------------------
open_q = queue[queue["Review Status"] != "Approved"]
cards = st.columns(6)
stats = [
    ("Total Exceptions", len(open_q)),
    ("Pending Investigation", int((open_q["AI Investigation"] == "Not Analyzed").sum())),
    ("Pending Review", int((open_q["Review Status"] == "Pending Review").sum())),
    ("Pending Application", int((open_q["Review Status"] == "Approved - Not Yet Applied").sum())),
    ("High Priority", int((open_q["Priority"] == "High").sum())),
    ("Resolved by Approval", int((queue["Review Status"] == "Approved").sum())),
]
for col, (label, value) in zip(cards, stats):
    with col:
        c.kpi(label, fmt.count(value))
st.write("")

# --- Filters -----------------------------------------------------------------------
saved = st.session_state.wb_filters or {}
ids_filter = saved.get("search_ids")
with st.container(border=True):
    f1 = st.columns([2.6, 1, 1.6, 1, 1.2])
    search = f1[0].text_input("Search", placeholder="Customer, transaction ID or invoice number", key="wb_search")
    currency = f1[1].selectbox("Currency", ["All"] + sorted(queue["Currency"].unique()), key="wb_currency")
    category = f1[2].selectbox("Exception Category", ["All"] + sorted(queue["Exception Category"].unique()), key="wb_category")
    priority = f1[3].selectbox("Priority", ["All", "High", "Medium", "Low"], key="wb_priority")
    sort = f1[4].selectbox("Sort By", ["Priority", "Unresolved Amount", "Receipt Date"], key="wb_sort")
    f2 = st.columns([1.2, 1.4, 1.6, 1.4, 1.4])
    evidence = f2[0].selectbox("Evidence", ["All", "Available", "Missing"], key="wb_evidence")
    review_f = f2[1].selectbox("Review Status", ["All"] + sorted(queue["Review Status"].unique()), key="wb_review")
    validation_f = f2[2].selectbox("Financial Validation", ["All"] + sorted(queue["Financial Validation"].unique()),
                                   key="wb_validation")
    ai_f = f2[3].selectbox("AI Investigation", ["All"] + sorted(queue["AI Investigation"].unique()), key="wb_ai")
    show_resolved = f2[4].toggle("Include resolved", value=False, key="wb_resolved")
    if ids_filter:
        info, clear = st.columns([5, 1])
        info.info(f"Showing {len(ids_filter)} transactions referenced by a Management Attention item.")
        if clear.button("Clear", use_container_width=True):
            st.session_state.wb_filters = {}
            st.rerun()

frame = queue if show_resolved else open_q
if ids_filter:
    frame = frame[frame["Transaction ID"].isin(ids_filter) | frame["_invoices"].str.contains("|".join(ids_filter), regex=True)]
if search:
    needle = search.strip().lower()
    frame = frame[(frame["Transaction ID"].str.lower().str.contains(needle, regex=False))
                  | (frame["Customer"].str.lower().str.contains(needle, regex=False))
                  | (frame["_invoices"].str.lower().str.contains(needle, regex=False))]
for column, value in (("Currency", currency), ("Exception Category", category), ("Priority", priority),
                      ("Evidence", evidence), ("Review Status", review_f), ("Financial Validation", validation_f),
                      ("AI Investigation", ai_f)):
    if value != "All":
        frame = frame[frame[column] == value]

if sort == "Priority":
    order = {"High": 0, "Medium": 1, "Low": 2}
    frame = frame.assign(_p=frame["Priority"].map(order)).sort_values(["_p", "Currency", "Unresolved Amount"],
                                                                     ascending=[True, True, False]).drop(columns="_p")
elif sort == "Unresolved Amount":
    # Amounts in different currencies are not comparable: rank within each currency.
    frame = frame.sort_values(["Currency", "Unresolved Amount"], ascending=[True, False])
else:
    frame = frame.sort_values("Receipt Date", ascending=False)
if sort == "Unresolved Amount" and currency == "All" and frame["Currency"].nunique() > 1:
    st.caption("Amounts are ranked within each currency; select a currency to rank a single currency.")

st.session_state.queue_order = list(frame["Transaction ID"])

# --- Queue -------------------------------------------------------------------------
display = frame.drop(columns=["_invoices"])
event = st.dataframe(display, hide_index=True, use_container_width=True, height=440, on_select="rerun",
                     selection_mode="multi-row", key="wb_table", column_config={
                         "Receipt Date": c.date_col("Receipt Date"),
                         "Receipt Amount": c.money_col("Receipt Amount"),
                         "Priority": st.column_config.TextColumn(width="small"),
                         "Currency": st.column_config.TextColumn(width="small"),
                         "Unresolved Amount": c.money_col("Unresolved Amount", width="small"),
                         "Required Action": st.column_config.TextColumn(width="large"),
                         "Customer": st.column_config.TextColumn(width="medium"),
                     })
selected = [display.iloc[i]["Transaction ID"] for i in (event.selection.rows if event and event.selection else [])]

actions = st.columns([1.3, 1.6, 1.6, 1.6, 1.4])
open_clicked = actions[0].button("Open Investigation", type="primary", disabled=len(selected) != 1,
                                 use_container_width=True, help="Select exactly one row.")
analyze_selected = actions[1].button(f"Analyze Selected ({len(selected)})", disabled=not selected or st.session_state.busy,
                                     use_container_width=True)
pending = list(open_q.loc[open_q["AI Investigation"] == "Not Analyzed", "Transaction ID"])
analyzable = set(services.sheets().get("Unmatched Bank Receipts", pd.DataFrame()).get("Bank Transaction ID", pd.Series(dtype=str)).astype(object).fillna("").astype(str))
analyze_all = actions[2].button(f"Analyze All Pending ({len(pending)})", disabled=not pending or st.session_state.busy,
                                use_container_width=True)
failed = sorted(model_errors & set(open_q["Transaction ID"]))
rerun_failed = actions[3].button(f"Re-run Failed ({len(failed)})", disabled=not failed or st.session_state.busy,
                                 use_container_width=True,
                                 help="Receipts where the LLM was unavailable and the deterministic fallback was used.")
actions[4].download_button("Export Queue (CSV)", display.to_csv(index=False).encode(),
                           file_name=f"exception_queue_{model.run_id}.csv", mime="text/csv", use_container_width=True)
st.caption("Investigation proposes resolutions only. Nothing is approved or applied until a reviewer acts on each "
           "receipt, and the financial policy can block an approval.")

if open_clicked:
    st.session_state.selected_txn = selected[0]
    st.switch_page("views/investigation.py")
for clicked, ids in ((analyze_selected, selected), (analyze_all, pending), (rerun_failed, failed)):
    if clicked:
        skipped = [t for t in ids if t not in analyzable]
        ids = [t for t in ids if t in analyzable]
        if not ids:
            state.flash("The selected receipts were matched by the engine; they need confirmation, not an AI "
                        "investigation.", "info")
            st.rerun()
        with st.spinner(f"Investigating {len(ids)} exceptions..."):
            ok, message = services.analyze(ids)
        state.flash(message, "success" if ok else "error")
        st.rerun()
