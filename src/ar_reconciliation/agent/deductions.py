"""
Reviewer decisions on deductions proposed from structured remittance advice.

The engine applies the cash a remittance states and writes each deduction to
the Proposed Deductions sheet - it never applies one (decision G1).  A
reviewer then decides:

* APPROVE an **Authorized** deduction (allowed by the contract terms) - it is
  written to the invoice as a non-cash adjustment ("Approved Adjustment
  Amount"), once, and recorded in the approval records and the audit trail;
* APPROVE an **Unauthorized** deduction - refused: no contract term supports
  it, so it cannot become an adjustment (E1); the balance stays open for a
  dispute or collection;
* REJECT - nothing changes; the balance stays open.

The AR roll-forward (opening - cash - approved adjustments = remaining)
covers the result, so the controls still tie.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ar_reconciliation.core.primitives import cents_to_money, money_to_cents, normalize_key, normalize_text
from ar_reconciliation.core.schema import (
    OUT_APPROVED_ADJUSTMENT_COL,
    OUT_APPROVED_ADJUSTMENT_TYPE_COL,
)

__all__ = ["DEDUCTIONS_SHEET", "apply_deduction_decisions", "record_deduction_decision"]

DEDUCTIONS_SHEET = "Proposed Deductions"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else normalize_text(value)


def record_deduction_decision(result_path: Path, deduction_id: str, decision: str, reviewer: str,
                              notes: str = "") -> None:
    decision = decision.strip().upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("A deduction decision must be APPROVE or REJECT.")
    if not normalize_text(reviewer):
        raise ValueError("A reviewer name is required to record a decision.")
    frame = pd.read_excel(result_path, sheet_name=DEDUCTIONS_SHEET).astype(object)
    mask = frame["Deduction ID"].astype(str) == deduction_id
    if mask.sum() != 1:
        raise ValueError(f"{deduction_id} is not exactly once in the {DEDUCTIONS_SHEET} sheet.")
    if decision == "APPROVE" and _text(frame.loc[mask, "Authorization"].iloc[0]) != "Authorized":
        raise ValueError("An unauthorized deduction cannot be approved as an adjustment; resolve it as a dispute.")
    frame.loc[mask, "Decision"] = decision
    frame.loc[mask, "Reviewer"] = normalize_text(reviewer)
    frame.loc[mask, "Decision Timestamp"] = _now()
    frame.loc[mask, "Reviewer Notes"] = normalize_text(notes)
    from ar_reconciliation.agent.workflow import _write_sheets

    _write_sheets(Path(result_path), {DEDUCTIONS_SHEET: frame})


def apply_deduction_decisions(
    ar_df: pd.DataFrame,
    bank_df: pd.DataFrame,
    deductions: pd.DataFrame,
    reviewer: str,
    records: list[dict],
    audit: list[dict],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict]]:
    log: list[dict] = []
    if deductions is None or deductions.empty:
        return ar_df, bank_df, deductions, log
    ar = ar_df.copy()
    bank = bank_df.copy()
    deductions = deductions.copy().astype(object)
    for frame, columns in ((ar, [OUT_APPROVED_ADJUSTMENT_COL, OUT_APPROVED_ADJUSTMENT_TYPE_COL]),):
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.Series([""] * len(frame), index=frame.index, dtype=object)
            frame[column] = frame[column].astype(object).where(frame[column].notna(), "")
    for column in ("Residual Amount", "Suggested Recon Status", "Reconciliation Notes"):
        if column in ar.columns:
            ar[column] = ar[column].astype(object)
    if "Suggested Bank Status" in bank.columns:
        bank["Suggested Bank Status"] = bank["Suggested Bank Status"].astype(object)

    def record(row, outcome, detail):
        log.append({"Bank Transaction ID": _text(row["Bank Transaction ID"]), "Outcome": outcome, "Detail": detail})
        records.append({
            "Bank Transaction ID": _text(row["Bank Transaction ID"]), "Resolution ID": _text(row["Deduction ID"]),
            "Decision": f"{_text(row['Decision'])} (deduction)", "Reviewer": _text(row["Reviewer"]) or reviewer,
            "Decision Timestamp": _text(row["Decision Timestamp"]) or _now(),
            "Reviewer Notes": _text(row["Reviewer Notes"]),
            "Approved Allocations": "", "Approved Adjustments": f"{_text(row['Invoice Number'])}: "
            f"{_text(row['Deduction Code'])} {money_to_cents(row['Deduction Amount']) / 100:,.2f}",
            "Outcome": outcome, "Detail": detail, "Processed At": _now(),
        })

    for index, row in deductions.iterrows():
        decision = _text(row.get("Decision")).upper()
        if not decision or _text(row.get("Applied")) == "Yes":
            continue
        if decision == "REJECT":
            record(row, "Not applied", "Deduction rejected by the reviewer; the balance stays open for collection.")
            deductions.at[index, "Applied"] = "Rejected"
            continue
        if decision != "APPROVE":
            continue
        if _text(row.get("Authorization")) != "Authorized":
            record(row, "Rejected", "Unauthorized deduction: no contract term supports it, so it cannot become an "
                                    "adjustment. Resolve it as a dispute.")
            continue
        invoice = normalize_key(row["Invoice Number"])
        rows = ar.index[ar["Invoice Number"].apply(normalize_key) == invoice]
        if len(rows) != 1:
            record(row, "Rejected", "The invoice does not resolve to exactly one AR row.")
            continue
        idx = rows[0]
        amount = money_to_cents(row["Deduction Amount"])
        opening = money_to_cents(ar.at[idx, "Open Amount"])
        applied = money_to_cents(ar.at[idx, "Applied Amount"] if "Applied Amount" in ar else 0)
        adjusted = money_to_cents(ar.at[idx, OUT_APPROVED_ADJUSTMENT_COL])
        remaining = max(opening - applied - adjusted, 0)
        if amount <= 0 or amount > remaining:
            record(row, "Rejected", f"The deduction exceeds the invoice's open balance ({remaining / 100:,.2f}).")
            continue
        after = remaining - amount
        ar.at[idx, OUT_APPROVED_ADJUSTMENT_COL] = cents_to_money(adjusted + amount)
        types = [t for t in [_text(ar.at[idx, OUT_APPROVED_ADJUSTMENT_TYPE_COL]), "Customer Deduction"] if t]
        ar.at[idx, OUT_APPROVED_ADJUSTMENT_TYPE_COL] = ", ".join(dict.fromkeys(types))
        if "Residual Amount" in ar.columns:
            ar.at[idx, "Residual Amount"] = cents_to_money(after)
        if "Suggested Recon Status" in ar.columns:
            ar.at[idx, "Suggested Recon Status"] = "Reconciled" if after == 0 else "Pending Review"
        if "Reconciliation Notes" in ar.columns:
            ar.at[idx, "Reconciliation Notes"] = (
                f"{_text(ar.at[idx, 'Reconciliation Notes'])} Approved deduction {_text(row['Deduction ID'])} "
                f"({_text(row['Deduction Code'])}) {amount / 100:,.2f}."
            ).strip()
        deductions.at[index, "Applied"] = "Yes"
        audit.append({
            "Timestamp": _now(), "Event": "Deduction approved", "Bank Transaction ID": _text(row["Bank Transaction ID"]),
            "Resolution ID": _text(row["Deduction ID"]), "Invoice Number": _text(row["Invoice Number"]),
            "Customer ID": _text(ar.at[idx, "ERP Customer ID"]), "Open Before": cents_to_money(remaining),
            "Cash Applied": 0.0, "Adjustment Amount": cents_to_money(amount),
            "Adjustment Type": f"Customer Deduction ({_text(row['Deduction Code'])})", "Open After": cents_to_money(after),
            "Supporting Documents": _text(row.get("Source")), "Reviewer": _text(row["Reviewer"]) or reviewer,
            "Detail": _text(row.get("Authorization Rule")),
        })
        record(row, "Applied", f"Deduction applied as a non-cash adjustment of {amount / 100:,.2f}.")
        # The receipt is settled once every deduction it carried is resolved.
        txn = normalize_key(row["Bank Transaction ID"])
        own = deductions[deductions["Bank Transaction ID"].apply(normalize_key) == txn]
        if (own["Applied"].astype(str) == "Yes").all() and "Suggested Bank Status" in bank.columns:
            b = bank.index[bank["Bank Transaction ID"].apply(normalize_key) == txn]
            if len(b) == 1 and money_to_cents(bank.at[b[0], "Unapplied Amount"] if "Unapplied Amount" in bank else 0) == 0:
                bank.at[b[0], "Suggested Bank Status"] = "Reconciled"
    return ar, bank, deductions, log
