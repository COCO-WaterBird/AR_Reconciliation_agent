"""Evidence Center - supporting documents, their parsing and their use."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ar_reconciliation.evidence import extract_document, suggest_links
from ar_reconciliation.evidence.samples import SAMPLE_DOCUMENTS

from app.ui import components as c
from app.ui import fmt, layout, services, state

layout.header("Evidence Center", "Contracts, customer emails and remittance advice used to investigate exceptions.",
              show_status=False)

docs = services.document_rows()
stages = docs["Evidence Status"].value_counts().to_dict() if not docs.empty else {}
cards = st.columns(6)
stats = [
    ("Uploaded Documents", len(docs)),
    ("Parsed Successfully", int((docs["Parsing Status"] == "Parsed").sum()) if not docs.empty else 0),
    ("Parsing Failures", int((docs["Parsing Status"] != "Parsed").sum()) if not docs.empty else 0),
    ("Linked to Transactions", int((docs["Linked Transactions"] != "Not Linked").sum()) if not docs.empty else 0),
    ("Retrieved for Investigation", stages.get("Retrieved for Investigation", 0) + stages.get("Used in Investigation", 0)),
    ("Cited in Investigation", stages.get("Used in Investigation", 0)),
]
for col, (label, value) in zip(cards, stats):
    with col:
        c.kpi(label, fmt.count(value))
st.caption("Stages: Uploaded → Parsed (text read) → Linked (to a receipt) → Retrieved (given to the investigation) → "
           "Cited (the analysis quoted it). Documents are untrusted input: their text is evidence, never instructions.")

upload, sample = st.columns([3, 1])
with upload:
    with st.container(border=True):
        st.markdown("### Upload Documents")
        files = st.file_uploader("PDF, EML, TXT, XLSX or CSV (up to 10 MB each)", accept_multiple_files=True,
                                 type=["pdf", "eml", "txt", "xlsx", "csv"], key="ec_upload")
        txns: list[str] = []
        if state.has_run():
            open_ids = list(services.model().exceptions["Bank Receipt ID"]) if not services.model().exceptions.empty else []
            txns = st.multiselect("Link to transactions (optional)", open_ids, key="ec_link")
        if st.button("Store Documents", type="primary", disabled=not files):
            for name, outcome, info in services.add_documents([(f.name, f.getvalue()) for f in files], txns):
                (st.success if outcome != "Rejected" else st.error)(f"{name}: {outcome} — {info}")
with sample:
    with st.container(border=True):
        st.markdown("### Sample Documents")
        st.caption(f"{len(SAMPLE_DOCUMENTS)} synthetic documents linked to receipts in the sample data.")
        if st.button("Load Sample Documents", use_container_width=True):
            for name, (data, linked, _scenario) in SAMPLE_DOCUMENTS.items():
                services.store().add_bytes(data, name, linked, uploaded_by="Sample data")
            state.flash("Synthetic sample documents stored and linked.", "info")
            st.rerun()

with st.container(border=True):
    st.markdown("### Payment Emails (Read-Only Sync)")
    m1, m2 = st.columns([3, 2])
    with m1:
        st.caption("Emails are synced incrementally (only new messages), deduplicated by content and parsed once. "
                   "Their facts are evidence for review and the AI agent; they never trigger an automatic match.")
        gmail_ok, gmail_text = services.gmail_status()
        c.kv([("Synthetic demo mailbox", "Available (no credentials needed)"), ("Gmail", gmail_text)])
    with m2:
        if st.button("Sync Demo Mailbox", use_container_width=True, disabled=st.session_state.busy):
            ok, message, _ = services.sync_mail("local")
            state.flash(message, "success" if ok else "error")
            st.rerun()
        if st.button("Sync Gmail (Read-Only)", use_container_width=True, disabled=not gmail_ok,
                     help="Requires the optional Google client libraries and an OAuth client secret; see the README."):
            ok, message, _ = services.sync_mail("gmail")
            state.flash(message, "success" if ok else "error")
            st.rerun()
    records = st.session_state.get("mail_records")
    if records is not None and not records.empty:
        st.dataframe(records.drop(columns=["Document ID", "Message ID"], errors="ignore"), hide_index=True,
                     use_container_width=True, column_config={
                         "Payment Amount": c.money_col("Payment Amount"),
                         "Deduction Amount": c.money_col("Deduction Amount"),
                         "Deduction Reason": st.column_config.TextColumn(width="large")})

st.markdown("### Document Library")
if docs.empty:
    c.empty_state("No documents have been uploaded in this session.")
    st.stop()
f = st.columns([3, 1.4, 1.6])
search = f[0].text_input("Search", placeholder="Document name, ID or transaction", key="ec_search", label_visibility="collapsed")
stage = f[1].selectbox("Evidence Status", ["All"] + sorted(docs["Evidence Status"].unique()), key="ec_stage",
                       label_visibility="collapsed")
kind = f[2].selectbox("Document Type", ["All"] + sorted(docs["Document Type"].unique()), key="ec_kind",
                      label_visibility="collapsed")
frame = docs
if search:
    needle = search.lower()
    frame = frame[frame.apply(lambda r: needle in " ".join(map(str, r.values)).lower(), axis=1)]
if stage != "All":
    frame = frame[frame["Evidence Status"] == stage]
if kind != "All":
    frame = frame[frame["Document Type"] == kind]
event = st.dataframe(frame, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
                     key="ec_table", column_config={"Facts Extracted": c.count_col("Facts Extracted"),
                                                    "Linked Transactions": st.column_config.TextColumn(width="medium")})
rows = event.selection.rows if event and event.selection else []
if rows:
    doc_id = frame.iloc[rows[0]]["Document ID"]
    store = services.store()
    document = store.get(doc_id)
    extracted = extract_document(store, doc_id)
    with st.container(border=True):
        st.markdown(f"### {fmt.esc(document.filename)}", unsafe_allow_html=True)
        c.kv([("Document ID", doc_id), ("SHA-256", document.sha256), ("Uploaded At", document.uploaded_at),
              ("Uploaded By", document.uploaded_by or "Not Recorded"),
              ("Linked Transactions", ", ".join(document.linked_txns) or "Not Linked"),
              ("Parsing Status", extracted.status + (f" — {extracted.error}" if extracted.error else ""))])
        t1, t2 = st.tabs(["Extracted Facts", "Document Text"])
        with t1:
            if extracted.facts:
                st.dataframe(pd.DataFrame([{"Location": x.location, "Fact": x.field.replace("_", " ").title(),
                                            "Value": x.value, "Invoice": x.invoice or "Not Stated",
                                            "Amount": (x.amount_cents / 100) if x.amount_cents is not None else float("nan"),
                                            "Quote": x.quote} for x in extracted.facts]),
                             hide_index=True, use_container_width=True,
                             column_config={"Amount": c.money_col("Amount"), "Quote": st.column_config.TextColumn(width="large")})
            else:
                st.caption("No financial facts were found in this document.")
        with t2:
            st.code("\n".join(f"[{loc}] {text}" for loc, text in extracted.lines) or "No text layer.", language=None)
        if state.has_run():
            link_to = st.multiselect("Link to additional transactions", list(services.model().exceptions["Bank Receipt ID"]),
                                     key=f"ec_more_{doc_id}")
            if st.button("Save Links", disabled=not link_to):
                store.link(doc_id, link_to)
                state.flash("Links saved.", "info")
                st.rerun()

if state.has_run():
    unmatched = services.sheets().get("Unmatched Bank Receipts", pd.DataFrame())
    suggestions = suggest_links(services.store(), unmatched) if not unmatched.empty else []
    if suggestions:
        with st.expander(f"Suggested Links ({len(suggestions)})"):
            st.caption("Based on matching invoice numbers or amounts. Suggestions are not linked automatically.")
            st.dataframe(pd.DataFrame(suggestions), hide_index=True, use_container_width=True)
