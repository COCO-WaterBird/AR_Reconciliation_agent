"""
Backend adapter: the only place the UI talks to the reconciliation services.

No financial logic lives here.  Every figure, status and rule result comes
from the existing backend: the engine (`run_reconciliation`), the report
model (`reporting.build_report_model`), the agent context and policy
decision (`agent.context.build_context`, `agent.policy.evaluate_policy`), the
exception workflow (`agent.workflow`) and the document store.

Caching: the report model and per-transaction details are cached by the
workbook's modification time.  Any action that changes the workbook (a run,
an analysis, a decision, an application) changes that time, so a cached
balance or approval status can never outlive the data it came from.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ar_reconciliation.agent.context import build_context
from ar_reconciliation.agent.policy import PolicyDecision, evaluate_policy
from ar_reconciliation.agent.workflow import apply_decisions, record_decision, run_analysis
from ar_reconciliation.config import AR_FILE, BANK_FILE, BANK_SHEET, AR_SHEET, load_env
from ar_reconciliation.core.primitives import normalize_key, normalize_text
from ar_reconciliation.evidence import DocumentRejected, DocumentStore, evidence_for_receipt, extract_document
from ar_reconciliation.reporting import build_report_model, load_config
from ar_reconciliation.services import run_reconciliation
from ar_reconciliation.services.reconciliation import AR_REQUIRED_COLUMNS, BANK_REQUIRED_COLUMNS

from app.ui import state

FRIENDLY_ERROR = "The operation could not be completed. Check the input files and try again."


# ---------------------------------------------------------------------------
# Data import
# ---------------------------------------------------------------------------

@dataclass
class FileCheck:
    role: str
    name: str
    size: int
    records: int = 0
    status: str = "Not Validated"
    missing: list[str] = field(default_factory=list)
    duplicates: int = 0
    issues: list[str] = field(default_factory=list)
    currencies: dict[str, int] = field(default_factory=dict)
    customers: int = 0
    preview: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def ok(self) -> bool:
        return self.status == "Valid"


def check_source(role: str, name: str, data: bytes) -> FileCheck:
    """Validate a bank or AR workbook against the engine's required columns."""
    check = FileCheck(role=role, name=name, size=len(data))
    sheet = BANK_SHEET if role == "Bank Statement" else AR_SHEET
    required = BANK_REQUIRED_COLUMNS if role == "Bank Statement" else AR_REQUIRED_COLUMNS
    try:
        frame = pd.read_excel(io.BytesIO(data), sheet_name=sheet)
    except ValueError:
        check.status = "Invalid"
        check.issues.append(f"The workbook has no sheet named '{sheet}'.")
        return check
    except Exception:
        check.status = "Invalid"
        check.issues.append("The file could not be read as an Excel workbook (.xlsx).")
        return check
    check.records = len(frame)
    check.missing = [c for c in required if c not in frame.columns]
    key = "Bank Transaction ID" if role == "Bank Statement" else "Invoice Number"
    if key in frame.columns:
        keys = frame[key].astype(object).fillna("").astype(str).str.strip()
        check.duplicates = int(keys[keys != ""].duplicated(keep=False).sum())
        if check.duplicates:
            check.issues.append(f"{check.duplicates} rows share a {key}; they will be held out of matching.")
        blank = int((keys == "").sum() + frame[key].isna().sum())
        if blank:
            check.issues.append(f"{blank} rows have no {key}.")
    if "Currency" in frame.columns:
        check.currencies = {str(k): int(v) for k, v in frame["Currency"].astype(object).fillna("").astype(str).value_counts().items()}
    if "ERP Customer ID" in frame.columns:
        check.customers = int(frame["ERP Customer ID"].nunique())
        missing_ids = int(frame["ERP Customer ID"].isna().sum())
        if missing_ids:
            check.issues.append(f"{missing_ids} rows have no ERP Customer ID.")
    check.preview = frame.head(50)
    check.status = "Invalid" if check.missing else ("Valid with Warnings" if check.issues else "Valid")
    if check.status == "Valid with Warnings":
        check.status = "Valid"  # warnings do not block a run; they are listed
    return check


SAMPLE_SOURCES = {"Use Synthetic Sample Data", "Use Synthetic Enriched Demo"}
STRUCTURED_KINDS = ("Customer Master", "Contract Schedule", "Remittance Advice")


def enriched_demo_files() -> dict[str, tuple[str, bytes]]:
    from ar_reconciliation.config import SAMPLE_DIR

    demo = SAMPLE_DIR / "enriched"
    names = {"Customer Master": "customer_master.csv", "Contract Schedule": "contract_schedule.csv",
             "Remittance Advice": "remittance_advice.csv"}
    return {k: (v, (demo / v).read_bytes()) for k, v in names.items()}


def check_structured(kind: str, name: str, data: bytes):
    """Validate one structured input with the engine's own loader."""
    from ar_reconciliation.enrichment import load_inputs

    keyword = {"Customer Master": "customer_master", "Contract Schedule": "contracts",
               "Remittance Advice": "remittances"}[kind]
    inputs = load_inputs(**{keyword: data}, labels={kind: name})
    return inputs.sources[0] if inputs.sources else {}, inputs.issues


def run(bank: tuple[str, bytes] | None, ar: tuple[str, bytes] | None, source: str,
        structured: dict[str, tuple[str, bytes]] | None = None) -> tuple[bool, str]:
    """Run the deterministic engine once into the session workspace."""
    if st.session_state.get("busy"):
        return False, "A reconciliation is already running."
    st.session_state.busy = True
    try:
        if source in SAMPLE_SOURCES:
            bank_path, ar_path = BANK_FILE, AR_FILE
        else:
            bank_path = state.workspace() / "bank_upload.xlsx"
            ar_path = state.workspace() / "ar_upload.xlsx"
            bank_path.write_bytes(bank[1])
            ar_path.write_bytes(ar[1])
        if source == "Use Synthetic Enriched Demo":
            structured = enriched_demo_files()
        expected = None
        if structured:
            from ar_reconciliation.enrichment import load_inputs
            from ar_reconciliation.enrichment.comparison import run_with_comparison

            keyword = {"Customer Master": "customer_master", "Contract Schedule": "contracts",
                       "Remittance Advice": "remittances"}
            inputs = load_inputs(**{keyword[k]: v[1] for k, v in structured.items()},
                                 labels={k: v[0] for k, v in structured.items()})
            if source == "Use Synthetic Enriched Demo":
                from ar_reconciliation.config import SAMPLE_DIR

                expected = SAMPLE_DIR / "enriched" / "expected_matches.csv"
            result, _ = run_with_comparison(bank_path, ar_path, state.result_path(), inputs, expected)
        else:
            result = run_reconciliation(bank_path=bank_path, ar_path=ar_path,
                                        output_path=state.result_path(), verbose=False)
        label = {"Use Synthetic Sample Data": "Synthetic Sample Data",
                 "Use Synthetic Enriched Demo": "Synthetic Enriched Demo"}.get(source, "Uploaded Files")
        info = {
            "source": label + (" + structured inputs" if structured and source == "Upload My Files" else ""),
            "bank_file": BANK_FILE.name if source in SAMPLE_SOURCES else bank[0],
            "ar_file": AR_FILE.name if source in SAMPLE_SOURCES else ar[0],
            "structured": ", ".join(f"{k}: {v[0]}" for k, v in (structured or {}).items()) or "None",
            "ran_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "controls_balanced": result.controls.balanced if result.controls else None,
        }
        st.session_state.run = info
        st.session_state.selected_txn = None
        st.session_state.run_history = st.session_state.run_history + [info]
        return True, "Reconciliation completed."
    except ValueError as exc:
        message = str(exc)
        if "missing required columns" in message:
            return False, message.split("\n")[0]
        if "do not balance" in message:
            return False, "Financial controls did not balance. The results were not finalized; review the diagnostic file."
        return False, FRIENDLY_ERROR
    except Exception:
        return False, FRIENDLY_ERROR
    finally:
        st.session_state.busy = False


# ---------------------------------------------------------------------------
# Report model and details (cached on the workbook's modification time)
# ---------------------------------------------------------------------------

def _mtime() -> float:
    path = state.result_path()
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data(show_spinner=False, max_entries=8)
def _sheets(path: str, mtime: float) -> dict[str, pd.DataFrame]:
    excel = pd.ExcelFile(path)
    names = ["Processed Bank Receipts", "Reconciled Open AR", "Unmatched Bank Receipts", "AI Exception Review",
             "AI Exception Log", "Approval Records", "Audit Trail", "Data Quality Checks", "Summary",
             "Supporting Documents", "Extracted Evidence", "Proposed Deductions", "Data Sources & Enrichment",
             "Enrichment Comparison"]
    return {n: pd.read_excel(excel, n) for n in names if n in excel.sheet_names}


def sheets() -> dict[str, pd.DataFrame]:
    return _sheets(str(state.result_path()), _mtime())


@st.cache_resource(show_spinner=False, max_entries=8)
def _model(path: str, mtime: float):
    return build_report_model(_sheets(path, mtime), load_config())


def model():
    """The backend report model: every KPI, status and control comes from it."""
    return _model(str(state.result_path()), _mtime())


def store() -> DocumentStore:
    return DocumentStore(state.evidence_dir())


@dataclass
class TransactionDetail:
    txn: str
    bank: pd.Series
    unmatched: pd.Series | None
    review: pd.Series | None
    context: Any
    policy: PolicyDecision
    evidence: Any
    applied_invoices: pd.DataFrame
    customer_open_ar: pd.DataFrame
    log: pd.DataFrame
    status_row: pd.Series | None


@st.cache_resource(show_spinner=False, max_entries=256)
def _detail(path: str, mtime: float, evidence_mtime: float, txn: str) -> TransactionDetail:
    data = _sheets(path, mtime)
    bank = data["Processed Bank Receipts"]
    ar = data["Reconciled Open AR"]
    key = normalize_key(txn)
    bank_row = bank.loc[bank["Bank Transaction ID"].apply(normalize_key) == key].iloc[0]
    unmatched = data.get("Unmatched Bank Receipts", pd.DataFrame())
    u_rows = unmatched.loc[unmatched["Bank Transaction ID"].apply(normalize_key) == key] if not unmatched.empty else unmatched
    review = data.get("AI Exception Review", pd.DataFrame())
    r_rows = review.loc[review["Bank Transaction ID"].apply(normalize_key) == key] if not review.empty else review
    review_row = r_rows.iloc[0] if len(r_rows) else None
    unmatched_row = u_rows.iloc[0] if len(u_rows) else bank_row

    doc_store = DocumentStore(state.evidence_dir())
    evidence = evidence_for_receipt(doc_store, txn, normalize_text(bank_row.get("Currency", "")))
    context = build_context(bank_row, unmatched_row, ar, set(),
                            evidence=evidence if evidence.has_documents else None)
    applied_cents = 0
    try:
        from ar_reconciliation.core.primitives import money_to_cents
        applied_cents = money_to_cents(bank_row.get("AR Applied Amount Total", 0))
    except Exception:
        pass
    policy = evaluate_policy(
        context, review_row,
        resolution_status=normalize_text(review_row.get("Validation Result")) if review_row is not None else "",
        evidence_sufficiency=normalize_text(review_row.get("Evidence Sufficiency")) if review_row is not None else "",
        already_applied=applied_cents > 0,
        approval_recorded=review_row is not None and normalize_text(review_row.get("Approval")).upper() == "APPROVE",
    )
    matched = ar["Matched Bank Transaction ID"].astype(object).fillna("").astype(str).str.upper().str.contains(key, regex=False, na=False)
    customer_key = normalize_key(bank_row.get("ERP Customer ID", ""))
    customer_ar = ar.loc[ar["ERP Customer ID"].apply(normalize_key) == customer_key] if customer_key else ar.iloc[0:0]
    log = data.get("AI Exception Log", pd.DataFrame())
    log = log.loc[log["Bank Transaction ID"].apply(normalize_key) == key] if not log.empty else log
    statuses = _model(path, mtime).statuses
    s_rows = statuses.loc[statuses["key"] == key]
    return TransactionDetail(txn, bank_row, u_rows.iloc[0] if len(u_rows) else None, review_row, context, policy,
                             evidence, ar.loc[matched], customer_ar, log, s_rows.iloc[0] if len(s_rows) else None)


def _evidence_mtime() -> float:
    manifest = state.evidence_dir() / "manifest.json"
    return manifest.stat().st_mtime if manifest.exists() else 0.0


def detail(txn: str) -> TransactionDetail:
    return _detail(str(state.result_path()), _mtime(), _evidence_mtime(), txn)


@st.cache_resource(show_spinner=False, max_entries=8)
def _policies(path: str, mtime: float, evidence_mtime: float) -> dict[str, PolicyDecision]:
    """The policy decision for every receipt that needs attention."""
    exceptions = _model(path, mtime).exceptions
    result = {}
    if exceptions.empty:
        return result
    for txn in exceptions["Bank Receipt ID"].astype(object).fillna("").astype(str):
        try:
            result[txn] = _detail(path, mtime, evidence_mtime, txn).policy
        except Exception:
            continue
    return result


def policies() -> dict[str, PolicyDecision]:
    return _policies(str(state.result_path()), _mtime(), _evidence_mtime())


def ai_source(analysed_by: str) -> str:
    """Name the analysis that actually produced a recommendation."""
    text = normalize_text(analysed_by)
    if not text:
        return "Not Analyzed"
    if "fallback" in text.lower():
        return "Deterministic Fallback (LLM Unavailable)"
    if text.startswith("LLM"):
        return "LLM Investigation"
    return "Deterministic Analysis"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def llm_status() -> tuple[bool, str]:
    load_env()
    if os.environ.get("OPENAI_API_KEY"):
        return True, "Configured"
    return False, "Not Configured"


def analyze(transaction_ids: list[str] | None = None) -> tuple[bool, str]:
    if st.session_state.get("busy"):
        return False, "Another operation is running."
    st.session_state.busy = True
    try:
        backend = "llm" if st.session_state.get("use_llm") and llm_status()[0] else "rules"
        options = {"model": os.environ.get("AR_AGENT_MODEL", "gpt-4o")} if backend == "llm" else {}
        outcome = run_analysis(state.result_path(), backend_name=backend, evidence_dir=state.evidence_dir(),
                               transaction_ids=transaction_ids or None, **options)
        errors = 0
        if not outcome.log_df.empty:
            errors = int((outcome.log_df["Outcome"] == "Model Error").sum())
        analysed = len(outcome.review_df) if transaction_ids else len(outcome.review_df)
        if transaction_ids:
            keys = {normalize_key(t) for t in transaction_ids}
            analysed = int(outcome.review_df["Bank Transaction ID"].apply(normalize_key).isin(keys).sum())
        message = f"Investigation completed for {analysed} receipt(s)."
        if errors:
            message += f" {errors} fell back to deterministic analysis because the LLM was unavailable."
        return True, message
    except Exception:
        return False, "The investigation could not be completed."
    finally:
        st.session_state.busy = False


def decide(txn: str, decision: str, notes: str) -> tuple[bool, str]:
    reviewer = normalize_text(st.session_state.get("reviewer"))
    if not reviewer:
        return False, "Enter your name as reviewer in Settings before recording a decision."
    if decision == "APPROVE" and not detail(txn).policy.execution_allowed:
        return False, "Approval is not available: the financial policy does not allow this proposal."
    try:
        record_decision(state.result_path(), txn, decision, reviewer, notes)
        return True, "Review decision saved."
    except ValueError as exc:
        return False, str(exc)


def apply_approved() -> tuple[bool, str, Any]:
    if st.session_state.get("busy"):
        return False, "Another operation is running.", None
    st.session_state.busy = True
    try:
        outcome = apply_decisions(state.result_path(), evidence_dir=state.evidence_dir(),
                                  reviewer=st.session_state.get("reviewer", ""))
        if outcome.failed_path is not None:
            return False, "A financial control did not balance. Nothing was applied.", outcome
        return True, (f"{outcome.applied_count} approved resolutions applied; "
                      f"{outcome.rejected_count} rejected by validation."), outcome
    except Exception:
        return False, "The approved resolutions could not be applied.", None
    finally:
        st.session_state.busy = False


def add_documents(files: list[tuple[str, bytes]], txns: list[str]) -> list[tuple[str, str, str]]:
    """Store uploads; returns (file, outcome, detail) per file."""
    results = []
    doc_store = store()
    for name, data in files:
        try:
            document, created = doc_store.add_bytes(data, name, txns, uploaded_by=st.session_state.get("reviewer", ""))
            extracted = extract_document(doc_store, document.doc_id)
            results.append((name, "Stored" if created else "Already stored - links updated",
                            f"{document.doc_id}; parsing: {extracted.status}"))
        except DocumentRejected as exc:
            results.append((name, "Rejected", str(exc)))
    return results


def document_rows() -> pd.DataFrame:
    """Every stored document with its lifecycle stages."""
    doc_store = store()
    review = sheets().get("AI Exception Review", pd.DataFrame()) if state.has_run() else pd.DataFrame()
    cited = " ".join(review["Evidence Citations"].astype(object).fillna("").astype(str)) if "Evidence Citations" in review.columns else ""
    used = " ".join(review["Supporting Documents"].astype(object).fillna("").astype(str)) if "Supporting Documents" in review.columns else ""
    rows = []
    for d in doc_store.documents():
        extracted = extract_document(doc_store, d.doc_id)
        if d.doc_id in cited:
            stage = "Used in Investigation"
        elif d.doc_id in used:
            stage = "Retrieved for Investigation"
        elif d.linked_txns:
            stage = "Linked"
        elif extracted.status == "Parsed":
            stage = "Parsed"
        else:
            stage = "Uploaded"
        rows.append({
            "Document ID": d.doc_id, "Document Name": d.filename, "Document Type": {"pdf": "PDF", "email": "Email", "text": "Text", "spreadsheet": "Spreadsheet"}.get(d.kind, d.kind),
            "File Size (KB)": round(d.size_bytes / 1024, 1), "Uploaded At": d.uploaded_at[:19].replace("T", " "),
            "Linked Transactions": ", ".join(d.linked_txns) or "Not Linked",
            "Parsing Status": extracted.status, "Facts Extracted": len(extracted.facts),
            "Evidence Status": stage, "Integrity": "Verified" if doc_store.verify(d.doc_id) else "Hash Mismatch",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Deductions and mailbox
# ---------------------------------------------------------------------------

def decide_deduction(deduction_id: str, decision: str, notes: str = "") -> tuple[bool, str]:
    from ar_reconciliation.agent.deductions import record_deduction_decision

    reviewer = normalize_text(st.session_state.get("reviewer"))
    if not reviewer:
        return False, "Enter your name as reviewer in Settings before recording a decision."
    try:
        record_deduction_decision(state.result_path(), deduction_id, decision, reviewer, notes)
        return True, "Deduction decision saved. Apply approved resolutions to post it."
    except ValueError as exc:
        return False, str(exc)


def gmail_status() -> tuple[bool, str]:
    """Gmail is usable only with the optional libraries and a client secret file."""
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
    except ImportError:
        return False, "Not Configured (optional Google client libraries are not installed)"
    secret = os.environ.get("AR_GMAIL_CLIENT_SECRET", "client_secret.json")
    if not Path(secret).exists():
        return False, "Not Configured (no OAuth client secret file)"
    return True, "Ready (read-only, gmail.readonly)"


def sync_mail(source_kind: str) -> tuple[bool, str, pd.DataFrame]:
    from ar_reconciliation.config import SAMPLE_DIR
    from ar_reconciliation.enrichment import load_inputs
    from ar_reconciliation.evidence.mail import (
        GmailSource,
        LocalMailboxSource,
        email_payment_records,
        link_email_records,
        sync_mailbox,
    )

    doc_store = store()
    try:
        source = (LocalMailboxSource(SAMPLE_DIR / "enriched" / "mailbox") if source_kind == "local"
                  else GmailSource(token_path=state.evidence_dir() / "gmail_token.json"))
        outcome = sync_mailbox(source, doc_store)
    except Exception as exc:  # noqa: BLE001 - shown as a plain message
        return False, f"Mailbox sync failed: {type(exc).__name__}.", pd.DataFrame()
    customers = load_inputs(customer_master=SAMPLE_DIR / "enriched" / "customer_master.csv").customers \
        if source_kind == "local" else None
    records = email_payment_records(doc_store, customers)
    if state.has_run() and not records.empty:
        records = link_email_records(doc_store, records, sheets()["Processed Bank Receipts"])
    message = (f"{outcome.source}: {outcome.fetched} fetched, {outcome.new_documents} new, "
               f"{outcome.duplicates} duplicate(s) skipped. Email facts are evidence for review and AI only.")
    st.session_state.mail_records = records
    return True, message, records

