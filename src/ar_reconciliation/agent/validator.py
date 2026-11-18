"""
Validating the agent's recommendation.

The agent is an adviser, not a bookkeeper.  Everything it proposes has to
pass through this module before it reaches the review sheet, and every
repair or downgrade is recorded as a note so a reviewer can see what was
changed and why.

The agent can never talk the system into applying an unsupported amount.
"""

from __future__ import annotations

from typing import Any

from ar_reconciliation.agent.context import ExceptionContext, Recommendation
from ar_reconciliation.core.primitives import format_money, money_to_cents, normalize_text
from ar_reconciliation.rules.policies import (
    AGENT_REVIEW_ONLY_SCENARIOS,
    CONFIDENCE_LEVELS,
    EXCEPTION_TYPES,
    HOLD_ONLY_SCENARIOS,
    MAX_PARTIAL_RESIDUAL_RATIO,
    RECOMMENDED_ACTIONS,
)

__all__ = ["validate_recommendation"]


def validate_recommendation(
    raw: dict[str, Any], context: ExceptionContext
) -> Recommendation:
    """
    Convert a raw model answer into a validated Recommendation.

    Any field that fails validation is repaired or downgraded.  The agent can
    never talk the system into applying an unsupported amount.
    """
    notes: list[str] = []

    exception_type = normalize_text(raw.get("exception_type"))
    if exception_type not in EXCEPTION_TYPES:
        notes.append(
            f"exception_type '{exception_type}' is outside the controlled taxonomy; "
            f"mapped to Unknown."
        )
        exception_type = "Unknown"

    confidence = normalize_text(raw.get("confidence")).title()
    if confidence not in CONFIDENCE_LEVELS:
        notes.append(
            f"confidence '{raw.get('confidence')}' is not a valid level; downgraded to Low."
        )
        confidence = "Low"

    recommended_action = normalize_text(raw.get("recommended_action"))
    if recommended_action not in RECOMMENDED_ACTIONS:
        notes.append(
            f"recommended_action '{recommended_action}' is not a valid action; "
            f"downgraded to Manual Review."
        )
        recommended_action = "Manual Review"

    # --- Proposed invoices must exist in the candidate pool ---
    valid_invoice_numbers = {c.invoice_number.upper(): c for c in context.candidates}
    raw_invoices = raw.get("proposed_invoices") or []
    if isinstance(raw_invoices, str):
        raw_invoices = [raw_invoices]
    proposed_invoices: list[str] = []
    for value in raw_invoices:
        text = normalize_text(value)
        if not text:
            continue
        if text.upper() not in valid_invoice_numbers:
            notes.append(
                f"proposed invoice {text} is not in the candidate pool; discarded."
            )
            continue
        if text not in proposed_invoices:
            proposed_invoices.append(text)

    # --- Proposed amount must not exceed the cash actually received ---
    proposed_amount_cents = money_to_cents(raw.get("proposed_amount", 0))
    max_appliable = (
        context.net_amount_cents
        if context.net_amount_cents > 0
        else context.gross_amount_cents
    )
    if proposed_amount_cents < 0:
        notes.append("proposed amount was negative; reset to 0.")
        proposed_amount_cents = 0
    if max_appliable < 0:
        # A refund or chargeback carries a negative amount.  There is no cash
        # to apply, so nothing may be proposed.
        if proposed_amount_cents > 0:
            notes.append(
                "the receipt carries a negative amount, so no cash can be applied to AR; "
                "the proposed amount was discarded."
            )
        proposed_amount_cents = 0
        max_appliable = 0
    elif proposed_amount_cents > max_appliable:
        notes.append(
            f"proposed amount {format_money(proposed_amount_cents)} exceeds the cash received "
            f"{format_money(max_appliable)}; capped."
        )
        proposed_amount_cents = max_appliable

    if proposed_invoices:
        candidate_total = sum(
            valid_invoice_numbers[i.upper()].open_amount_cents for i in proposed_invoices
        )
        if proposed_amount_cents > candidate_total:
            notes.append(
                f"proposed amount exceeds the combined open amount of the proposed invoices "
                f"({format_money(candidate_total)}); capped."
            )
            proposed_amount_cents = candidate_total
    elif proposed_amount_cents > 0:
        notes.append(
            "an amount was proposed without naming an invoice; the amount was discarded."
        )
        proposed_amount_cents = 0

    proposed_residual_cents = money_to_cents(raw.get("proposed_residual", 0))
    if proposed_residual_cents < 0:
        proposed_residual_cents = 0

    # The residual is recomputed from the actual open amounts rather than
    # trusted from the model, so the figure in the review sheet is always
    # consistent with the AR data.
    if proposed_invoices and proposed_amount_cents > 0:
        computed_residual = max(
            sum(
                valid_invoice_numbers[i.upper()].open_amount_cents
                for i in proposed_invoices
            )
            - proposed_amount_cents,
            0,
        )
        if computed_residual != proposed_residual_cents:
            notes.append(
                f"proposed residual {format_money(proposed_residual_cents)} does not agree "
                f"with the AR data; recomputed as {format_money(computed_residual)}."
            )
            proposed_residual_cents = computed_residual
    else:
        # Nothing is being applied, so there is no residual to report.  The
        # whole balance of the named invoices simply stays open.
        proposed_residual_cents = 0

    # --- Consistency between confidence, action and evidence ---
    if recommended_action == "Auto Match" and confidence != "High":
        notes.append(
            f"'Auto Match' requires High confidence but confidence is {confidence}; "
            f"downgraded to 'Recommend Match'."
        )
        recommended_action = "Recommend Match"

    if (
        recommended_action in {"Auto Match", "Recommend Match"}
        and not proposed_invoices
    ):
        notes.append("no invoice was proposed; downgraded to 'Manual Review'.")
        recommended_action = "Manual Review"

    if confidence == "Low" and recommended_action in {"Auto Match", "Recommend Match"}:
        notes.append(
            "Low confidence cannot carry an automatic recommendation; downgraded to "
            "'Manual Review'."
        )
        recommended_action = "Manual Review"

    # --- The engine's review-only decisions are not overturned by the agent ---
    #
    # These scenarios were deliberately excluded from auto-reconciliation.  The
    # agent may add context and propose an invoice, but it may not authorise
    # an automatic application.
    if (
        context.memo_scenario in AGENT_REVIEW_ONLY_SCENARIOS
        and recommended_action == "Auto Match"
    ):
        notes.append(
            f"scenario '{context.memo_scenario}' is review-only in the deterministic engine; "
            f"downgraded to 'Recommend Match'."
        )
        recommended_action = "Recommend Match"

    if context.memo_scenario in HOLD_ONLY_SCENARIOS and recommended_action in {
        "Auto Match",
        "Recommend Match",
    }:
        notes.append(
            f"scenario '{context.memo_scenario}' requires an accounting treatment decision; "
            f"downgraded to 'Hold / Escalate'."
        )
        recommended_action = "Hold / Escalate"

    # A receipt with no cash cannot be applied to an invoice.
    if (
        context.net_amount_cents <= 0
        and recommended_action in {"Auto Match", "Recommend Match"}
    ):
        notes.append(
            "the receipt carries no positive net amount, so nothing can be applied to AR; "
            "downgraded to 'Hold / Escalate'."
        )
        recommended_action = "Hold / Escalate"

    # A proposal that does not settle the invoice and leaves a large residual
    # is never an automatic match, it is a decision for a human.
    if proposed_invoices and proposed_residual_cents > 0:
        smallest_open = min(
            valid_invoice_numbers[i.upper()].open_amount_cents
            for i in proposed_invoices
        )
        if proposed_residual_cents > smallest_open * MAX_PARTIAL_RESIDUAL_RATIO:
            if recommended_action == "Auto Match":
                notes.append(
                    "the proposed residual is too large for an automatic match; downgraded to "
                    "'Recommend Match'."
                )
                recommended_action = "Recommend Match"

    return Recommendation(
        bank_transaction_id=context.bank_transaction_id,
        exception_type=exception_type,
        root_cause=normalize_text(raw.get("root_cause")),
        recommended_action=recommended_action,
        confidence=confidence,
        proposed_invoices=proposed_invoices,
        proposed_amount_cents=proposed_amount_cents,
        proposed_residual_cents=proposed_residual_cents,
        accounting_treatment=normalize_text(raw.get("accounting_treatment")),
        gl_posting=normalize_text(raw.get("gl_posting")),
        explanation=normalize_text(raw.get("explanation")),
        analysed_by="",
        candidates_considered=[c.invoice_number for c in context.candidates],
        validation_notes=notes,
    )
