"""
The exception-resolution workflow, as three plain functions.

The CLI and the review UI drive the same three steps, so the steps live here
and neither front end contains business logic:

  1. :func:`run_analysis` analyses every receipt in Needs Review (with its
     supporting documents, when a document store is given) and writes the
     review, evidence and document sheets into the result workbook;
  2. :func:`record_decision` records one reviewer decision (APPROVE, REJECT
     or MORE_EVIDENCE) with the reviewer's name and a timestamp;
  3. :func:`apply_decisions` applies the approved resolutions exactly once,
     recalculates the controls, appends the approval records and the audit
     trail, and refreshes the Summary.  If a control breaks, the result
     workbook is left untouched and a FAILED diagnostic workbook is written
     next to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ar_reconciliation.config import REVIEW_LOG_SHEET, REVIEW_SHEET
from ar_reconciliation.core.primitives import (
    cents_to_money,
    money_to_cents,
    normalize_key,
    normalize_text,
)
from ar_reconciliation.core.schema import (
    APPROVAL_RECORDS_SHEET,
    AUDIT_TRAIL_SHEET,
    DOCUMENTS_SHEET,
    EVIDENCE_SHEET,
    OUT_AI_APPLIED_AMOUNT_COL,
    OUT_APPROVED_ADJUSTMENT_COL,
    REVIEW_APPLIED_COL,
    REVIEW_APPROVAL_COL,
    REVIEW_BANK_TXN_COL,
    REVIEW_DECISION_TS_COL,
    REVIEW_DECISIONS,
    REVIEW_REVIEWER_COL,
    REVIEW_REVIEWER_NOTES_COL,
)

__all__ = [
    "AnalysisOutcome",
    "ApplyOutcome",
    "documents_frame",
    "run_analysis",
    "record_decision",
    "apply_decisions",
]

EVIDENCE_COLUMNS = [
    "Bank Transaction ID", "Document ID", "Field", "Value", "Invoice", "Amount",
    "Currency", "Location", "Quote", "Extracted By",
]
DOCUMENT_COLUMNS = [
    "Document ID", "File Name", "Type", "Size (bytes)", "SHA-256", "Uploaded At",
    "Uploaded By", "Linked Bank Transactions", "Parse Status", "Parse Error", "Original Intact",
]
APPROVAL_COLUMNS = [
    "Bank Transaction ID", "Resolution ID", "Decision", "Reviewer", "Decision Timestamp",
    "Reviewer Notes", "Approved Allocations", "Approved Adjustments", "Outcome", "Detail",
    "Processed At",
]
AUDIT_COLUMNS = [
    "Timestamp", "Event", "Bank Transaction ID", "Resolution ID", "Invoice Number",
    "Customer ID", "Open Before", "Cash Applied", "Adjustment Amount", "Adjustment Type",
    "Open After", "Supporting Documents", "Reviewer", "Detail",
]


@dataclass
class AnalysisOutcome:
    review_df: pd.DataFrame
    log_df: pd.DataFrame
    evidence_df: pd.DataFrame
    documents_df: pd.DataFrame
    backend: Any = None


@dataclass
class ApplyOutcome:
    applied_count: int = 0
    rejected_count: int = 0
    run_status: str = "FINAL"
    controls: Any = None
    log: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    failed_path: Path | None = None


def _store(evidence_dir: Path | None):
    if evidence_dir is None:
        return None
    from ar_reconciliation.evidence.store import DocumentStore

    return DocumentStore(Path(evidence_dir))


def documents_frame(store: Any) -> pd.DataFrame:
    """One row per stored document: identity, links and integrity."""
    if store is None:
        return pd.DataFrame(columns=DOCUMENT_COLUMNS)
    from ar_reconciliation.evidence.service import extract_document

    rows = []
    for document in store.documents():
        extracted = extract_document(store, document.doc_id)
        rows.append({
            "Document ID": document.doc_id,
            "File Name": document.filename,
            "Type": document.kind,
            "Size (bytes)": document.size_bytes,
            "SHA-256": document.sha256,
            "Uploaded At": document.uploaded_at,
            "Uploaded By": document.uploaded_by,
            "Linked Bank Transactions": ", ".join(document.linked_txns),
            "Parse Status": extracted.status,
            "Parse Error": extracted.error,
            "Original Intact": "Yes" if store.verify(document.doc_id) else "NO - hash mismatch",
        })
    return pd.DataFrame(rows, columns=DOCUMENT_COLUMNS)


def _write_sheets(result_path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(result_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, index=False, sheet_name=name)
    from ar_reconciliation.io.workbook import apply_workbook_formatting

    apply_workbook_formatting(result_path)


def _read_sheet(result_path: Path, name: str, columns: list[str]) -> pd.DataFrame:
    try:
        frame = pd.read_excel(result_path, sheet_name=name)
    except Exception:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame


def run_analysis(
    result_path: Path,
    backend_name: str = "rules",
    evidence_dir: Path | None = None,
    llm_extract: bool = False,
    backend_override: Any | None = None,
    **llm_options: Any,
) -> AnalysisOutcome:
    """Analyse the receipts in Needs Review and write the review sheets."""
    from ar_reconciliation.agent.exception_agent import analyse_exceptions, write_review_sheet

    store = _store(evidence_dir)
    evidence_rows: list[dict[str, Any]] = []
    _ar, _result, review_df, log_df, _unmatched, backend = analyse_exceptions(
        result_path=Path(result_path),
        backend_name=backend_name,
        evidence_store=store,
        llm_extract=llm_extract,
        evidence_rows=evidence_rows,
        backend_override=backend_override,
        **llm_options,
    )
    evidence_df = pd.DataFrame(evidence_rows, columns=EVIDENCE_COLUMNS)
    docs_df = documents_frame(store)
    if not review_df.empty:
        write_review_sheet(Path(result_path), review_df, log_df)
        _write_sheets(Path(result_path), {DOCUMENTS_SHEET: docs_df, EVIDENCE_SHEET: evidence_df})
    return AnalysisOutcome(review_df, log_df, evidence_df, docs_df, backend)


def record_decision(
    result_path: Path,
    txn_id: str,
    decision: str,
    reviewer: str,
    notes: str = "",
    timestamp: str | None = None,
) -> None:
    """Record one reviewer decision in the review sheet."""
    from ar_reconciliation.agent.exception_agent import _decision, _now_iso

    canonical = _decision(decision)
    if canonical not in REVIEW_DECISIONS:
        raise ValueError(f"Decision must be one of {', '.join(REVIEW_DECISIONS)}; got {decision!r}.")
    if not normalize_text(reviewer):
        raise ValueError("A reviewer name is required to record a decision.")
    review = pd.read_excel(result_path, sheet_name=REVIEW_SHEET)
    for column in (REVIEW_APPROVAL_COL, REVIEW_REVIEWER_COL, REVIEW_DECISION_TS_COL, REVIEW_REVIEWER_NOTES_COL):
        if column not in review.columns:
            review[column] = ""
        review[column] = review[column].astype(object).where(review[column].notna(), "")
    mask = review[REVIEW_BANK_TXN_COL].apply(normalize_key) == normalize_key(txn_id)
    if mask.sum() != 1:
        raise ValueError(f"{txn_id} is not exactly once in the {REVIEW_SHEET} sheet.")
    review.loc[mask, REVIEW_APPROVAL_COL] = canonical
    review.loc[mask, REVIEW_REVIEWER_COL] = normalize_text(reviewer)
    review.loc[mask, REVIEW_DECISION_TS_COL] = timestamp or _now_iso()
    review.loc[mask, REVIEW_REVIEWER_NOTES_COL] = normalize_text(notes)
    _write_sheets(Path(result_path), {REVIEW_SHEET: review})


def _refresh_summary(summary_df: pd.DataFrame, controls: Any, review_df: pd.DataFrame,
                     ar_df: pd.DataFrame, result_df: pd.DataFrame, applied_count: int) -> pd.DataFrame:
    from ar_reconciliation.agent.exception_agent import refresh_summary

    summary = refresh_summary(summary_df, controls, applied_count)
    ai_cash = 0
    if OUT_AI_APPLIED_AMOUNT_COL in result_df.columns:
        ai_cash = int(result_df[OUT_AI_APPLIED_AMOUNT_COL].astype(object).fillna("").apply(money_to_cents).sum())
    adjustments = 0
    if OUT_APPROVED_ADJUSTMENT_COL in ar_df.columns:
        adjustments = int(ar_df[OUT_APPROVED_ADJUSTMENT_COL].astype(object).fillna("").apply(money_to_cents).sum())
    resolutions = 0
    if "Resolution ID" in result_df.columns:
        resolutions = int(result_df["Resolution ID"].astype(object).fillna("").astype(str).str.strip().ne("").sum())
    still_open = 0
    if not review_df.empty and REVIEW_APPLIED_COL in review_df.columns:
        still_open = int((review_df[REVIEW_APPLIED_COL].astype(object).fillna("").astype(str) != "Yes").sum())
    updates = [
        ["AI-ASSISTED RESOLUTION", ""],
        ["Evidence-based resolutions applied", resolutions],
        ["AI-resolved cash applied (all currencies)", cents_to_money(ai_cash)],
        ["Approved non-cash adjustments (all currencies)", cents_to_money(adjustments)],
        ["Receipts remaining in Needs Review", still_open],
    ]
    summary["Value"] = summary["Value"].astype(object)
    for metric, value in updates:
        mask = summary["Metric"] == metric
        if mask.any():
            summary.loc[mask, "Value"] = value
        else:
            summary = pd.concat(
                [summary, pd.DataFrame([[metric, value]], columns=["Metric", "Value"])],
                ignore_index=True,
            )
    return summary


def apply_decisions(
    result_path: Path,
    evidence_dir: Path | None = None,
    reviewer: str = "",
) -> ApplyOutcome:
    """
    Apply the decisions in the review sheet and recalculate the controls.

    Nothing in the result workbook changes when a control breaks; the
    diagnostics are written to ``<result>_FAILED.xlsx`` instead.
    """
    from ar_reconciliation.agent.exception_agent import apply_approved_recommendations
    from ar_reconciliation.services.controls import compute_output_controls, control_break_rows

    result_path = Path(result_path)
    store = _store(evidence_dir)
    ar_df = pd.read_excel(result_path, sheet_name="Reconciled Open AR")
    result_df = pd.read_excel(result_path, sheet_name="Processed Bank Receipts")
    review_df = pd.read_excel(result_path, sheet_name=REVIEW_SHEET)
    log_df = _read_sheet(result_path, REVIEW_LOG_SHEET, ["Bank Transaction ID", "Outcome", "Detail"])

    outcome = ApplyOutcome()
    ar_updated, result_updated, outcome.log = apply_approved_recommendations(
        result_df=result_df,
        ar_df=ar_df,
        review_df=review_df,
        evidence_store=store,
        reviewer=reviewer,
        records=outcome.records,
        audit=outcome.audit,
    )
    controls = compute_output_controls(ar_updated, result_updated)
    outcome.controls = controls
    outcome.run_status = controls.run_status
    applied_txns = {
        normalize_key(e["Bank Transaction ID"]) for e in outcome.log if e["Outcome"] in {"Applied", "Adjusted"}
    }
    outcome.applied_count = len(applied_txns)
    outcome.rejected_count = sum(1 for e in outcome.log if e["Outcome"] == "Rejected")

    if not controls.balanced:
        failed = result_path.with_name(f"{result_path.stem}_FAILED.xlsx")
        summary = pd.DataFrame(
            [["Run Status", "FAILED"], ["Cause", controls.describe_breaks()]],
            columns=["Metric", "Value"],
        )
        with pd.ExcelWriter(failed, engine="openpyxl") as writer:
            summary.to_excel(writer, index=False, sheet_name="Summary")
            control_break_rows(controls).to_excel(writer, index=False, sheet_name="Control Breaks")
            ar_updated.to_excel(writer, index=False, sheet_name="Reconciled Open AR")
            result_updated.to_excel(writer, index=False, sheet_name="Processed Bank Receipts")
            pd.DataFrame(outcome.log).to_excel(writer, index=False, sheet_name="AI Exception Log")
        outcome.failed_path = failed
        return outcome

    review_df[REVIEW_APPLIED_COL] = review_df[REVIEW_APPLIED_COL].astype(object)
    applied_mask = review_df[REVIEW_BANK_TXN_COL].apply(normalize_key).isin(applied_txns)
    review_df.loc[applied_mask, REVIEW_APPLIED_COL] = "Yes"

    if outcome.log:
        log_df = pd.concat([log_df, pd.DataFrame(outcome.log)], ignore_index=True)
    records_df = pd.concat(
        [_read_sheet(result_path, APPROVAL_RECORDS_SHEET, APPROVAL_COLUMNS),
         pd.DataFrame(outcome.records, columns=APPROVAL_COLUMNS)],
        ignore_index=True,
    )[APPROVAL_COLUMNS]
    # A REJECT or MORE_EVIDENCE decision is the same decision every time the
    # sheet is processed; it is recorded once.  Every approval attempt is
    # kept, so a repeated approval shows up as "Already applied".
    keys = ["Bank Transaction ID", "Resolution ID", "Decision", "Decision Timestamp", "Outcome"]
    standing = records_df["Decision"].astype(str).isin({"REJECT", "MORE_EVIDENCE"})
    duplicate = records_df.astype(str).duplicated(subset=keys, keep="first") & standing
    records_df = records_df.loc[~duplicate].reset_index(drop=True)
    audit_df = pd.concat(
        [_read_sheet(result_path, AUDIT_TRAIL_SHEET, AUDIT_COLUMNS),
         pd.DataFrame(outcome.audit, columns=AUDIT_COLUMNS)],
        ignore_index=True,
    )[AUDIT_COLUMNS]
    # The count on the Summary is cumulative: every receipt the AI path has
    # applied so far, not only the ones applied by this run.
    total_applied = int(
        result_updated["Match Method"].astype(object).fillna("").astype(str)
        .isin({"AI Approved Match", "AI Approved Resolution"}).sum()
    )
    summary_df = _refresh_summary(
        pd.read_excel(result_path, sheet_name="Summary"), controls, review_df,
        ar_updated, result_updated, total_applied,
    )

    sheets = {
        "Reconciled Open AR": ar_updated,
        "Processed Bank Receipts": result_updated,
        "Summary": summary_df,
        REVIEW_SHEET: review_df,
        REVIEW_LOG_SHEET: log_df,
        APPROVAL_RECORDS_SHEET: records_df,
        AUDIT_TRAIL_SHEET: audit_df,
    }
    if store is not None:
        sheets[DOCUMENTS_SHEET] = documents_frame(store)
    _write_sheets(result_path, sheets)
    return outcome
