"""
Report metrics with their definitions - the single source of truth.

Every figure in the report comes from :func:`compute_metrics`, and every
metric has an entry in :data:`METRIC_DEFINITIONS` (business definition, data
source, formula, denominator, currency treatment, inclusion and exclusion
rules).  The Metric Definitions sheet and ``docs/metric_definitions.md`` are
rendered from that list.

Currency rule: amounts are only ever added within one currency.  A
consolidated total is produced only when an FX rate (with rate, date and
source) exists for every currency involved; otherwise it is ``None`` and the
report says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pandas as pd

from ar_reconciliation.reporting.config import ReportingConfig
from ar_reconciliation.reporting.statuses import MATCH_STATUSES, REVIEW_STATUSES, cents

__all__ = [
    "METRIC_DEFINITIONS",
    "RunMetrics",
    "ar_remaining",
    "compute_metrics",
    "consolidate",
    "rate",
]

IN_SCOPE_RULE = "In scope: every receipt except those already reconciled in the source before this run."

METRIC_DEFINITIONS: list[dict[str, str]] = [
    {
        "Metric": "Total Bank Receipts",
        "Business Definition": "Bank receipt lines in the bank file for this run.",
        "Data Source": "Processed Bank Receipts",
        "Calculation": "COUNT(receipts)",
        "Denominator": "n/a",
        "Currency Treatment": "Count; currency-independent. Amounts shown per currency.",
        "Inclusion / Exclusion": "All lines, including refunds, reversals and receipts held by integrity checks.",
    },
    {
        "Metric": "Automatically Matched Receipts",
        "Business Definition": "Receipts whose cash the deterministic engine applied to AR without a reviewer decision.",
        "Data Source": "Processed Bank Receipts (AR Applied Amount Total, Match Method)",
        "Calculation": "COUNT(receipts with applied cash > 0 and not applied by an approved AI resolution)",
        "Denominator": "n/a",
        "Currency Treatment": "Count; currency-independent.",
        "Inclusion / Exclusion": "Excludes AI-approved applications and previously reconciled receipts.",
    },
    {
        "Metric": "Fully Matched Receipts",
        "Business Definition": "Receipts whose cash is fully accounted for by AR application (and any fee / rounding adjustment), with nothing left unapplied.",
        "Data Source": "Processed Bank Receipts",
        "Calculation": "COUNT(applied > 0 and unapplied = 0)",
        "Denominator": "n/a",
        "Currency Treatment": "Count; currency-independent.",
        "Inclusion / Exclusion": "Receipt-level status. An invoice paid in part by a fully applied receipt stays open on the AR side (see Open AR Balance).",
    },
    {
        "Metric": "Partially Matched Receipts",
        "Business Definition": "Receipts with cash applied to AR and a remainder held as unapplied cash on account.",
        "Data Source": "Processed Bank Receipts",
        "Calculation": "COUNT(applied > 0 and unapplied > 0)",
        "Denominator": "n/a",
        "Currency Treatment": "Count; currency-independent.",
        "Inclusion / Exclusion": "Mutually exclusive with the other match statuses.",
    },
    {
        "Metric": "Unmatched Receipts",
        "Business Definition": "Positive receipts with no cash applied; the full amount is held for review.",
        "Data Source": "Processed Bank Receipts",
        "Calculation": "COUNT(gross > 0 and applied = 0 and not previously reconciled)",
        "Denominator": "n/a",
        "Currency Treatment": "Count; currency-independent.",
        "Inclusion / Exclusion": "Refunds / reversals (negative receipts) are counted separately.",
    },
    {
        "Metric": "Pending Review Receipts",
        "Business Definition": "Receipts that still need a reviewer decision.",
        "Data Source": "Processed Bank Receipts (Suggested Bank Status) and AI Exception Review (Approval)",
        "Calculation": "COUNT(Review Status = Pending Review)",
        "Denominator": "n/a",
        "Currency Treatment": "Count; currency-independent.",
        "Inclusion / Exclusion": "Independent of Match Status: a matched receipt flagged for confirmation is also pending review. Receipts with a recorded decision are excluded.",
    },
    {
        "Metric": "Invoices with Cash Applied",
        "Business Definition": "Distinct AR invoices that received cash in this run.",
        "Data Source": "Reconciled Open AR (Applied Amount)",
        "Calculation": "COUNT(DISTINCT invoices with applied amount > 0)",
        "Denominator": "n/a",
        "Currency Treatment": "Count; currency-independent.",
        "Inclusion / Exclusion": "An invoice paid by several receipts counts once (the engine's 'Matched AR invoice rows' counts applications).",
    },
    {
        "Metric": "Auto-Match Rate",
        "Business Definition": "Share of in-scope receipts the deterministic engine settled on its own.",
        "Data Source": "Processed Bank Receipts",
        "Calculation": "Automatically Matched Receipts / In-scope Receipts",
        "Denominator": "In-scope receipts. " + IN_SCOPE_RULE,
        "Currency Treatment": "Count-based ratio; currency-independent.",
        "Inclusion / Exclusion": "Refunds stay in the denominator (they are exceptions to resolve).",
    },
    {
        "Metric": "Cash Application Rate",
        "Business Definition": "Share of positive cash received that was applied to AR, per currency.",
        "Data Source": "Processed Bank Receipts",
        "Calculation": "Applied to AR / (Positive Gross Receipts - Previously Reconciled)",
        "Denominator": "Positive gross receipts in the currency, excluding receipts reconciled before this run.",
        "Currency Treatment": "Computed separately per currency; never combined.",
        "Inclusion / Exclusion": "Negative receipts (refunds, reversals) are excluded from numerator and denominator.",
    },
    {
        "Metric": "Exception Rate",
        "Business Definition": "Share of in-scope receipts with no cash applied (unmatched or refund / reversal).",
        "Data Source": "Processed Bank Receipts",
        "Calculation": "(Unmatched + Refund / Reversal receipts) / In-scope Receipts",
        "Denominator": "In-scope receipts. " + IN_SCOPE_RULE,
        "Currency Treatment": "Count-based ratio; currency-independent.",
        "Inclusion / Exclusion": "Matched receipts that are pending confirmation are not exceptions here; see Pending Review Receipts.",
    },
    {
        "Metric": "Cash Applied to AR",
        "Business Definition": "Cash applied to AR invoices in this run (engine plus approved AI resolutions).",
        "Data Source": "Processed Bank Receipts (AR Applied Amount Total)",
        "Calculation": "SUM(applied) per currency",
        "Denominator": "n/a",
        "Currency Treatment": "Per currency; consolidated only with FX rates for every currency.",
        "Inclusion / Exclusion": "Excludes non-cash adjustments (discounts, deductions).",
    },
    {
        "Metric": "Unapplied Cash",
        "Business Definition": "Cash received beyond the AR it settled (overpayments), held on account.",
        "Data Source": "Processed Bank Receipts (Unapplied Amount)",
        "Calculation": "SUM(unapplied) per currency",
        "Denominator": "n/a",
        "Currency Treatment": "Per currency.",
        "Inclusion / Exclusion": "Does not include receipts held for review (shown separately as Held for Review).",
    },
    {
        "Metric": "Held for Review",
        "Business Definition": "Positive receipts not applied at all, waiting for a decision.",
        "Data Source": "Processed Bank Receipts (Held For Review Amount)",
        "Calculation": "SUM(held) per currency for positive receipts",
        "Denominator": "n/a",
        "Currency Treatment": "Per currency.",
        "Inclusion / Exclusion": "Refunds / reversals are shown in their own column.",
    },
    {
        "Metric": "Open AR Balance (Outstanding AR)",
        "Business Definition": "AR still owed after this run's cash and approved non-cash adjustments.",
        "Data Source": "Reconciled Open AR",
        "Calculation": "SUM(MAX(Open Amount - Applied Amount - Approved Adjustment Amount, 0)) per currency",
        "Denominator": "n/a",
        "Currency Treatment": "Per invoice currency.",
        "Inclusion / Exclusion": "Fully settled invoices contribute 0. Customer credits (negative balances) would be reported separately; the sample has none.",
    },
    {
        "Metric": "Overdue AR / AR Over 90 Days",
        "Business Definition": "Open AR past its due date as of the Reporting As-of Date; the part more than 90 days past due.",
        "Data Source": "Reconciled Open AR (Due Date) and the Reporting As-of Date",
        "Calculation": "SUM(open balance where As-of Date - Due Date >= 1) ; >= 91",
        "Denominator": "Overdue AR % = Overdue AR / Open AR Balance, per currency.",
        "Currency Treatment": "Per currency.",
        "Inclusion / Exclusion": "Invoices without a due date are reported as Unknown Aging, never assumed.",
    },
]


def rate(numerator: int, denominator: int) -> float | None:
    """A ratio, or None when the denominator is zero (shown as N/A)."""
    if denominator == 0:
        return None
    return float(Decimal(numerator) / Decimal(denominator))


def consolidate(amounts_by_currency: dict[str, int], config: ReportingConfig) -> tuple[int | None, str]:
    """
    Convert per-currency cents into the reporting currency, if every rate exists.

    Returns (total cents or None, explanation).  Rounding is half-up to the
    cent, per currency, before adding.
    """
    target = config.reporting_currency
    if not target:
        return None, "No reporting currency configured; amounts are shown by original currency."
    missing = [c for c, v in amounts_by_currency.items() if v and c != target and c not in config.fx_rates]
    if missing:
        return None, f"No FX rate for {', '.join(sorted(missing))} ({config.fx_status}); no consolidated total."
    total = 0
    for currency, value in amounts_by_currency.items():
        if currency == target:
            total += value
        elif value:
            converted = Decimal(value) * config.fx_rates[currency].rate_to_reporting
            total += int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return total, f"Converted to {target} using the supplied FX rates."


def ar_remaining(ar: pd.DataFrame) -> pd.DataFrame:
    """Per AR row: opening, applied, adjustment and remaining balance in cents."""
    frame = pd.DataFrame(index=ar.index)
    frame["opening"] = ar["Open Amount"].apply(cents) if "Open Amount" in ar else 0
    frame["applied"] = ar["Applied Amount"].apply(cents) if "Applied Amount" in ar else 0
    frame["adjustment"] = (
        ar["Approved Adjustment Amount"].apply(cents) if "Approved Adjustment Amount" in ar else 0
    )
    raw = frame["opening"] - frame["applied"] - frame["adjustment"]
    frame["credit"] = raw.where(frame["opening"] < 0, 0)
    frame["remaining"] = raw.where(frame["opening"] < 0, raw.clip(lower=0)).where(frame["opening"] >= 0, 0)
    frame["Currency"] = ar["Currency"].astype(str).str.upper() if "Currency" in ar else ""
    return frame


@dataclass
class RunMetrics:
    total_receipts: int = 0
    in_scope_receipts: int = 0
    match_counts: dict[str, int] = field(default_factory=dict)
    review_counts: dict[str, int] = field(default_factory=dict)
    auto_matched: int = 0
    matched_receipts: int = 0
    invoices_with_cash: int = 0
    invoice_applications: int | None = None
    auto_match_rate: float | None = None
    exception_rate: float | None = None
    pending_review: int = 0
    cash_by_currency: pd.DataFrame = field(default_factory=pd.DataFrame)
    open_ar_by_currency: dict[str, int] = field(default_factory=dict)
    consolidated: dict[str, Any] = field(default_factory=dict)


def compute_metrics(
    statuses: pd.DataFrame,
    ar: pd.DataFrame,
    config: ReportingConfig,
    invoice_applications: int | None = None,
) -> RunMetrics:
    metrics = RunMetrics()
    metrics.total_receipts = len(statuses)
    metrics.match_counts = {s: int((statuses["Match Status"] == s).sum()) for s in MATCH_STATUSES}
    metrics.review_counts = {s: int((statuses["Review Status"] == s).sum()) for s in REVIEW_STATUSES}
    metrics.in_scope_receipts = metrics.total_receipts - metrics.match_counts["Previously Reconciled"]
    metrics.auto_matched = int(statuses["Auto Matched"].sum()) if len(statuses) else 0
    metrics.matched_receipts = metrics.match_counts["Fully Matched"] + metrics.match_counts["Partially Matched"]
    metrics.pending_review = metrics.review_counts["Pending Review"]
    metrics.auto_match_rate = rate(metrics.auto_matched, metrics.in_scope_receipts)
    metrics.exception_rate = rate(
        metrics.match_counts["Unmatched"] + metrics.match_counts["Refund / Reversal"],
        metrics.in_scope_receipts,
    )
    if not ar.empty and "Applied Amount" in ar:
        applied = ar["Applied Amount"].apply(cents)
        metrics.invoices_with_cash = int(ar.loc[applied > 0, "Invoice Number"].astype(str).nunique())
    metrics.invoice_applications = invoice_applications

    rows = []
    for currency, group in statuses.groupby("Currency", sort=True):
        positive = group[group["gross"] > 0]
        refunds = group[group["gross"] < 0]
        gross_positive = int(positive["gross"].sum())
        skipped = int(positive["skipped"].sum())
        applied = int(group["applied"].sum())
        rows.append({
            "Currency": currency,
            "Receipts": int(len(group)),
            "Gross Receipts": gross_positive,
            "Applied to AR": applied,
            "Unapplied Cash": int(group["unapplied"].sum()),
            "Fee / Rounding Adjustment": int(group["adjustment"].sum()),
            "Held for Review": int(positive["held"].sum()),
            "Previously Reconciled": skipped,
            "Refunds / Reversals": int(refunds["gross"].sum()),
            "Cash Application Rate": rate(applied, gross_positive - skipped),
        })
    metrics.cash_by_currency = pd.DataFrame(rows, columns=[
        "Currency", "Receipts", "Gross Receipts", "Applied to AR", "Unapplied Cash",
        "Fee / Rounding Adjustment", "Held for Review", "Previously Reconciled",
        "Refunds / Reversals", "Cash Application Rate",
    ])

    if not ar.empty:
        remaining = ar_remaining(ar)
        metrics.open_ar_by_currency = {
            c: int(v) for c, v in remaining.groupby("Currency")["remaining"].sum().items()
        }

    for label, column in (("Applied to AR", "Applied to AR"), ("Unapplied Cash", "Unapplied Cash")):
        amounts = dict(zip(metrics.cash_by_currency["Currency"], metrics.cash_by_currency[column]))
        metrics.consolidated[label] = consolidate(amounts, config)
    metrics.consolidated["Open AR Balance"] = consolidate(metrics.open_ar_by_currency, config)
    return metrics
