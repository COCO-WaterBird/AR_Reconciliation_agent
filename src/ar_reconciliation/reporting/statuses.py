"""
Receipt-level statuses, kept as three independent dimensions.

The engine's own counts overlap: a receipt can be matched *and* pending
review (for example a bank-fee match that a reviewer must confirm).  The
report therefore classifies every receipt three times, independently:

* **Match Status** - what happened to the cash.  Mutually exclusive; every
  receipt has exactly one, so the counts add up to all receipts:

    - ``Fully Matched``         cash applied to AR, nothing left unapplied;
    - ``Partially Matched``     cash applied to AR, a remainder held on account;
    - ``Unmatched``             no cash applied (held for review);
    - ``Refund / Reversal``     a negative receipt, never applied to AR;
    - ``Previously Reconciled`` reconciled in the source before this run.

* **Review Status** - whether a person still has to act:
  ``Pending Review``, ``Approved``, ``Approved - Not Yet Applied``,
  ``Rejected``, ``More Evidence Requested`` or ``Not Required``.  Only
  decisions recorded in the AI Exception Review sheet count; nothing is
  inferred.

* **Posting Status** - the project has no ERP / GL connection, so every
  receipt is ``Not Posted (ERP/GL Not Integrated)``.

Amounts are integer cents throughout.
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.core.primitives import money_to_cents, normalize_key, normalize_text

__all__ = [
    "MATCH_STATUSES",
    "REVIEW_STATUSES",
    "POSTING_STATUS",
    "cents",
    "text",
    "receipt_statuses",
]

MATCH_STATUSES = (
    "Fully Matched",
    "Partially Matched",
    "Unmatched",
    "Refund / Reversal",
    "Previously Reconciled",
)
REVIEW_STATUSES = (
    "Pending Review",
    "Approved",
    "Approved - Not Yet Applied",
    "Rejected",
    "More Evidence Requested",
    "Not Required",
)
POSTING_STATUS = "Not Posted (ERP/GL Not Integrated)"

_SOURCE_RECONCILED = {"reconciled", "matched", "closed"}
_REVIEW_BANK_STATUSES = {"pending review", "exception"}


def cents(value: object) -> int:
    """Money to integer cents; blank or unreadable is 0 (the controls report unreadable amounts)."""
    try:
        return money_to_cents(value)
    except Exception:
        return 0


def text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return normalize_text(value)


def _decision(value: object) -> str:
    raw = "_".join(text(value).upper().replace("-", " ").split())
    return "MORE_EVIDENCE" if raw in {"REQUEST_MORE_EVIDENCE", "MORE_EVIDENCE"} else raw


def _match_status(row: pd.Series) -> str:
    gross = cents(row.get("Gross Receipt Amount"))
    applied = cents(row.get("AR Applied Amount Total"))
    unapplied = cents(row.get("Unapplied Amount"))
    if text(row.get("Status")).lower() in _SOURCE_RECONCILED and applied <= 0:
        return "Previously Reconciled"
    if gross < 0:
        return "Refund / Reversal"
    if applied > 0 and unapplied > 0:
        return "Partially Matched"
    if applied > 0:
        return "Fully Matched"
    return "Unmatched"


def receipt_statuses(bank: pd.DataFrame, review: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    One row per bank receipt with its amounts (cents) and three statuses.

    The frame is the basis of every receipt metric, so a metric can never
    count a receipt twice.
    """
    reviews: dict[str, pd.Series] = {}
    if review is not None and not review.empty and "Bank Transaction ID" in review.columns:
        reviews = {normalize_key(r["Bank Transaction ID"]): r for _, r in review.iterrows()}

    rows = []
    for _, row in bank.iterrows():
        key = normalize_key(row.get("Bank Transaction ID"))
        match = _match_status(row)
        review_row = reviews.get(key)
        decision = _decision(review_row.get("Approval")) if review_row is not None else ""
        applied_flag = text(review_row.get("Applied To AR")) if review_row is not None else ""
        bank_status = text(row.get("Suggested Bank Status")).lower()
        if decision == "APPROVE":
            review_status = "Approved" if applied_flag == "Yes" else "Approved - Not Yet Applied"
        elif decision == "REJECT":
            review_status = "Rejected"
        elif decision == "MORE_EVIDENCE":
            review_status = "More Evidence Requested"
        elif match == "Previously Reconciled":
            review_status = "Not Required"
        elif bank_status in _REVIEW_BANK_STATUSES or match in {"Unmatched", "Refund / Reversal"}:
            review_status = "Pending Review"
        else:
            review_status = "Not Required"

        ai_applied = cents(row.get("AI-Resolved Applied Amount")) > 0 or text(row.get("Match Method")).startswith("AI Approved")
        if match in {"Fully Matched", "Partially Matched"}:
            settled_by = "AI resolution approved by reviewer" if ai_applied else "Deterministic rules"
        elif match == "Previously Reconciled":
            settled_by = "Source system"
        else:
            settled_by = ""

        gross = cents(row.get("Gross Receipt Amount"))
        rows.append({
            "key": key,
            "Bank Transaction ID": text(row.get("Bank Transaction ID")),
            "Value Date": row.get("Value Date"),
            "Customer Name": text(row.get("Customer Name")),
            "ERP Customer ID": text(row.get("ERP Customer ID")),
            "Currency": text(row.get("Currency")).upper(),
            "gross": gross,
            "fees": cents(row.get("Bank Fees")),
            "applied": cents(row.get("AR Applied Amount Total")),
            "unapplied": cents(row.get("Unapplied Amount")),
            "adjustment": cents(row.get("Fee / Rounding Adjustment")),
            "held": cents(row.get("Held For Review Amount")),
            "skipped": gross if match == "Previously Reconciled" else 0,
            "Match Status": match,
            "Review Status": review_status,
            "Posting Status": POSTING_STATUS,
            "Settled By": settled_by,
            "Auto Matched": match in {"Fully Matched", "Partially Matched"} and not ai_applied,
            "Match Method": text(row.get("Match Method")),
            "Engine Status": text(row.get("Suggested Bank Status")),
            "Memo": text(row.get("Memo / Remittance Detail")),
            "Memo Scenario": text(row.get("Memo Scenario")),
            "Memo Invoices": text(row.get("Extracted Invoice Numbers")),
            "Processing Notes": text(row.get("Processing Notes")),
            "Resolution ID": text(row.get("Resolution ID")),
            "Evidence Source": text(row.get("Evidence Source")) or ("Engine (bank + AR)" if match in {
                "Fully Matched", "Partially Matched"} else ""),
        })
    columns = [
        "key", "Bank Transaction ID", "Value Date", "Customer Name", "ERP Customer ID", "Currency",
        "gross", "fees", "applied", "unapplied", "adjustment", "held", "skipped",
        "Match Status", "Review Status", "Posting Status", "Settled By", "Auto Matched",
        "Match Method", "Engine Status", "Memo", "Memo Scenario", "Memo Invoices",
        "Processing Notes", "Resolution ID", "Evidence Source",
    ]
    return pd.DataFrame(rows, columns=columns)
