"""
Assemble every report table from the result workbook's system-of-record
sheets.

This module calls the analytics (statuses, metrics, controls, exceptions,
aging, attention) and returns plain DataFrames and values.  It does no
styling and adds no new financial calculation, so the Excel exporter and the
tests read exactly the same numbers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from ar_reconciliation.reporting.aging import aging_detail, aging_summary, customer_risk
from ar_reconciliation.reporting.attention import management_attention
from ar_reconciliation.reporting.config import ReportingConfig, load_config
from ar_reconciliation.reporting.controls_matrix import control_matrix, overall_control_status
from ar_reconciliation.reporting.exceptions import (
    exception_records,
    exception_summary,
    review_queue,
    root_cause_summary,
)
from ar_reconciliation.reporting.metrics import RunMetrics, compute_metrics
from ar_reconciliation.reporting.statuses import POSTING_STATUS, cents, receipt_statuses, text

__all__ = ["ReportModel", "build_report_model", "resolve_as_of"]


@dataclass
class ReportModel:
    as_of: date | None
    as_of_basis: str
    generated_at: str
    run_id: str
    config: ReportingConfig
    statuses: pd.DataFrame
    metrics: RunMetrics
    controls: pd.DataFrame
    processing_status: str
    control_status: str
    review_status: str
    posting_status: str
    exceptions: pd.DataFrame
    exception_summary: pd.DataFrame
    root_causes: pd.DataFrame
    queue: pd.DataFrame
    aging_detail: pd.DataFrame
    aging_summary: pd.DataFrame
    customer_risk: pd.DataFrame
    attention: pd.DataFrame
    evidence: pd.DataFrame
    cash_results: pd.DataFrame
    metadata: list[tuple[str, Any]] = field(default_factory=list)


def resolve_as_of(bank: pd.DataFrame, config: ReportingConfig) -> tuple[date | None, str]:
    if config.as_of_date is not None:
        return config.as_of_date, "Configured Reporting As-of Date"
    for column in ("Posting Date", "Value Date"):
        if column in bank.columns:
            dates = pd.to_datetime(bank[column], errors="coerce").dropna()
            if len(dates):
                return dates.max().date(), f"Latest bank {column} in the run (default; configurable)"
    return None, "Not available (no bank dates)"


def _summary_value(summary: pd.DataFrame, metric: str) -> Any:
    if summary.empty or "Metric" not in summary.columns:
        return None
    match = summary.loc[summary["Metric"].astype(str) == metric, "Value"]
    return None if match.empty else match.iloc[0]


def _run_id(bank: pd.DataFrame, as_of: date | None) -> str:
    """Deterministic for identical input receipts and as-of date."""
    digest = hashlib.sha256()
    digest.update(str(as_of).encode())
    if not bank.empty:
        for column in ("Bank Transaction ID", "Gross Receipt Amount", "Currency"):
            if column in bank.columns:
                digest.update("|".join(bank[column].astype(str)).encode())
    return "RUN-" + digest.hexdigest()[:10].upper()


def _cash_results(statuses: pd.DataFrame, ar: pd.DataFrame) -> pd.DataFrame:
    invoices: dict[str, list[str]] = {}
    if not ar.empty and "Matched Bank Transaction ID" in ar.columns:
        applied = ar["Applied Amount"].apply(cents) if "Applied Amount" in ar else 0
        for (_, row), cash in zip(ar.iterrows(), applied):
            if cash <= 0:
                continue
            for txn in text(row.get("Matched Bank Transaction ID")).split(","):
                key = txn.strip().upper()
                if key:
                    invoices.setdefault(key, []).append(text(row.get("Invoice Number")))
    frame = pd.DataFrame({
        "Bank Receipt ID": statuses["Bank Transaction ID"],
        "Receipt Date": statuses["Value Date"],
        "Customer Name": statuses["Customer Name"],
        "ERP Customer ID": statuses["ERP Customer ID"],
        "Currency": statuses["Currency"],
        "Gross Receipt Amount": statuses["gross"] / 100,
        "Bank Fees": statuses["fees"] / 100,
        "Applied to AR": statuses["applied"] / 100,
        "Unapplied Cash": statuses["unapplied"] / 100,
        "Fee / Rounding Adjustment": statuses["adjustment"] / 100,
        "Held for Review": statuses["held"] / 100,
        "Invoices Applied": [", ".join(dict.fromkeys(invoices.get(t.upper(), []))) for t in statuses["Bank Transaction ID"]],
        "Match Status": statuses["Match Status"],
        "Review Status": statuses["Review Status"],
        "Posting Status": statuses["Posting Status"],
        "Settled By": statuses["Settled By"],
        "Match Method": statuses["Match Method"],
        "Resolution ID": statuses["Resolution ID"],
    })
    return frame


def _evidence(statuses: pd.DataFrame, review: pd.DataFrame, facts: pd.DataFrame,
              documents: pd.DataFrame, records: pd.DataFrame) -> pd.DataFrame:
    """Evidence per receipt: rule, AI recommendation (labelled), document facts, decisions."""
    columns = ["Bank Receipt ID", "Evidence Type", "Basis", "Source", "Detail", "Amount",
               "Location / Reference", "Quote / Notes", "Recorded At"]
    rows: list[dict] = []
    for _, s in statuses.iterrows():
        if s["Match Status"] in {"Fully Matched", "Partially Matched"}:
            rows.append({
                "Bank Receipt ID": s["Bank Transaction ID"], "Evidence Type": "Matching Rule",
                "Basis": "Confirmed - deterministic engine" if s["Settled By"] == "Deterministic rules"
                else "Confirmed - reviewer-approved resolution",
                "Source": s["Settled By"], "Detail": s["Match Method"], "Amount": s["applied"] / 100,
                "Location / Reference": s["Memo"], "Quote / Notes": s["Processing Notes"], "Recorded At": "",
            })
    if review is not None and not review.empty:
        for _, r in review.iterrows():
            rows.append({
                "Bank Receipt ID": text(r.get("Bank Transaction ID")), "Evidence Type": "AI Recommendation",
                "Basis": "AI-generated - not a confirmed fact",
                "Source": text(r.get("Analysed By")),
                "Detail": f"{text(r.get('AI Exception Type'))}: {text(r.get('Recommended Action'))} "
                          f"({text(r.get('AI Confidence'))} confidence)",
                "Amount": cents(r.get("Proposed Applied Amount")) / 100 or None,
                "Location / Reference": text(r.get("Validation Result")) or "No supporting documents",
                "Quote / Notes": text(r.get("AI Explanation")), "Recorded At": "",
            })
    names: dict[str, str] = {}
    if documents is not None and not documents.empty and "Document ID" in documents.columns:
        names = dict(zip(documents["Document ID"].astype(str), documents["File Name"].astype(str)))
    if facts is not None and not facts.empty:
        for _, f in facts.iterrows():
            doc = text(f.get("Document ID"))
            amount = f.get("Amount")
            rows.append({
                "Bank Receipt ID": text(f.get("Bank Transaction ID")), "Evidence Type": "Document Fact",
                "Basis": "Extracted from a supporting document",
                "Source": f"{doc} ({names[doc]})" if doc in names else doc,
                "Detail": f"{text(f.get('Field'))}: {text(f.get('Value'))}",
                "Amount": cents(amount) / 100 if text(amount) else None,
                "Location / Reference": text(f.get("Location")), "Quote / Notes": text(f.get("Quote")), "Recorded At": "",
            })
    if records is not None and not records.empty:
        for _, r in records.iterrows():
            rows.append({
                "Bank Receipt ID": text(r.get("Bank Transaction ID")), "Evidence Type": "Reviewer Decision",
                "Basis": "Recorded human decision", "Source": text(r.get("Reviewer")),
                "Detail": f"{text(r.get('Decision'))} - {text(r.get('Outcome'))}", "Amount": None,
                "Location / Reference": text(r.get("Resolution ID")),
                "Quote / Notes": text(r.get("Reviewer Notes")) or text(r.get("Detail")),
                "Recorded At": text(r.get("Decision Timestamp")),
            })
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    order = {"Matching Rule": 0, "AI Recommendation": 1, "Document Fact": 2, "Reviewer Decision": 3}
    return frame.assign(_o=frame["Evidence Type"].map(order)).sort_values(
        ["Bank Receipt ID", "_o"], kind="stable").drop(columns="_o").reset_index(drop=True)


def build_report_model(sheets: dict[str, pd.DataFrame], config: ReportingConfig | None = None,
                       generated_at: datetime | None = None) -> ReportModel:
    from ar_reconciliation.services.controls import compute_output_controls

    config = config or load_config()
    empty = pd.DataFrame()
    bank = sheets.get("Processed Bank Receipts", empty)
    ar = sheets.get("Reconciled Open AR", empty)
    review = sheets.get("AI Exception Review", empty)
    unmatched = sheets.get("Unmatched Bank Receipts", empty)
    records = sheets.get("Approval Records", empty)
    data_quality = sheets.get("Data Quality Checks", empty)
    summary = sheets.get("Summary", empty)

    as_of, basis = resolve_as_of(bank, config)
    statuses = receipt_statuses(bank, review) if not bank.empty else receipt_statuses(pd.DataFrame(columns=["Bank Transaction ID"]))
    applications = _summary_value(summary, "Matched AR invoice rows")
    metrics = compute_metrics(statuses, ar, config, int(applications) if applications not in (None, "") and not pd.isna(applications) else None)

    required_bank = {"Gross Receipt Amount", "AR Applied Amount Total", "Suggested Bank Status",
                     "Bank Transaction ID", "Currency"}
    required_ar = {"Open Amount", "Applied Amount", "Invoice Number", "Currency"}
    controls = None
    if not bank.empty and not ar.empty and required_bank <= set(bank.columns) and required_ar <= set(ar.columns):
        controls = compute_output_controls(ar, bank)
    matrix = control_matrix(controls, data_quality)
    exceptions = exception_records(statuses, review, unmatched, records, config)
    detail = aging_detail(ar, as_of, config) if as_of else aging_detail(pd.DataFrame(), date.today(), config)
    aging = aging_summary(detail, config)

    pending = metrics.review_counts.get("Pending Review", 0)
    awaiting = metrics.review_counts.get("Approved - Not Yet Applied", 0)
    if pending or awaiting:
        review_text = f"Pending ({pending + awaiting} receipts)"
    elif any(metrics.review_counts.get(s, 0) for s in ("Approved", "Rejected", "More Evidence Requested")):
        review_text = "Completed"
    else:
        review_text = "Not Required"

    generated = (generated_at or datetime.now(timezone.utc)).replace(microsecond=0)
    model = ReportModel(
        as_of=as_of, as_of_basis=basis, generated_at=generated.isoformat(), run_id=_run_id(bank, as_of),
        config=config, statuses=statuses, metrics=metrics, controls=matrix,
        processing_status="Completed" if not bank.empty else "No Data",
        control_status=overall_control_status(matrix), review_status=review_text,
        posting_status="Not Integrated (no ERP/GL posting)",
        exceptions=exceptions, exception_summary=exception_summary(exceptions),
        root_causes=root_cause_summary(exceptions), queue=review_queue(exceptions),
        aging_detail=detail, aging_summary=aging, customer_risk=customer_risk(detail, exceptions, config),
        attention=management_attention(exceptions, matrix, metrics.cash_by_currency, aging, detail, data_quality, config),
        evidence=_evidence(statuses, review, sheets.get("Extracted Evidence", empty),
                           sheets.get("Supporting Documents", empty), records),
        cash_results=_cash_results(statuses, ar) if not bank.empty else pd.DataFrame(),
    )
    model.metadata = [
        ("Run ID", model.run_id),
        ("Report Generated At (UTC)", model.generated_at),
        ("Reconciliation Run At (UTC)", text(_summary_value(summary, "Run Timestamp (UTC)")) or "Not recorded"),
        ("Reporting As-of Date", str(as_of) if as_of else "Not available"),
        ("As-of Date Basis", basis),
        ("Reporting Currency", config.reporting_currency or "None - amounts shown in original currency"),
        ("FX Rates", config.fx_status),
        ("Bank Input File", text(_summary_value(summary, "Bank Input File")) or "Not recorded"),
        ("Bank Input SHA-256", text(_summary_value(summary, "Bank Input SHA-256")) or "Not recorded"),
        ("AR Input File", text(_summary_value(summary, "AR Input File")) or "Not recorded"),
        ("AR Input SHA-256", text(_summary_value(summary, "AR Input SHA-256")) or "Not recorded"),
        ("Bank Receipts", len(bank)),
        ("AR Invoices", len(ar)),
        ("Processing Status", model.processing_status),
        ("Financial Control Status", model.control_status),
        ("Exception Review Status", model.review_status),
        ("Posting Status", model.posting_status),
        ("Posting Status (receipts)", POSTING_STATUS),
    ]
    for key in ("cash_application_rate_warning", "high_priority_categories", "missing_remittance_categories",
                "duplicate_categories"):
        model.metadata.append((f"Config: {key}", str(config.get(key))))
    for currency, values in sorted(config.get("materiality", {}).items()):
        model.metadata.append((f"Config: materiality {currency}", ", ".join(f"{k}={v}" for k, v in values.items())))
    rules = config.get("customer_risk", {})
    model.metadata.append(("Config: customer_risk", ", ".join(f"{k}={v}" for k, v in rules.items() if not k.startswith("_"))))
    model.metadata.append(("Config: aging buckets", "; ".join(b["label"] for b in config.aging_buckets)))
    return model
