"""
AR aging and customer follow-up priority.

Aging uses the balance that remains AFTER this run (open amount minus cash
applied minus approved adjustments), the invoice Due Date and an explicit
Reporting As-of Date.  Fully settled invoices drop out; partially paid
invoices age only their remainder; an invoice without a due date is shown as
"Unknown (Missing Due Date)" - no payment terms are assumed.  Disputed or
held invoices stay in the aging and are also shown separately.

Customer risk is an **operational prioritization** for follow-up, computed
with transparent rules from ``reporting_config.json``.  It is not a credit
rating, and it uses no payment history (the project has none).  Cash already
received but not yet applied is an operational issue, reported in its own
column, not as credit risk.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from ar_reconciliation.core.primitives import normalize_key
from ar_reconciliation.reporting.config import ReportingConfig
from ar_reconciliation.reporting.metrics import ar_remaining
from ar_reconciliation.reporting.statuses import text

__all__ = ["UNKNOWN_BUCKET", "aging_detail", "aging_summary", "customer_risk"]

UNKNOWN_BUCKET = "Unknown (Missing Due Date)"
_DISPUTED = {"disputed", "on hold"}


def _bucket(days: int | None, buckets: list[dict]) -> str:
    if days is None:
        return UNKNOWN_BUCKET
    for bucket in buckets:
        low = bucket["min_days"]
        high = bucket["max_days"]
        if (low is None or days >= low) and (high is None or days <= high):
            return bucket["label"]
    return UNKNOWN_BUCKET


def aging_detail(ar: pd.DataFrame, as_of: date, config: ReportingConfig) -> pd.DataFrame:
    """One row per open invoice with its remaining balance, days past due and bucket."""
    columns = ["Customer Name", "ERP Customer ID", "Invoice Number", "Currency", "Due Date",
               "Days Past Due", "Aging Bucket", "remaining", "Disputed / On Hold"]
    if ar.empty:
        return pd.DataFrame(columns=columns)
    balances = ar_remaining(ar)
    rows = []
    for index, row in ar.iterrows():
        remaining = int(balances.at[index, "remaining"])
        if remaining <= 0:
            continue
        due = pd.to_datetime(row.get("Due Date"), errors="coerce")
        days = None if pd.isna(due) else (pd.Timestamp(as_of) - due.normalize()).days
        disputed = bool(text(row.get("Dispute Reason"))) or text(row.get("AR Status")).lower() in _DISPUTED or (
            text(row.get("Reconciliation Status")).lower() in _DISPUTED
        )
        rows.append({
            "Customer Name": text(row.get("Customer Name")),
            "ERP Customer ID": text(row.get("ERP Customer ID")),
            "Invoice Number": text(row.get("Invoice Number")),
            "Currency": text(row.get("Currency")).upper(),
            "Due Date": None if pd.isna(due) else due.date(),
            "Days Past Due": days,
            "Aging Bucket": _bucket(days, config.aging_buckets),
            "remaining": remaining,
            "Disputed / On Hold": "Yes" if disputed else "",
        })
    return pd.DataFrame(rows, columns=columns)


def aging_summary(detail: pd.DataFrame, config: ReportingConfig) -> pd.DataFrame:
    """Aging buckets by currency, in cents, with overdue totals."""
    labels = [b["label"] for b in config.aging_buckets] + [UNKNOWN_BUCKET]
    columns = ["Currency"] + labels + ["Total Outstanding AR", "Total Overdue AR", "Overdue AR %",
                                       "AR Over 90 Days", "Disputed / On Hold", "Open Invoices"]
    if detail.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for currency, group in detail.groupby("Currency", sort=True):
        row = {"Currency": currency}
        for label in labels:
            row[label] = int(group.loc[group["Aging Bucket"] == label, "remaining"].sum())
        total = int(group["remaining"].sum())
        known = group["Days Past Due"].notna()
        overdue = int(group.loc[known & (group["Days Past Due"].fillna(0) >= 1), "remaining"].sum())
        over_90 = int(group.loc[known & (group["Days Past Due"].fillna(0) >= 91), "remaining"].sum())
        row.update({
            "Total Outstanding AR": total,
            "Total Overdue AR": overdue,
            "Overdue AR %": (overdue / total) if total else None,
            "AR Over 90 Days": over_90,
            "Disputed / On Hold": int(group.loc[group["Disputed / On Hold"] == "Yes", "remaining"].sum()),
            "Open Invoices": int(len(group)),
        })
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def customer_risk(detail: pd.DataFrame, exceptions: pd.DataFrame, config: ReportingConfig) -> pd.DataFrame:
    """Follow-up priority per customer and currency, with the rule that set it."""
    columns = ["Customer Name", "ERP Customer ID", "Currency", "Open AR", "Overdue AR", "AR Over 90 Days",
               "Disputed / On Hold", "Open Payment Exceptions", "Unapplied / Held Cash (operational)",
               "Risk Level", "Risk Rule", "Recommended Action"]
    rules = config.get("customer_risk", {})
    high_90 = int(Decimal(str(rules.get("high_over_90_amount", 0))) * 100)
    high_ratio = float(rules.get("high_overdue_ratio", 1))
    medium_overdue = int(Decimal(str(rules.get("medium_overdue_amount", 0))) * 100)

    held: dict[tuple[str, str], tuple[int, int]] = {}
    if exceptions is not None and not exceptions.empty:
        open_items = exceptions[exceptions["Review Status"] != "Approved"]
        for (customer, currency), group in open_items.groupby(["Customer Name", "Currency"]):
            positive = group["unresolved_cents"].clip(lower=0)
            held[(normalize_key(customer), currency)] = (int(len(group)), int(positive.sum()))

    rows = []
    groups = detail.groupby(["Customer Name", "Currency"], sort=True) if not detail.empty else []
    seen: set[tuple[str, str]] = set()
    for (customer, currency), group in groups:
        key = (normalize_key(customer), currency)
        seen.add(key)
        open_ar = int(group["remaining"].sum())
        known = group["Days Past Due"].notna()
        overdue = int(group.loc[known & (group["Days Past Due"].fillna(0) >= 1), "remaining"].sum())
        over_90 = int(group.loc[known & (group["Days Past Due"].fillna(0) >= 91), "remaining"].sum())
        disputed = int(group.loc[group["Disputed / On Hold"] == "Yes", "remaining"].sum())
        count, held_cents = held.get(key, (0, 0))
        if over_90 >= high_90 and over_90 > 0:
            level, rule = "High", f"AR over 90 days >= {high_90 / 100:,.2f} {currency}"
        elif open_ar and overdue / open_ar >= high_ratio and overdue > 0:
            level, rule = "High", f"Overdue share >= {high_ratio:.0%} of open AR"
        elif disputed > 0:
            level, rule = "Medium", "Disputed or held invoices"
        elif overdue >= medium_overdue and overdue > 0:
            level, rule = "Medium", "Overdue balance"
        else:
            level, rule = "Low", "Not overdue"
        if level == "High":
            action = "Escalate collection follow-up; confirm payment plan with the customer."
        elif disputed > 0:
            action = "Resolve the dispute or hold before requesting payment."
        elif level == "Medium":
            action = "Send a past-due reminder."
        else:
            action = "No action; monitor."
        if count:
            action += f" Apply or resolve {count} receipt(s) already received before contacting the customer."
        rows.append({
            "Customer Name": customer,
            "ERP Customer ID": ", ".join(sorted(set(group["ERP Customer ID"]) - {""})),
            "Currency": currency,
            "Open AR": open_ar,
            "Overdue AR": overdue,
            "AR Over 90 Days": over_90,
            "Disputed / On Hold": disputed,
            "Open Payment Exceptions": count,
            "Unapplied / Held Cash (operational)": held_cents,
            "Risk Level": level,
            "Risk Rule": rule,
            "Recommended Action": action,
        })
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    order = {"High": 0, "Medium": 1, "Low": 2}
    return frame.assign(_o=frame["Risk Level"].map(order)).sort_values(
        ["_o", "AR Over 90 Days", "Overdue AR", "Open AR"], ascending=[True, False, False, False]
    ).drop(columns="_o").reset_index(drop=True)
