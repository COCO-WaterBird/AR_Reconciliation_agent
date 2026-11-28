"""
Financial validation rules, thresholds and taxonomies.

Everything in this module is a business rule.  Changing a value here
changes how the company reconciles, so every value is spelled out with the
reason it has that value, and none of it is derived from anything else.

The module deliberately holds no logic beyond "is this value in the set" and
"is this scenario allowed to do X".  The decisions themselves live in the
matching and agent packages.

Two of the sets here look similar but are not the same, and the difference
is deliberate:

  * ``ENGINE_REVIEW_ONLY_SCENARIOS`` - the scenarios the deterministic
    engine refuses to auto-reconcile.  These are routed to the review sheet.
  * ``AGENT_REVIEW_ONLY_SCENARIOS`` - the scenarios the AI agent may not
    overturn with an automatic match.  It is the engine's set plus
    "Lockbox Batch", and it is a *cap on the agent*, not a routing rule.

They are kept apart on purpose so that neither can drift into the other.
"""

from __future__ import annotations

from decimal import Decimal

__all__ = [
    "STRUCTURED_BLOCKED_SCENARIOS",
    "AMOUNT_TOLERANCE_CENTS",
    "ROUNDING_TOLERANCE_CENTS",
    "SMALL_RESIDUAL_WRITE_OFF_CENTS",
    "MAX_CANDIDATES_PER_BANK_RECEIPT",
    "MAX_PARTIAL_RESIDUAL_RATIO",
    "MIN_PARTIAL_RESIDUAL_CENTS",
    "PARTIAL_ALLOWANCE_SCENARIOS",
    "UNRESTRICTED_PARTIAL_SCENARIOS",
    "ENGINE_REVIEW_ONLY_SCENARIOS",
    "NAME_FALLBACK_SCENARIOS",
    "AGENT_REVIEW_ONLY_SCENARIOS",
    "HOLD_ONLY_SCENARIOS",
    "EXCEPTION_TYPES",
    "CONFIDENCE_LEVELS",
    "RECOMMENDED_ACTIONS",
    "SCENARIO_TO_EXCEPTION_TYPE",
    "MAX_CANDIDATES_IN_PROMPT",
    "CENTS",
]

# ---------------------------------------------------------------------------
# Amount tolerances
# ---------------------------------------------------------------------------

#: Exact-match tolerance.  Zero means the amount has to match to the cent.
AMOUNT_TOLERANCE_CENTS = 0

#: Rounding tolerance, in cents.  A receipt that differs from the invoice by
#: at most this much is treated as a rounding variance rather than a
#: mismatch.  Two cents is the usual accounting float tolerance.
ROUNDING_TOLERANCE_CENTS = 2

#: A residual at or below this (in cents) is a manual write-off candidate
#: rather than a mismatch.  Five dollars.
SMALL_RESIDUAL_WRITE_OFF_CENTS = 500

#: Upper bound on the invoices considered for one receipt.  Keeps the
#: subset-sum search bounded on a customer with many open invoices.
MAX_CANDIDATES_PER_BANK_RECEIPT = 80

#: Maximum residual, as a ratio of the referenced AR total, that may be
#: applied for scenarios where a residual is a legitimate business outcome.
#: A residual larger than this means the memo probably points at a different
#: invoice and must not be silently applied.
MAX_PARTIAL_RESIDUAL_RATIO = 0.10

#: Absolute floor for the residual ratio, in cents.  One dollar.  Without it
#: a tiny invoice could have a proportionally huge residual.
MIN_PARTIAL_RESIDUAL_CENTS = 100

#: One cent, as a Decimal.  Used for half-up quantisation.
CENTS = Decimal("0.01")

# ---------------------------------------------------------------------------
# Scenario policies
# ---------------------------------------------------------------------------

#: Scenarios where a partial / residual application is a valid business
#: outcome.  For these, the memo invoice reference is allowed to differ from
#: the receipt amount and the residual is reported rather than treated as a
#: mismatch.
PARTIAL_ALLOWANCE_SCENARIOS = {
    "Short Payment",
    "Overpayment",
    "Partial Payment",
    "Installment",
    "Small Residual Write-Off",
    "Sales Tax Adjustment",
}

#: Scenarios where the memo names the invoice explicitly and the receipt is
#: simply one of several instalments against it.  The remaining balance stays
#: open and no residual cap applies, because the balance is expected to be
#: cleared by later receipts.
UNRESTRICTED_PARTIAL_SCENARIOS = {
    "Partial Payment",
    "Installment",
}

#: Scenarios the deterministic engine refuses to auto-reconcile.  These are
#: routed to the review sheet regardless of how well the amounts line up.
ENGINE_REVIEW_ONLY_SCENARIOS = {
    "Duplicate Review",
    "Currency Mismatch",
    "Wrong Customer or Legal Entity",
    "Reversal / Chargeback",
    "Refund",
    "FX Exception",
    "Future-Dated Receipt",
    "Unexpected Bank Account",
    "Collector Hold",
    "Dispute Payment",
    "Credit Memo",
    "Tax Withholding",
    "On-account / Unapplied",
    "Missing ERP Customer ID",
    "Ambiguous Invoice Reference",
}

#: Memo scenarios where structured evidence (remittance advice, contract
#: schedule) may NOT drive an automatic match (decision G2): each needs an
#: accounting or investigation decision that a remittance cannot settle.
#: Scenarios whose only problem is missing or ambiguous payment detail
#: (no memo, PO-only, ambiguous reference, partial / short payment, tax
#: withholding, missing customer ID) are not listed and may be resolved.
STRUCTURED_BLOCKED_SCENARIOS = frozenset(
    {
        "Duplicate Review",
        "Currency Mismatch",
        "Wrong Customer or Legal Entity",
        "Reversal / Chargeback",
        "Refund",
        "FX Exception",
        "Future-Dated Receipt",
        "Unexpected Bank Account",
        "Collector Hold",
        "Dispute Payment",
        "Credit Memo",
        "Lockbox Batch",
    }
)

#: Scenarios that may be applied only after a controlled fallback match:
#:   - "Missing ERP Customer ID"  -> fall back to customer/remitter name.
#:   - "Parent Child Payment"     -> fall back to the child customer AR.
#: These are handled separately from ENGINE_REVIEW_ONLY_SCENARIOS because a
#: safe match is still possible when the memo names a specific open invoice.
NAME_FALLBACK_SCENARIOS = {
    "Missing ERP Customer ID",
    "Parent Child Payment",
}

#: Scenarios the deterministic engine refused to auto-reconcile on purpose.
#: The AI agent may add context, but it must never overturn the engine's
#: review-only decision with an automatic match.  These are capped at
#: "Recommend Match" at best.
AGENT_REVIEW_ONLY_SCENARIOS = frozenset(
    {
        "Duplicate Review",
        "Currency Mismatch",
        "Wrong Customer or Legal Entity",
        "Reversal / Chargeback",
        "Refund",
        "FX Exception",
        "Future-Dated Receipt",
        "Unexpected Bank Account",
        "Collector Hold",
        "Dispute Payment",
        "Credit Memo",
        "Tax Withholding",
        "On-account / Unapplied",
        "Ambiguous Invoice Reference",
        "Lockbox Batch",
    }
)

#: Scenarios where applying cash would be an accounting error rather than a
#: matching question.  These are always escalated.
HOLD_ONLY_SCENARIOS = frozenset(
    {
        "Collector Hold",
        "Reversal / Chargeback",
        "Refund",
        "Credit Memo",
        "On-account / Unapplied",
        "Lockbox Batch",
    }
)

# ---------------------------------------------------------------------------
# AI exception taxonomy
# ---------------------------------------------------------------------------

#: The controlled vocabulary of exception types.  A model answer outside
#: this set is mapped to "Unknown" rather than accepted.
EXCEPTION_TYPES = (
    "Short Payment",
    "Overpayment",
    "Partial Payment",
    "Duplicate Payment",
    "Bank Fee",
    "FX Difference",
    "Missing Customer",
    "Wrong Legal Entity",
    "Credit Memo",
    "Refund",
    "Chargeback",
    "On-Account / Unapplied",
    "Ambiguous Invoice Reference",
    "Dispute Payment",
    "Tax Withholding",
    "Future-Dated Receipt",
    "Unexpected Bank Account",
    "Collector Hold",
    "Data Quality Issue",
    "Unknown",
)

#: Confidence levels, strongest first.  Only "High" may be auto-approved by
#: a reviewer without further evidence; "Medium" requires a named invoice;
#: "Low" always goes to manual review.
CONFIDENCE_LEVELS = ("High", "Medium", "Low")

#: The actions the agent may recommend.  "Auto Match" is the only one that
#: can move money, and only with High confidence.
RECOMMENDED_ACTIONS = (
    "Auto Match",  # safe to apply after human approval
    "Recommend Match",  # plausible but needs a human decision
    "Manual Review",  # insufficient evidence, escalate
    "Hold / Escalate",  # accounting treatment required, do not apply
    "Return to Customer",  # request more information from the payer
)

#: Maximum number of candidate invoices shown to the model.  Keeps the
#: prompt bounded and prevents the agent from drowning in rows.
MAX_CANDIDATES_IN_PROMPT = 12

#: Maps a memo scenario to the exception type the agent reports for it.
SCENARIO_TO_EXCEPTION_TYPE = {
    "Short Payment": "Short Payment",
    "Overpayment": "Overpayment",
    "Partial Payment": "Partial Payment",
    "Installment": "Partial Payment",
    "Small Residual Write-Off": "Short Payment",
    "Sales Tax Adjustment": "Tax Withholding",
    "Duplicate Review": "Duplicate Payment",
    "Currency Mismatch": "FX Difference",
    "Wrong Customer or Legal Entity": "Wrong Legal Entity",
    "Reversal / Chargeback": "Chargeback",
    "Refund": "Refund",
    "FX Exception": "FX Difference",
    "Future-Dated Receipt": "Future-Dated Receipt",
    "Unexpected Bank Account": "Unexpected Bank Account",
    "Collector Hold": "Collector Hold",
    "Dispute Payment": "Dispute Payment",
    "Credit Memo": "Credit Memo",
    "Tax Withholding": "Tax Withholding",
    "On-account / Unapplied": "On-Account / Unapplied",
    "Missing ERP Customer ID": "Missing Customer",
    "Ambiguous Invoice Reference": "Ambiguous Invoice Reference",
    "Bank Fee": "Bank Fee",
    "Lockbox Batch": "On-Account / Unapplied",
    "No Memo": "Unknown",
    "Unclassified": "Unknown",
}
