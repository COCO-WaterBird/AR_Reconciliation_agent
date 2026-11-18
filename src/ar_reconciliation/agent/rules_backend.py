"""
The offline rules backend.

This is the fallback used when no LLM backend is configured.  It applies the
same conservative policy as the LLM prompt:

  * a receipt the engine flagged as review-only is never auto-matched;
  * a receipt whose amount matches several invoices equally is never
    auto-matched;
  * a receipt with no cash to apply (refund, chargeback, on-account) is
    escalated rather than applied.

It runs with no API key and no network, so the agent is always available.
"""

from __future__ import annotations

from typing import Any

from ar_reconciliation.agent.context import ExceptionContext
from ar_reconciliation.core.primitives import cents_to_money, format_money
from ar_reconciliation.rules.policies import (
    AGENT_REVIEW_ONLY_SCENARIOS,
    HOLD_ONLY_SCENARIOS,
    MAX_PARTIAL_RESIDUAL_RATIO,
    SCENARIO_TO_EXCEPTION_TYPE,
)

__all__ = [
    "rules_backend_analyse",
    "build_root_cause",
    "build_accounting_treatment",
    "build_gl_posting",
    "build_explanation",
    "proposed_invoices_available",
]


def rules_backend_analyse(context: ExceptionContext) -> dict[str, Any]:
    """Analyse one receipt with the deterministic rules backend."""
    scenario = context.memo_scenario
    exception_type = SCENARIO_TO_EXCEPTION_TYPE.get(scenario, "Unknown")

    memo_lower = context.memo.lower()
    if "duplicate" in memo_lower:
        exception_type = "Duplicate Payment"
    elif (
        "short" in memo_lower
        or "less " in memo_lower
        or "deduct" in memo_lower
    ) and exception_type == "Unknown":
        exception_type = "Short Payment"

    top = context.candidates[0] if context.candidates else None
    runner_up = context.candidates[1] if len(context.candidates) > 1 else None

    proposed_invoices: list[str] = []
    proposed_amount = 0
    proposed_residual = 0
    confidence = "Low"
    action = "Manual Review"

    # --- Evidence for a specific invoice ---
    if top is not None and context.net_amount_cents > 0:
        gap = top.residual_cents
        ratio = gap / top.open_amount_cents if top.open_amount_cents else 1.0
        named_in_memo = top.invoice_number.upper() in {
            v.upper() for v in context.extracted_invoices.split(",") if v.strip()
        }

        # A rival candidate that is equally close makes the receipt ambiguous.
        # Equal amounts on two invoices of the same customer are a real
        # business case (S12), so the tie must break the recommendation.
        ambiguous_tie = runner_up is not None and runner_up.residual_cents == gap

        if gap == 0 and not ambiguous_tie:
            proposed_invoices = [top.invoice_number]
            proposed_amount = min(top.open_amount_cents, context.net_amount_cents)
            proposed_residual = max(top.open_amount_cents - proposed_amount, 0)
            confidence = "High" if named_in_memo else "Medium"
            action = "Auto Match" if confidence == "High" else "Recommend Match"
        elif ratio <= MAX_PARTIAL_RESIDUAL_RATIO and not ambiguous_tie:
            proposed_invoices = [top.invoice_number]
            proposed_amount = min(top.open_amount_cents, context.net_amount_cents)
            proposed_residual = max(top.open_amount_cents - proposed_amount, 0)
            confidence = "Medium"
            action = "Recommend Match"

    # --- Scenario policy ---
    if scenario in HOLD_ONLY_SCENARIOS:
        action = "Hold / Escalate"
        confidence = "Medium" if proposed_invoices else "Low"
    elif scenario in AGENT_REVIEW_ONLY_SCENARIOS:
        # The engine deliberately refused to auto-reconcile these.  The agent
        # may recommend, but it may not overturn that decision.
        if action == "Auto Match":
            action = "Recommend Match"
            confidence = "Medium" if confidence == "High" else confidence
    elif scenario == "Missing ERP Customer ID":
        action = "Return to Customer" if not proposed_invoices else "Recommend Match"

    if exception_type == "Unknown" and not proposed_invoices:
        action = "Manual Review"

    if not proposed_invoices:
        confidence = "Low"
        if action not in {"Hold / Escalate", "Return to Customer"}:
            action = "Manual Review"

    root_cause = build_root_cause(context, exception_type)
    accounting_treatment = build_accounting_treatment(
        context, exception_type, proposed_residual
    )
    gl_posting = build_gl_posting(context, exception_type, proposed_invoices)
    explanation = build_explanation(
        context,
        exception_type,
        proposed_invoices,
        proposed_amount,
        proposed_residual,
        confidence,
    )

    return {
        "exception_type": exception_type,
        "root_cause": root_cause,
        "recommended_action": action,
        "confidence": confidence,
        "proposed_invoices": proposed_invoices,
        "proposed_amount": cents_to_money(proposed_amount),
        "proposed_residual": cents_to_money(proposed_residual),
        "accounting_treatment": accounting_treatment,
        "gl_posting": gl_posting,
        "explanation": explanation,
    }


def build_root_cause(context: ExceptionContext, exception_type: str) -> str:
    """Explain why the receipt did not match."""
    if exception_type == "Short Payment":
        return (
            f"The payer settled {format_money(context.net_amount_cents)} against an open balance "
            f"that is higher; the difference is a deduction or a deliberate partial settlement."
        )
    if exception_type == "Overpayment":
        return (
            f"The receipt of {format_money(context.net_amount_cents)} exceeds the open balance "
            f"of the referenced invoice, which usually means an advance or a duplicated payment."
        )
    if exception_type == "Bank Fee":
        return (
            f"The bank deducted {format_money(context.bank_fee_cents)} of fees. The invoice is "
            f"cleared by the net cash received, and the fee is a bank charge, not an AR item."
        )
    if exception_type == "Duplicate Payment":
        return (
            "An earlier receipt with the same customer, amount and invoice reference was already "
            "applied, so this receipt is a suspected duplicate payment."
        )
    if exception_type == "Missing Customer":
        return (
            "The bank feed carries no ERP Customer ID and the payer name does not resolve to a "
            "single ERP customer, so the receipt cannot be attributed to an open balance."
        )
    if exception_type == "On-Account / Unapplied":
        return (
            "The remittance advice gives no invoice reference, so the cash cannot be tied to a "
            "specific open item and must be held on account."
        )
    if exception_type == "FX Difference":
        return (
            "The receipt currency or amount does not agree with the open invoice; the difference "
            "needs to be identified as an FX gain or loss."
        )
    if exception_type == "Wrong Legal Entity":
        return (
            "The receipt was paid by a different legal entity than the one holding the open "
            "balance, so it cannot clear that invoice directly."
        )
    if exception_type == "Ambiguous Invoice Reference":
        return (
            "The memo names an invoice that is not open, or several invoices match the reference, "
            "so the intended target cannot be proven."
        )
    if exception_type in {"Refund", "Chargeback"}:
        return (
            "The receipt reverses money that was previously applied, so it must be handled as a "
            "refund or a chargeback rather than as a cash application."
        )
    if exception_type == "Credit Memo":
        return "The receipt settles an amount that was offset by a credit memo, not by an invoice."
    if exception_type == "Dispute Payment":
        return (
            "The payer has withheld part of the invoice because of an open dispute, so the "
            "payment is short pending the dispute resolution."
        )
    if exception_type == "Tax Withholding":
        return (
            "The payment is net of a withholding tax deduction, so the invoice is cleared at the "
            "net amount and the withheld tax must be reported."
        )
    if exception_type == "Future-Dated Receipt":
        return "The receipt is dated after the invoice due date, so it belongs to a later period."
    if exception_type == "Unexpected Bank Account":
        return "The receipt landed in a bank account that is not part of the reconciled account set."
    if exception_type == "Collector Hold":
        return "The customer is on a collections hold, so cash application is blocked pending review."
    if exception_type == "Data Quality Issue":
        return "The source data is inconsistent, so no reliable match can be computed."

    if not context.candidates:
        return (
            f"No open AR invoice exists for customer "
            f"{context.customer_id or context.customer_name} in {context.currency}, so there is "
            f"nothing for the receipt to clear."
        )
    return (
        "The receipt amount does not equal any open invoice amount, nor any combination of open "
        "invoices for this customer and currency."
    )


def build_accounting_treatment(
    context: ExceptionContext, exception_type: str, proposed_residual_cents: int
) -> str:
    """Say how the accounting should be handled."""
    if exception_type in {"Refund", "Chargeback", "Credit Memo"}:
        return (
            "Do not apply to AR. Reverse the original application and book the reversal, "
            "re-opening the invoice if it was cleared."
        )
    if exception_type == "Bank Fee":
        return (
            "Clear the invoice at the net cash received and book the bank fee separately as a "
            "bank charge; it must not be posted against the customer balance."
        )
    if exception_type == "On-Account / Unapplied":
        return (
            "Suspend the cash on the customer unapplied-cash account until remittance detail "
            "identifies the invoices."
        )
    if exception_type == "Collector Hold":
        return (
            "Do not apply while the collections hold is active. Park the cash as unapplied, clear "
            "the hold with the credit team, and apply it only after the hold is released."
        )
    if exception_type == "Wrong Legal Entity":
        return (
            "Do not apply to the named invoice. Route the cash to the legal entity that holds the "
            "balance, or request a corrected remittance advice."
        )
    if exception_type == "Tax Withholding":
        return (
            "Clear the invoice at the net cash received and report the withheld tax separately so "
            "the tax authority remittance can be filed."
        )
    if exception_type == "Dispute Payment":
        return (
            "Apply the cash as a short payment and leave the disputed amount open, but record the "
            "dispute against the invoice so it is not aged as an ordinary receivable."
        )
    if proposed_residual_cents > 0:
        return (
            f"Apply the cash to the invoice and leave {format_money(proposed_residual_cents)} "
            f"open, subject to the dispute or deduction being agreed with the customer."
        )
    if proposed_invoices_available(context):
        return (
            "Apply the cash to the invoice(s) in full once a reviewer has approved the "
            "recommendation."
        )
    return (
        "No cash application until the exception is resolved. Hold the receipt in the exception "
        "queue and assign it to an owner."
    )


def proposed_invoices_available(context: ExceptionContext) -> bool:
    """True when the context carries at least one candidate invoice."""
    return bool(context.candidates)


def build_gl_posting(
    context: ExceptionContext, exception_type: str, proposed_invoices: list[str]
) -> str:
    """Describe the general-ledger posting that would be proposed."""
    if exception_type == "Bank Fee":
        return (
            f"Dr Bank {format_money(context.net_amount_cents)} / Cr AR "
            f"{format_money(context.net_amount_cents)}; "
            f"Dr Bank Fee Expense {format_money(context.bank_fee_cents)} / Cr Bank "
            f"{format_money(context.bank_fee_cents)}."
        )
    if exception_type in {"Refund", "Chargeback"}:
        return (
            "Dr AR (reopen customer balance) / Cr Bank for the refunded amount, with a "
            "corresponding chargeback or credit-memo document."
        )
    if exception_type == "On-Account / Unapplied":
        return (
            f"Dr Cash Unapplied {format_money(context.net_amount_cents)} / Cr Bank "
            f"{format_money(context.net_amount_cents)}."
        )
    if exception_type == "Collector Hold":
        return (
            f"Dr Cash Unapplied {format_money(context.net_amount_cents)} / Cr Bank "
            f"{format_money(context.net_amount_cents)}, reversing to AR once the hold is released."
        )
    if exception_type in {"Credit Memo", "Wrong Legal Entity", "FX Difference"}:
        return (
            "No general-ledger posting is recommended until the exception is resolved; the cash "
            "stays on the bank side of the reconciliation."
        )
    if proposed_invoices:
        total = min(context.net_amount_cents, context.gross_amount_cents)
        return (
            f"Dr Bank {format_money(total)} / Cr AR - {proposed_invoices[0]} "
            f"{format_money(total)}."
        )
    return "No general-ledger posting is recommended until the exception is resolved."


def build_explanation(
    context: ExceptionContext,
    exception_type: str,
    proposed_invoices: list[str],
    proposed_amount_cents: int,
    proposed_residual_cents: int,
    confidence: str,
) -> str:
    """Write the explanation addressed to the AR analyst."""
    parts: list[str] = []
    parts.append(
        f"This receipt of {format_money(context.net_amount_cents)} {context.currency} was left "
        f"unreconciled by the deterministic engine "
        f"({context.engine_reason or 'no rule matched'})."
    )
    if context.candidates:
        top = context.candidates[0]
        parts.append(
            f"The closest open invoice is {top.invoice_number} with "
            f"{format_money(top.open_amount_cents)} open "
            f"(difference {format_money(top.residual_cents)})."
        )
        if len(context.candidates) > 1:
            parts.append(
                f"{len(context.candidates)} candidate invoices were considered, so the ranking "
                f"below is the engine's arithmetic, not a confirmed customer intent."
            )
    else:
        parts.append("No open invoice was available for this customer and currency.")

    if proposed_invoices:
        parts.append(
            f"Recommended action is to apply {format_money(proposed_amount_cents)} to "
            f"{', '.join(proposed_invoices)}"
            + (
                f", leaving {format_money(proposed_residual_cents)} open."
                if proposed_residual_cents > 0
                else " and clear the invoice(s) in full."
            )
        )
    else:
        parts.append(
            "No invoice can be proposed safely, so the receipt is routed to manual review rather "
            "than applied on a guess."
        )
    parts.append(f"Confidence is {confidence}.")
    return " ".join(parts)
