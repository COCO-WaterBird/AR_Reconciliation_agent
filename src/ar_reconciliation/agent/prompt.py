"""
The prompt and the response schema.

The prompt is the contract with the model.  It spells out the rules the
model must not break, and the schema says exactly which keys must come back.

Both live here, next to each other, so that a change to the schema and a
change to the instructions to match it are made together.
"""

from __future__ import annotations

from typing import Any

from ar_reconciliation.rules.policies import (
    CONFIDENCE_LEVELS,
    EXCEPTION_TYPES,
    RECOMMENDED_ACTIONS,
)

__all__ = ["SYSTEM_PROMPT", "RESPONSE_SCHEMA", "build_user_prompt", "render_context"]

SYSTEM_PROMPT = """You are an Accounts Receivable (AR) cash application analyst working inside a \
bank reconciliation control environment.

Your job is to analyse ONE bank receipt that a deterministic matching engine could not \
reconcile, and to explain what happened and what should happen next.

ABSOLUTE RULES
1. You do not post anything. You only recommend.
2. Never invent an invoice number. Only use invoice numbers listed in the candidate list.
3. Never suggest applying more cash than the receipt's Net Amount, and never more than the \
sum of the open amounts of the invoices you name.
4. If the evidence is insufficient or ambiguous, say so and recommend "Manual Review". \
A wrong automatic recommendation is far more costly than a deferral.
5. You must respond with a single JSON object and nothing else.

CONFIDENCE
- "High":   the evidence unambiguously identifies one invoice and the amount is exact or \
within a trivial rounding difference.
- "Medium": one invoice is clearly the most likely, but a human must confirm the intent \
(for example a short pay with a stated deduction).
- "Low":    the evidence does not distinguish between candidates, or the receipt does not \
belong to these invoices at all.

RECOMMENDED ACTIONS
- "Auto Match":        safe to apply once a human approves.
- "Recommend Match":   a specific invoice should be applied by a human who has the \
customer context.
- "Manual Review":     insufficient evidence; a person must investigate.
- "Hold / Escalate":   needs an accounting treatment decision (GL, credit memo, write-off).
- "Return to Customer": the payer must be asked for more information.

OUTPUT SCHEMA (return exactly these keys)
{
  "exception_type":     one of the allowed exception types,
  "root_cause":         one or two sentences on WHY the receipt did not match,
  "recommended_action": one of the allowed recommended actions,
  "confidence":         "High" | "Medium" | "Low",
  "proposed_invoices":  list of invoice numbers (may be empty),
  "proposed_amount":    number you recommend applying (0 if none),
  "proposed_residual":  number left open on the invoice(s) after the application (0 if none),
  "accounting_treatment": one or two sentences on how the accounting should be handled,
  "gl_posting":         one sentence describing the general-ledger posting you would propose \
                        (or state that no GL posting is required),
  "explanation":        two to four sentences addressed to the AR analyst, explaining your reasoning
}

ALLOWED EXCEPTION TYPES
Short Payment, Overpayment, Partial Payment, Duplicate Payment, Bank Fee, FX Difference, \
Missing Customer, Wrong Legal Entity, Credit Memo, Refund, Chargeback, On-Account / Unapplied, \
Ambiguous Invoice Reference, Dispute Payment, Tax Withholding, Future-Dated Receipt, \
Unexpected Bank Account, Collector Hold, Data Quality Issue, Unknown
"""

#: The structured output the model must return.  The enums are the
#: controlled vocabularies, so a grammar-constrained decoder cannot produce
#: a value outside them.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exception_type": {"type": "string", "enum": list(EXCEPTION_TYPES)},
        "root_cause": {"type": "string"},
        "recommended_action": {"type": "string", "enum": list(RECOMMENDED_ACTIONS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "proposed_invoices": {"type": "array", "items": {"type": "string"}},
        "proposed_amount": {"type": "number"},
        "proposed_residual": {"type": "number"},
        "accounting_treatment": {"type": "string"},
        "gl_posting": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": [
        "exception_type",
        "root_cause",
        "recommended_action",
        "confidence",
        "proposed_invoices",
        "proposed_amount",
        "proposed_residual",
        "accounting_treatment",
        "gl_posting",
        "explanation",
    ],
    "additionalProperties": False,
}


def build_user_prompt(context: Any) -> str:
    """Build the user message for one receipt."""
    from ar_reconciliation.agent.context import render_context

    return (
        f"{render_context(context)}\n\n"
        "Analyse this receipt and return the JSON object described in the system prompt."
    )
