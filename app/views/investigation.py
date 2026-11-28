"""Exception Investigation - one receipt: facts, evidence, AI analysis, policy decision, review."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import components as c
from app.ui import fmt, layout, services, state

layout.header("Exception Investigation", "Investigate one receipt, review the evidence and record a decision.",
              show_status=False)
if not layout.require_run():
    st.stop()

model = services.model()
order = st.session_state.queue_order or list(model.exceptions["Bank Receipt ID"]) if not model.exceptions.empty else []
requested = st.query_params.get("txn")
if requested and requested in set(model.cash_results["Bank Receipt ID"]):
    st.session_state.selected_txn = requested
    del st.query_params["txn"]
txn = st.session_state.selected_txn
if not txn:
    if not order:
        st.success("There are no exceptions to investigate.")
        st.stop()
    txn = order[0]
    st.session_state.selected_txn = txn

# --- Navigation ----------------------------------------------------------------------------
nav = st.columns([1.35, 1.15, 1.15, 4.4])
if nav[0].button("← Exception Queue", use_container_width=True):
    st.switch_page("views/workbench.py")
position = order.index(txn) if txn in order else -1
if nav[1].button("Previous Exception", disabled=position <= 0, use_container_width=True):
    st.session_state.selected_txn = order[position - 1]
    st.rerun()
if nav[2].button("Next Exception", disabled=position < 0 or position >= len(order) - 1, use_container_width=True):
    st.session_state.selected_txn = order[position + 1]
    st.rerun()
with nav[3]:
    jump_cols = st.columns([2.2, 1.6])
    choices = order if txn in order else [txn] + order
    picked = jump_cols[0].selectbox("Go to transaction", choices, index=choices.index(txn),
                                    label_visibility="collapsed", key=f"jump_{txn}")
    if picked != txn:
        st.session_state.selected_txn = picked
        st.rerun()
    if position >= 0:
        jump_cols[1].markdown(f"<div class='muted' style='padding-top:8px'>Exception {position + 1} of {len(order)} "
                              f"in the queue view</div>", unsafe_allow_html=True)

detail = services.detail(txn)
bank, review, policy, ctx = detail.bank, detail.review, detail.policy, detail.context
cur = fmt.text(bank.get("Currency"))
status = detail.status_row
exc = model.exceptions
exc_row = exc.loc[exc["Bank Receipt ID"] == txn].iloc[0] if not exc.empty and (exc["Bank Receipt ID"] == txn).any() else None

# --- Transaction header ----------------------------------------------------------------------
with st.container(border=True):
    h = st.columns([2.2, 1.2, 1.2, 1.2, 1.2])
    h[0].markdown(f"<div class='muted'>TRANSACTION</div><div style='font-size:1.35rem;font-weight:720'>{fmt.esc(txn)}</div>"
                  f"<div>{fmt.esc(bank.get('Customer Name'))} · {fmt.esc(fmt.text(bank.get('ERP Customer ID'), 'No Customer ID'))}</div>",
                  unsafe_allow_html=True)
    h[1].markdown(f"<div class='muted'>RECEIPT AMOUNT</div><div style='font-size:1.2rem;font-weight:700'>"
                  f"{fmt.esc(fmt.money(bank.get('Gross Receipt Amount'), cur))}</div>"
                  f"<div class='muted'>{fmt.esc(fmt.day(bank.get('Value Date')))}</div>", unsafe_allow_html=True)
    h[2].markdown(f"<div class='muted'>EXCEPTION TYPE</div><div style='font-weight:650'>"
                  f"{fmt.esc(exc_row['Exception Category'] if exc_row is not None else fmt.text(bank.get('Memo Scenario')))}</div>",
                  unsafe_allow_html=True)
    h[3].markdown(f"<div class='muted'>PRIORITY</div>{c.badge(exc_row['Priority'] if exc_row is not None else 'Not Available')}",
                  unsafe_allow_html=True)
    h[4].markdown(f"<div class='muted'>REVIEW STATUS</div>{c.badge(status['Review Status'] if status is not None else 'Not Available')}",
                  unsafe_allow_html=True)

# --- Bank transaction and candidate invoices ---------------------------------------------------
left, right = st.columns([4, 7])
with left:
    with st.container(border=True):
        st.markdown("### Bank Transaction")
        c.kv([
            ("Receipt ID", txn),
            ("Customer", fmt.text(bank.get("Customer Name"))),
            ("Remitter", fmt.text(bank.get("Remitter Name"))),
            ("Receipt Date", fmt.day(bank.get("Value Date"))),
            ("Currency", cur),
            ("Gross Amount", fmt.money(bank.get("Gross Receipt Amount"), cur)),
            ("Bank Fees", fmt.money(bank.get("Bank Fees"), cur)),
            ("Net Amount", fmt.money(bank.get("Net Amount"), cur)),
            ("Payment Reference", fmt.text(bank.get("Bank Reference"))),
            ("Invoices in Memo", fmt.text(bank.get("Extracted Invoice Numbers"), "None")),
        ])
        st.write("")
        st.markdown("**Remittance Details**")
        st.markdown(f"<div class='muted'>{fmt.esc(fmt.text(bank.get('Memo / Remittance Detail'), 'No remittance text'))}</div>",
                    unsafe_allow_html=True)
        reason = detail.unmatched.get("Reason") if detail.unmatched is not None else bank.get("Processing Notes")
        st.markdown("**Engine Result**")
        st.markdown(f"<div class='muted'>{fmt.esc(fmt.text(reason))}</div>", unsafe_allow_html=True)

with right:
    with st.container(border=True):
        st.markdown("### Candidate Invoices")
        st.caption("Open AR of the receipt's customer and currency, ranked by relevance (backend candidate pool).")
        memo_refs = {v.strip().upper() for v in fmt.text(bank.get("Extracted Invoice Numbers"), "").split(",") if v.strip()}
        rows = []
        for cand in ctx.candidates:
            flags = []
            if cand.dispute_reason:
                flags.append(cand.dispute_reason)
            if cand.ar_status and cand.ar_status.lower() not in {"open", ""}:
                flags.append(cand.ar_status)
            if cand.currency.upper() != cur.upper():
                flags.append("Currency mismatch")
            if cand.third_party:
                flags.append("Third party (documented)")
            reasons = []
            if cand.invoice_number.upper() in memo_refs:
                reasons.append("Named in memo")
            if cand.residual_cents == 0:
                reasons.append("Exact amount")
            rows.append({
                "Invoice": cand.invoice_number,
                "Eligibility": "Restricted" if flags else "Eligible",
                "Restriction": "; ".join(flags) or "None",
                "Due Date": fmt.day(cand.due_date or None),
                "Open Balance": fmt.money(cand.open_amount_cents, cand.currency, cents=True),
                "Match Reason": ", ".join(reasons) or f"Difference {fmt.money(cand.residual_cents, cand.currency, cents=True)}",
            })
        if rows:
            c.html_table(pd.DataFrame(rows), badge_cols=["Eligibility"], right_cols=["Open Balance"],
                         widths={"Invoice": "15%", "Eligibility": "13%", "Due Date": "13%", "Open Balance": "17%"})
        else:
            st.info("No open AR exists for this receipt's customer and currency." if fmt.text(bank.get("ERP Customer ID"), "")
                    else "The receipt has no ERP Customer ID, so no candidate pool can be built.")
        with st.expander("Expand All Open Invoices for this Customer"):
            ar = detail.customer_open_ar
            if ar.empty:
                st.caption("No AR records for this customer ID.")
            else:
                st.dataframe(pd.DataFrame({
                    "Invoice Number": ar["Invoice Number"], "Due Date": pd.to_datetime(ar["Due Date"], errors="coerce"),
                    "Currency": ar["Currency"], "Open Amount": ar["Open Amount"],
                    "Applied Amount": ar["Applied Amount"].fillna(0), "AR Status": ar["AR Status"],
                    "Dispute / Hold": ar["Dispute Reason"].fillna(""),
                }), hide_index=True, use_container_width=True, column_config={
                    "Due Date": c.date_col("Due Date"), "Open Amount": c.money_col("Open Amount"),
                    "Applied Amount": c.money_col("Applied Amount"),
                })

# --- Evidence / AI / Validation / Review in tabs -----------------------------------------------
tab_ai, tab_evidence, tab_policy, tab_review = st.tabs(
    ["AI Investigation", "Supporting Evidence", "Financial Validation & Decision", "Human Review"])

with tab_evidence:
    ev = detail.evidence
    if not ev.documents:
        reason = ("No documents have been uploaded in this session." if not services.store().documents()
                  else "Documents exist in the Evidence Center, but none is linked to this receipt.")
        st.info(reason + " Link or upload documents below.")
    else:
        cited = fmt.text(review.get("Evidence Citations"), "") if review is not None else ""
        rows = []
        for d in ev.documents:
            facts = [f for f in ev.facts if f.doc_id == d.doc_id]
            unreadable = any(d.doc_id in u for u in ev.unreadable)
            stage = ("Used in Investigation" if d.doc_id in cited else
                     "Unreadable" if unreadable else
                     "Retrieved" if review is not None and d.doc_id in fmt.text(review.get("Supporting Documents"), "") else "Linked")
            rows.append({"Document": d.filename, "Document ID": d.doc_id,
                         "Type": {"pdf": "PDF", "email": "Email", "text": "Text", "spreadsheet": "Spreadsheet"}.get(d.kind, d.kind),
                         "Facts Extracted": len(facts), "Evidence Status": stage,
                         "Integrity": "Verified" if services.store().verify(d.doc_id) else "Hash Mismatch"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        for warning in ev.warnings:
            c.callout(warning, "block")
        for u in ev.unreadable:
            c.callout(f"Could not be read: {u}", "warn")
        if ev.facts:
            st.markdown("**Extracted Evidence**")
            st.dataframe(pd.DataFrame([{
                "Document ID": f.doc_id, "Location": f.location, "Fact": f.field.replace("_", " ").title(),
                "Value": f.value, "Related Invoice": f.invoice or "Not Stated",
                "Amount": fmt.money(f.amount_cents, f.currency or cur, cents=True, missing="Not Stated")
                if f.amount_cents is not None else "Not Stated",
                "Quote": f.quote,
            } for f in ev.facts]), hide_index=True, use_container_width=True, column_config={
                "Quote": st.column_config.TextColumn(width="large"),
            })
        for conflict in ev.conflicts:
            c.callout("Conflict: " + conflict, "warn")
    with st.expander("Link or Upload Documents for this Receipt"):
        files = st.file_uploader("Contracts, emails, remittance advice", accept_multiple_files=True,
                                 type=["pdf", "eml", "txt", "xlsx", "csv"], key=f"inv_docs_{txn}")
        existing = {d.doc_id: d.filename for d in services.store().documents() if d.doc_id not in {x.doc_id for x in ev.documents}}
        link = st.multiselect("Link documents already in the Evidence Center", list(existing),
                              format_func=lambda k: f"{existing[k]} ({k})", key=f"inv_link_{txn}")
        if st.button("Save Links", disabled=not files and not link):
            if files:
                for name, outcome, info in services.add_documents([(f.name, f.getvalue()) for f in files], [txn]):
                    (st.success if outcome != "Rejected" else st.error)(f"{name}: {outcome} — {info}")
            for doc_id in link:
                services.store().link(doc_id, [txn])
            state.flash("Documents linked. Run the investigation again to use them.", "info")
            st.rerun()

with tab_ai:
    source = services.ai_source(review.get("Analysed By") if review is not None else "")
    if any(detail.log["Outcome"] == "Model Error") if not detail.log.empty else False:
        source = "Deterministic Fallback (LLM Unavailable)"
    top = st.columns([3, 1.2])
    top[0].markdown(f"Analysis source: {c.badge(source)}", unsafe_allow_html=True)
    if top[1].button("Run Investigation" if review is None else "Re-run Investigation", type="primary",
                     disabled=st.session_state.busy, use_container_width=True):
        with st.spinner("Investigating..."):
            ok, message = services.analyze([txn])
        state.flash(message, "success" if ok else "error")
        st.rerun()
    if review is None:
        st.info("This receipt has not been investigated yet.")
    else:
        if source.startswith("Deterministic"):
            st.caption("This analysis was produced by deterministic rules, not by a language model."
                       + (" Supporting documents were read by the deterministic extractor." if detail.evidence.documents else ""))
        a, b = st.columns(2)
        with a:
            c.labeled("Confirmed Facts", "fact")
            facts = [f"Receipt of {fmt.money(bank.get('Gross Receipt Amount'), cur)} on {fmt.day(bank.get('Value Date'))}.",
                     f"Engine classification: {fmt.text(bank.get('Memo Scenario'))}.",
                     f"{len(ctx.candidates)} open invoice(s) for this customer and currency."]
            if fmt.text(bank.get("Extracted Invoice Numbers"), ""):
                facts.append(f"Memo names {bank.get('Extracted Invoice Numbers')}.")
            st.markdown("\n".join(f"- {fmt.esc(x)}" for x in facts), unsafe_allow_html=True)
            c.labeled("Evidence-Supported Findings", "fact")
            citations = fmt.text(review.get("Evidence Citations"), "")
            if citations:
                st.markdown("\n".join(f"- {fmt.esc(line)}" for line in citations.split("\n")), unsafe_allow_html=True)
            else:
                st.caption("No verified document citation supports this analysis.")
        with b:
            c.labeled("AI Assessment", "ai")
            c.kv([
                ("Exception Category", fmt.text(review.get("AI Exception Type"))),
                ("Root Cause", fmt.text(review.get("Root Cause"))),
                ("Confidence", fmt.text(review.get("AI Confidence"))),
                ("Recommended Action", fmt.text(review.get("Recommended Action"))),
                ("Evidence Sufficiency", fmt.text(review.get("Evidence Sufficiency"), "Not Evaluated")),
            ])
            st.markdown("**AI Inference**")
            st.markdown(f"<div class='muted'>{fmt.esc(fmt.text(review.get('AI Explanation')))}</div>", unsafe_allow_html=True)
        st.markdown("**Candidate Resolution**")
        c.kv([
            ("Proposed Allocations", fmt.text(review.get("Proposed Allocations"), "")
             or (f"{review.get('Proposed Invoice Numbers')}: {fmt.money(review.get('Proposed Applied Amount'), cur)}"
                 if fmt.text(review.get("Proposed Invoice Numbers"), "") else "None")),
            ("Proposed Adjustments", fmt.text(review.get("Proposed Adjustments"), "None")),
            ("Accounting Treatment", fmt.text(review.get("Accounting Treatment"))),
        ])
        notes = fmt.text(review.get("Validation Notes"), "")
        legacy = [n for n in detail.log.loc[detail.log["Outcome"] == "Validation", "Detail"].astype(object).fillna("").astype(str)] if not detail.log.empty else []
        missing = [x for x in (notes.split("\n") if notes else []) + legacy if x]
        if missing:
            with st.expander(f"Validator Notes and Missing Evidence ({len(missing)})"):
                st.markdown("\n".join(f"- {fmt.esc(x)}" for x in missing), unsafe_allow_html=True)

with tab_policy:
    st.markdown("**Deterministic financial rules take precedence over AI recommendations.**")
    table = pd.DataFrame([{"Rule ID": r.rule_id, "Financial Control": r.control, "Result": r.result,
                           "Severity": r.severity, "Explanation": r.explanation} for r in policy.rules])
    c.html_table(table, badge_cols=["Result"], widths={"Rule ID": "8%", "Financial Control": "19%",
                                                       "Result": "13%", "Severity": "9%"})
    if policy.overridden_by_policy:
        c.callout("AI Recommendation Overridden by Financial Policy", "block")
    st.markdown("### Final Policy Decision")
    d1, d2 = st.columns(2)
    with d1:
        c.kv([
            ("Candidate Match", ", ".join(policy.candidate_invoices) or "None"),
            ("Original AI Recommendation", fmt.text(review.get("Original AI Recommendation"), policy.ai_recommendation)
             if review is not None else "Not Analyzed"),
            ("Policy-Validated Proposal", policy.ai_recommendation),
            ("Final Policy Decision", policy.final_decision),
            ("Execution Allowed", "Yes" if policy.execution_allowed else "No"),
        ])
    with d2:
        c.kv([
            ("Proposed Allocation", fmt.money(policy.proposed_allocation_cents, cur, cents=True)),
            ("Authorized Allocation", fmt.money(policy.authorized_allocation_cents, cur, cents=True)),
            ("Required Next Action", policy.required_action),
        ])
    if policy.blocking_reasons:
        c.callout("Blocking: " + " | ".join(policy.blocking_reasons), "block")
    elif policy.execution_allowed:
        c.callout("The proposal passes every blocking rule. It is applied only after a reviewer approves it.", "ok")

with tab_review:
    c.callout("Simulated Review Workflow: the reviewer name is self-declared in Settings. There is no enterprise "
              "authentication or role-based authorization.", "warn")
    current = fmt.text(review.get("Approval"), "") if review is not None else ""
    if current:
        c.kv([("Review Decision", current), ("Reviewer", fmt.text(review.get("Reviewer"))),
              ("Reviewed At", fmt.text(review.get("Decision Timestamp"))),
              ("Review Comments", fmt.text(review.get("Reviewer Notes"), "None")),
              ("Applied to AR", fmt.text(review.get("Applied To AR"), "No"))])
    if review is None:
        st.info("Run the investigation before recording a review decision.")
    else:
        options = ["Approve Proposal", "Reject Proposal", "Request More Evidence"]
        codes = {"Approve Proposal": "APPROVE", "Reject Proposal": "REJECT", "Request More Evidence": "MORE_EVIDENCE"}
        choice = st.radio("Review decision", options, horizontal=True, key=f"decision_{txn}",
                          index=1 if not policy.execution_allowed else 0)
        comment = st.text_area("Review comments", key=f"comment_{txn}", height=80)
        approve_blocked = choice == "Approve Proposal" and not policy.execution_allowed
        if approve_blocked:
            st.error(f"Approve is not available: {policy.final_decision}. Required action: {policy.required_action}.")
        if not st.session_state.reviewer:
            st.warning("Enter your reviewer name in Settings to record decisions.")
        cols = st.columns([1.2, 1.6, 3])
        if cols[0].button("Save Review", type="primary", disabled=approve_blocked or not st.session_state.reviewer):
            ok, message = services.decide(txn, codes[choice], comment)
            state.flash(message, "success" if ok else "error")
            st.rerun()
        pending_apply = model.metrics.review_counts.get("Approved - Not Yet Applied", 0)
        if cols[1].button(f"Apply Approved Resolutions ({pending_apply})", disabled=not pending_apply or st.session_state.busy):
            ok, message, _ = services.apply_approved()
            state.flash(message, "success" if ok else "error")
            st.rerun()
        cols[2].caption("Applying revalidates every approved resolution against current balances and the "
                        "financial policy, applies each one once, and recalculates the controls.")
