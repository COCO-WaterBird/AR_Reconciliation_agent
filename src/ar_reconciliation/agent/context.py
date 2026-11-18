"""
The analysis context for one unmatched receipt.

The agent is given a small, bounded picture of the receipt and the invoices
it could plausibly belong to.  Building that picture is deliberately
separate from the reasoning about it, so the boundaries of what the agent
may see are explicit and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from ar_reconciliation.core.primitives import (
    format_money,
    money_to_cents,
    normalize_key,
    normalize_text,
)
from ar_reconciliation.core.schema import (
    AR_APPLIED_AMOUNT_COL,
    AR_CURRENCY_COL,
    AR_CUSTOMER_ID_COL,
    AR_CUSTOMER_NAME_COL,
    AR_DISPUTE_REASON_COL,
    AR_DUE_DATE_COL,
    AR_INVOICE_COL,
    AR_INVOICE_DATE_COL,
    AR_OPEN_AMOUNT_COL,
    AR_RECON_STATUS_COL,
    AR_STATUS_COL,
    BANK_CURRENCY_COL,
    BANK_CUSTOMER_ID_COL,
    BANK_CUSTOMER_NAME_COL,
    BANK_FEES_COL,
    BANK_GROSS_AMOUNT_COL,
    BANK_MEMO_COL,
    BANK_NET_AMOUNT_COL,
    BANK_REMITTER_NAME_COL,
    BANK_TRANSACTION_ID_COL,
    BANK_VALUE_DATE_COL,
)
from ar_reconciliation.rules.policies import (
    MAX_CANDIDATES_IN_PROMPT,
    MAX_PARTIAL_RESIDUAL_RATIO,
)

__all__ = [
    "Candidate",
    "ExceptionContext",
    "Recommendation",
    "build_candidate_pool",
    "score_candidates",
    "build_context",
    "render_context",
]


@dataclass
class Candidate:
    """One open AR invoice considered as a possible match."""

    invoice_number: str
    customer_id: str
    customer_name: str
    currency: str
    open_amount_cents: int
    due_date: str
    invoice_date: str
    dispute_reason: str
    residual_cents: int
    score: float = 0.0


@dataclass
class ExceptionContext:
    """Everything the agent needs to reason about one unmatched receipt."""

    bank_transaction_id: str
    value_date: str
    customer_name: str
    remitter_name: str
    customer_id: str
    currency: str
    gross_amount_cents: int
    bank_fee_cents: int
    net_amount_cents: int
    memo: str
    memo_scenario: str
    extracted_invoices: str
    engine_reason: str
    candidates: list[Candidate]


@dataclass
class Recommendation:
    """The agent's structured judgement for one receipt."""

    bank_transaction_id: str
    exception_type: str
    root_cause: str
    recommended_action: str
    confidence: str
    proposed_invoices: list[str]
    proposed_amount_cents: int
    proposed_residual_cents: int
    accounting_treatment: str
    gl_posting: str
    explanation: str
    analysed_by: str
    candidates_considered: list[str] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)


def build_candidate_pool(
    ar_df: pd.DataFrame,
    bank_row: pd.Series,
    excluded_invoice_numbers: Iterable[str] = (),
) -> list[Candidate]:
    """
    Build the set of open AR invoices the agent is allowed to consider.

    The pool is deliberately wider than the deterministic engine's pool: the
    agent's job is to look for a match the rules could not prove.  It is
    limited to the same currency and to the same customer (or an unambiguous
    customer-name match), which is the hard boundary the agent may not cross
    without human intervention.

    The amount used for matching is the REMAINING balance (original open
    amount minus what has already been applied), so an invoice that was
    partly settled by an earlier receipt is matched against its remainder.
    """
    excluded = {
        normalize_text(v).upper() for v in excluded_invoice_numbers if normalize_text(v)
    }

    bank_customer_id = normalize_text(bank_row.get(BANK_CUSTOMER_ID_COL, ""))
    bank_currency = normalize_text(bank_row.get(BANK_CURRENCY_COL, "")).upper()

    ar_customer_key = ar_df[AR_CUSTOMER_ID_COL].apply(normalize_key)
    ar_customer_name_key = ar_df[AR_CUSTOMER_NAME_COL].apply(normalize_key)

    if bank_customer_id:
        allowed_keys = {normalize_key(bank_customer_id)}
    else:
        # Fall back to the customer / remitter name, but only when it resolves
        # to exactly one ERP customer.  An ambiguous name yields an empty pool
        # and the receipt is escalated instead of guessed.
        names = {
            normalize_key(bank_row.get(BANK_CUSTOMER_NAME_COL, "")),
            normalize_key(bank_row.get(BANK_REMITTER_NAME_COL, "")),
        }
        names = {n for n in names if n}
        allowed_keys = set()
        for name in names:
            resolved = {
                normalize_key(v)
                for v in ar_df.loc[ar_customer_name_key == name, AR_CUSTOMER_ID_COL]
                if normalize_key(v)
            }
            if len(resolved) == 1:
                allowed_keys |= resolved
                break

    if not allowed_keys:
        return []

    ar_currency = ar_df[AR_CURRENCY_COL].apply(lambda v: normalize_text(v).upper())

    mask = (
        ar_customer_key.isin(allowed_keys)
        & (ar_currency == bank_currency)
        & (ar_df[AR_OPEN_AMOUNT_COL].apply(money_to_cents) > 0)
        & (
            ~ar_df[AR_RECON_STATUS_COL]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"reconciled", "matched", "closed"})
        )
        & (
            ~ar_df[AR_STATUS_COL]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"closed", "paid", "cancelled", "void", "written off"})
        )
    )

    target_cents = money_to_cents(bank_row.get(BANK_NET_AMOUNT_COL, ""))
    gross_cents = money_to_cents(bank_row.get(BANK_GROSS_AMOUNT_COL, ""))

    # A refund or chargeback carries a negative amount.  Its absolute value is
    # the amount that would be reversed, which is what the candidate ranking
    # has to be compared against.
    compare_cents = target_cents if target_cents > 0 else abs(target_cents)
    compare_gross = gross_cents if gross_cents > 0 else abs(gross_cents)

    candidates: list[Candidate] = []
    for _, row in ar_df.loc[mask].iterrows():
        invoice_number = normalize_text(row.get(AR_INVOICE_COL, ""))
        if invoice_number.upper() in excluded:
            continue
        original_open = money_to_cents(row.get(AR_OPEN_AMOUNT_COL, ""))
        already_applied = money_to_cents(row.get(AR_APPLIED_AMOUNT_COL, 0))
        open_cents = max(original_open - already_applied, 0)
        if open_cents <= 0:
            # Already fully settled by the deterministic engine.
            continue
        residual = min(
            abs(open_cents - compare_cents), abs(open_cents - compare_gross)
        )
        candidates.append(
            Candidate(
                invoice_number=invoice_number,
                customer_id=normalize_text(row.get(AR_CUSTOMER_ID_COL, "")),
                customer_name=normalize_text(row.get(AR_CUSTOMER_NAME_COL, "")),
                currency=normalize_text(row.get(AR_CURRENCY_COL, "")),
                open_amount_cents=open_cents,
                due_date=_parse_iso_date(row.get(AR_DUE_DATE_COL, "")),
                invoice_date=_parse_iso_date(row.get(AR_INVOICE_DATE_COL, "")),
                dispute_reason=normalize_text(row.get(AR_DISPUTE_REASON_COL, "")),
                residual_cents=residual,
            )
        )

    candidates.sort(key=lambda c: (c.residual_cents, c.invoice_number))
    return candidates


def score_candidates(
    candidates: list[Candidate],
    context_invoice_numbers: list[str],
    target_cents: int,
) -> list[Candidate]:
    """
    Rank candidates with a transparent, deterministic heuristic.

    The score is only used to order and to explain the ranking; it never
    decides the final outcome on its own.
    """
    referenced = {
        normalize_text(v).upper() for v in context_invoice_numbers if normalize_text(v)
    }

    for cand in candidates:
        score = 0.0

        if cand.invoice_number.upper() in referenced:
            score += 100.0

        if cand.residual_cents == 0:
            score += 60.0
        else:
            ratio = (
                cand.residual_cents / cand.open_amount_cents
                if cand.open_amount_cents
                else 1.0
            )
            if ratio <= MAX_PARTIAL_RESIDUAL_RATIO:
                score += 35.0
            else:
                score += max(0.0, 20.0 - ratio * 20.0)

        if cand.dispute_reason:
            score -= 15.0

        cand.score = round(score, 3)

    candidates.sort(key=lambda c: (-c.score, c.residual_cents, c.invoice_number))
    return candidates


def build_context(
    bank_row: pd.Series,
    unmatched_row: pd.Series,
    ar_df: pd.DataFrame,
    already_matched_invoices: set[str],
) -> ExceptionContext:
    """Assemble the full analysis context for one unmatched receipt."""
    invoice_numbers = [
        v.strip()
        for v in normalize_text(unmatched_row.get("Extracted Invoice Numbers", "")).split(",")
        if v.strip()
    ]

    excluded = set(already_matched_invoices)
    candidates = build_candidate_pool(
        ar_df, bank_row, excluded_invoice_numbers=excluded
    )
    candidates = score_candidates(
        candidates, invoice_numbers, money_to_cents(bank_row.get(BANK_NET_AMOUNT_COL, ""))
    )
    candidates = candidates[:MAX_CANDIDATES_IN_PROMPT]

    return ExceptionContext(
        bank_transaction_id=normalize_text(bank_row.get(BANK_TRANSACTION_ID_COL, "")),
        value_date=_parse_iso_date(bank_row.get(BANK_VALUE_DATE_COL, "")),
        customer_name=normalize_text(bank_row.get(BANK_CUSTOMER_NAME_COL, "")),
        remitter_name=normalize_text(bank_row.get(BANK_REMITTER_NAME_COL, "")),
        customer_id=normalize_text(bank_row.get(BANK_CUSTOMER_ID_COL, "")),
        currency=normalize_text(bank_row.get(BANK_CURRENCY_COL, "")),
        gross_amount_cents=money_to_cents(bank_row.get(BANK_GROSS_AMOUNT_COL, "")),
        bank_fee_cents=money_to_cents(bank_row.get(BANK_FEES_COL, "")),
        net_amount_cents=money_to_cents(bank_row.get(BANK_NET_AMOUNT_COL, "")),
        memo=normalize_text(bank_row.get(BANK_MEMO_COL, "")),
        memo_scenario=normalize_text(unmatched_row.get("Memo Scenario", "")),
        extracted_invoices=normalize_text(
            unmatched_row.get("Extracted Invoice Numbers", "")
        ),
        engine_reason=normalize_text(unmatched_row.get("Reason", "")),
        candidates=candidates,
    )


def render_context(context: ExceptionContext) -> str:
    """Render the receipt and its candidates as a compact text block."""
    lines: list[str] = []
    lines.append("BANK RECEIPT")
    lines.append(f"  Bank Transaction ID : {context.bank_transaction_id}")
    lines.append(f"  Value Date          : {context.value_date}")
    lines.append(f"  ERP Customer ID     : {context.customer_id or '(missing from bank feed)'}")
    lines.append(f"  Customer Name       : {context.customer_name}")
    if context.remitter_name and context.remitter_name != context.customer_name:
        lines.append(f"  Remitter Name       : {context.remitter_name}")
    lines.append(f"  Currency            : {context.currency}")
    lines.append(f"  Gross Amount        : {format_money(context.gross_amount_cents)}")
    lines.append(f"  Bank Fees           : {format_money(context.bank_fee_cents)}")
    lines.append(f"  Net Amount          : {format_money(context.net_amount_cents)}")
    lines.append(f"  Remittance Memo     : {context.memo or '(empty)'}")
    lines.append(f"  Engine Scenario     : {context.memo_scenario or '(none)'}")
    lines.append(f"  Engine Reason       : {context.engine_reason}")
    if context.extracted_invoices:
        lines.append(f"  Invoice Numbers Named in Memo: {context.extracted_invoices}")

    lines.append("")
    lines.append("CANDIDATE OPEN AR INVOICES (same customer and currency)")
    if not context.candidates:
        lines.append("  (none - no open AR invoice exists for this customer and currency)")
    else:
        for cand in context.candidates:
            lines.append(
                f"  - {cand.invoice_number} | open {format_money(cand.open_amount_cents)} {cand.currency}"
                f" | invoice date {cand.invoice_date or 'n/a'} | due {cand.due_date or 'n/a'}"
                f" | difference {format_money(cand.residual_cents)}"
                + (f" | dispute: {cand.dispute_reason}" if cand.dispute_reason else "")
            )
    return "\n".join(lines)


def _parse_iso_date(value: object) -> str:
    """Render a date cell as an ISO date string."""
    text = _as_text(value)
    if not text:
        return ""
    try:
        return pd.to_datetime(text).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return text


def _as_text(value: object) -> str:
    """Format a cell value for inclusion in a prompt (dates stay readable)."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()
