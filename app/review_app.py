"""
AR Exception Review — a single-page review UI.

    python -m pip install -r requirements.txt -r requirements-ui.txt
    streamlit run app/review_app.py

Upload the bank statement and AR detail, run the deterministic engine,
pick a receipt in Needs Review, attach its contract / email / remittance,
let the agent analyse it, then approve, reject or ask for more evidence.
Applying the approvals recalculates the controls; the final report is a
download.

The page only calls the project's services
(:mod:`ar_reconciliation.services`, :mod:`ar_reconciliation.agent.workflow`,
:mod:`ar_reconciliation.evidence`).  It contains no matching, validation or
accounting logic of its own.  Every run happens in a private temporary
workspace; the repository's data folders are never written.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.agent.workflow import apply_decisions, record_decision, run_analysis  # noqa: E402
from ar_reconciliation.config import AR_FILE, BANK_FILE, load_env  # noqa: E402
from ar_reconciliation.core.primitives import normalize_key, normalize_text  # noqa: E402
from ar_reconciliation.evidence import DocumentRejected, DocumentStore  # noqa: E402
from ar_reconciliation.evidence.samples import SAMPLE_DOCUMENTS  # noqa: E402
from ar_reconciliation.services import run_reconciliation  # noqa: E402

st.set_page_config(page_title="AR Exception Review", layout="wide")


# ---------------------------------------------------------------------------
# Session workspace
# ---------------------------------------------------------------------------

def workspace() -> Path:
    if "workspace" not in st.session_state:
        st.session_state.workspace = Path(tempfile.mkdtemp(prefix="ar_review_"))
    return st.session_state.workspace


def result_path() -> Path:
    return workspace() / "Reconciliation_Result.xlsx"


def evidence_dir() -> Path:
    return workspace() / "evidence"


def store() -> DocumentStore:
    return DocumentStore(evidence_dir())


def read(sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(result_path(), sheet_name=sheet)
    except Exception:
        return pd.DataFrame()


def blank(value: object) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("AR Exception Review")
reviewer = st.sidebar.text_input("Reviewer name", key="reviewer")
load_env()
llm_available = bool(os.environ.get("OPENAI_API_KEY"))
use_llm = st.sidebar.checkbox(
    "Use the LLM backend",
    value=False,
    disabled=not llm_available,
    help="Needs OPENAI_API_KEY. Only extracted facts and masked short quotes are sent "
    "(decision E3). Without it the deterministic rules backend is used.",
)
st.sidebar.caption(f"Workspace: {workspace()}")
if st.sidebar.button("Start over"):
    shutil.rmtree(workspace(), ignore_errors=True)
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

# ---------------------------------------------------------------------------
# 1. Inputs and the deterministic engine
# ---------------------------------------------------------------------------

st.header("1. Reconcile")
col_bank, col_ar = st.columns(2)
bank_upload = col_bank.file_uploader("Bank statement (.xlsx)", type=["xlsx"], key="bank_upload")
ar_upload = col_ar.file_uploader("AR detail (.xlsx)", type=["xlsx"], key="ar_upload")
use_sample = st.checkbox("Use the synthetic sample data instead", value=bank_upload is None)

if st.button("Run reconciliation", type="primary"):
    if use_sample:
        bank_path, ar_path = BANK_FILE, AR_FILE
    elif bank_upload is None or ar_upload is None:
        st.error("Upload both workbooks, or tick 'Use the synthetic sample data'.")
        st.stop()
    else:
        bank_path = workspace() / "bank.xlsx"
        ar_path = workspace() / "ar.xlsx"
        bank_path.write_bytes(bank_upload.getvalue())
        ar_path.write_bytes(ar_upload.getvalue())
    try:
        outcome = run_reconciliation(
            bank_path=bank_path, ar_path=ar_path, output_path=result_path(), verbose=False
        )
    except ValueError as exc:
        st.error(f"The run did not finalise: {exc}")
        st.stop()
    st.session_state.reconciled = True
    st.success(
        f"{outcome.matched_receipt_count} receipts matched; "
        f"{outcome.unmatched_count} in Needs Review. Controls balance."
    )

if not st.session_state.get("reconciled") or not result_path().exists():
    st.info("Run the reconciliation to continue.")
    st.stop()

bank = read("Processed Bank Receipts")
unmatched = read("Unmatched Bank Receipts")
summary = read("Summary")
matched = bank.loc[bank["Suggested Bank Status"].astype(str) == "Reconciled"]

m1, m2, m3 = st.columns(3)
m1.metric("Matched (no AI needed)", len(matched))
m2.metric("Needs Review", len(unmatched))
status = summary.set_index("Metric")["Value"].get("Run Status", "") if not summary.empty else ""
m3.metric("Run status", blank(status) or "FINAL")

tab_review, tab_matched = st.tabs(["Needs Review", "Matched"])
with tab_matched:
    st.dataframe(matched, hide_index=True, use_container_width=True)
with tab_review:
    st.dataframe(
        unmatched[["Bank Transaction ID", "Customer Name", "Currency", "Gross Receipt Amount",
                   "Memo / Remittance Detail", "Reason"]],
        hide_index=True, use_container_width=True,
    )

# ---------------------------------------------------------------------------
# 2. One exception: documents and analysis
# ---------------------------------------------------------------------------

st.header("2. Investigate an exception")
txns = unmatched["Bank Transaction ID"].astype(str).tolist()
if not txns:
    st.success("Nothing is in Needs Review.")
    st.stop()
txn = st.selectbox("Receipt in Needs Review", txns, key="txn")
receipt = unmatched.loc[unmatched["Bank Transaction ID"].astype(str) == txn].iloc[0]

left, right = st.columns(2)
with left:
    st.subheader("Bank transaction")
    st.table(receipt[["Value Date", "Customer Name", "Remitter Name", "ERP Customer ID", "Currency",
                      "Gross Receipt Amount", "Bank Fees", "Net Amount", "Memo / Remittance Detail",
                      "Memo Scenario", "Reason"]].astype(str).rename("Value"))
with right:
    st.subheader("Open AR for this customer")
    ar = read("Reconciled Open AR")
    customer = normalize_key(receipt.get("ERP Customer ID", ""))
    if customer:
        rows = ar.loc[ar["ERP Customer ID"].apply(normalize_key) == customer]
    else:
        rows = ar.loc[ar["Customer Name"].apply(normalize_key) == normalize_key(receipt["Customer Name"])]
    st.dataframe(
        rows[["Invoice Number", "Invoice Date", "Due Date", "Currency", "Open Amount", "Applied Amount",
              "AR Status", "Dispute Reason"]],
        hide_index=True, use_container_width=True,
    )

st.subheader("Supporting documents")
uploads = st.file_uploader(
    "Contracts (PDF), emails (EML/TXT), remittance advice (PDF/XLSX/CSV/TXT)",
    type=["pdf", "eml", "txt", "xlsx", "csv"],
    accept_multiple_files=True,
    key=f"docs_{txn}",
)
c1, c2 = st.columns(2)
if c1.button("Attach to this receipt", disabled=not uploads):
    for upload in uploads or []:
        try:
            document, created = store().add_bytes(upload.getvalue(), upload.name, [txn], uploaded_by=reviewer)
            st.success(f"{document.doc_id} {upload.name}: {'stored' if created else 'already stored; linked'}")
        except DocumentRejected as exc:
            st.error(str(exc))
if c2.button("Load the synthetic sample documents"):
    for name, (data, linked, _scenario) in SAMPLE_DOCUMENTS.items():
        store().add_bytes(data, name, linked, uploaded_by="demo")
    st.success("Sample documents stored and linked to their receipts.")

linked_docs = store().documents_for(txn)
if linked_docs:
    st.dataframe(
        pd.DataFrame([{
            "Document ID": d.doc_id, "File": d.filename, "Type": d.kind, "Uploaded": d.uploaded_at,
            "Linked receipts": ", ".join(d.linked_txns),
        } for d in linked_docs]),
        hide_index=True, use_container_width=True,
    )
else:
    st.caption("No document is linked to this receipt.")

if st.button("Analyse exceptions", type="primary"):
    with st.spinner("Analysing..."):
        run_analysis(
            result_path(),
            backend_name="llm" if use_llm else "rules",
            evidence_dir=evidence_dir(),
        )
    st.session_state.analysed = True

review = read("AI Exception Review")
if review.empty:
    st.info("Run the analysis to see the agent's recommendation.")
    st.stop()

row = review.loc[review["Bank Transaction ID"].astype(str) == txn]
if row.empty:
    st.info("This receipt has not been analysed yet.")
    st.stop()
row = row.iloc[0]

st.subheader("AI analysis and validation")
a1, a2, a3, a4 = st.columns(4)
a1.metric("Recommended action", blank(row["Recommended Action"]))
a2.metric("Confidence", blank(row["AI Confidence"]))
a3.metric("Evidence", blank(row.get("Evidence Sufficiency")) or "No documents")
a4.metric("Validation", blank(row.get("Validation Result")) or "-")

st.markdown(f"**Exception type:** {blank(row['AI Exception Type'])}")
st.markdown(f"**Root cause:** {blank(row['Root Cause'])}")
st.markdown(f"**Explanation:** {blank(row['AI Explanation'])}")
st.markdown(f"**Proposed allocations:** {blank(row.get('Proposed Allocations')) or 'none'}")
st.markdown(f"**Proposed adjustments:** {blank(row.get('Proposed Adjustments')) or 'none'}")
st.markdown(f"**Accounting treatment:** {blank(row['Accounting Treatment'])}")
if blank(row.get("Evidence Conflicts")):
    st.warning("Conflicts: " + blank(row["Evidence Conflicts"]))
if blank(row.get("Validation Notes")):
    st.info("Validation notes:\n\n" + blank(row["Validation Notes"]))
with st.expander("Cited evidence"):
    st.text(blank(row.get("Evidence Citations")) or "(none)")
evidence = read("Extracted Evidence")
if not evidence.empty:
    with st.expander("All extracted facts for this receipt"):
        st.dataframe(evidence.loc[evidence["Bank Transaction ID"].astype(str) == txn],
                     hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
# 3. Decision
# ---------------------------------------------------------------------------

st.header("3. Decide")
current = blank(row.get("Approval"))
if current:
    st.caption(f"Current decision: {current} by {blank(row.get('Reviewer'))} at {blank(row.get('Decision Timestamp'))}")
decision = st.radio("Decision", ["APPROVE", "REJECT", "MORE_EVIDENCE"], horizontal=True,
                    format_func={"APPROVE": "Approve", "REJECT": "Reject",
                                 "MORE_EVIDENCE": "Request more evidence"}.get)
comment = st.text_input("Comment")
if st.button("Record decision"):
    if not normalize_text(reviewer):
        st.error("Enter your name in the sidebar first.")
    elif decision == "APPROVE" and not blank(row.get("Proposed Allocations")) and not blank(row.get("Proposed Invoice Numbers")):
        st.error("There is no proposal to approve for this receipt.")
    else:
        record_decision(result_path(), txn, decision, reviewer, comment)
        st.success(f"Recorded {decision} for {txn}.")
        st.rerun()

# ---------------------------------------------------------------------------
# 4. Apply, controls, report
# ---------------------------------------------------------------------------

st.header("4. Apply approved resolutions")
if st.button("Apply approved resolutions", type="primary"):
    outcome = apply_decisions(result_path(), evidence_dir=evidence_dir(), reviewer=reviewer)
    if outcome.failed_path is not None:
        st.error(
            "A control does not balance; the report was NOT updated. "
            + outcome.controls.describe_breaks()
        )
        st.download_button("Download the FAILED diagnostics", outcome.failed_path.read_bytes(),
                           file_name=outcome.failed_path.name)
    else:
        st.success(f"Applied {outcome.applied_count}; rejected by validation {outcome.rejected_count}.")
    if outcome.log:
        st.dataframe(pd.DataFrame(outcome.log), hide_index=True, use_container_width=True)

summary = read("Summary")
if not summary.empty:
    values = summary.set_index("Metric")["Value"]
    rows = []
    for metric, value in values.items():
        metric = str(metric)
        for suffix in ("receipts (gross)", "applied to AR", "cash control difference",
                       "bank vs AR applied difference", "AR roll-forward difference"):
            if metric.endswith(suffix) and len(metric.split(" ")[0]) == 3 and metric.split(" ")[0].isupper():
                rows.append({"Currency": metric.split(" ")[0], "Control": suffix, "Value": value})
    if rows:
        st.subheader("Financial controls by currency")
        table = pd.DataFrame(rows).pivot(index="Control", columns="Currency", values="Value")
        st.dataframe(table, use_container_width=True)

st.download_button(
    "Download the reconciliation report",
    result_path().read_bytes(),
    file_name="Open_AR_Detail_Reconciled_Result.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
