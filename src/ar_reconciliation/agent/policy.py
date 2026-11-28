"""
The financial policy decision for one receipt.

Deterministic financial rules take precedence over any AI recommendation.
This module turns the existing rules into one explicit decision per receipt,
used both by the review UI (to show the rule results and to enable or
disable Approve) and by the approval write-back (to refuse an approval the
rules do not allow), so the two can never disagree.

It adds no new business rule.  It applies the ones the project already has:

* the hold-only scenarios in :mod:`ar_reconciliation.rules.policies`, where
  applying cash "would be an accounting error rather than a matching
  question" and which are "always escalated" - except the scenario a
  validated evidence-based resolution may resolve (decision E1-E5 work);
* invoice eligibility from the engine's candidate rules (closed, disputed,
  on hold, dispute reason);
* the candidate pool (customer and currency), the gross cash cap (E5) and
  the open balance;
* evidence sufficiency from :func:`validate_resolution`;
* the requirement that a person approves before anything is applied.

The rules read deterministic data only (memo scenario, AR status, amounts),
never the recommendation text, so editing the "Recommended Action" cell in
Excel cannot turn a blocked proposal into an executable one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ar_reconciliation.core.primitives import format_money, money_to_cents, normalize_text
from ar_reconciliation.matching.candidates import CLOSED_AR_STATUSES, ON_HOLD_RECON_STATUSES
from ar_reconciliation.rules.policies import AGENT_REVIEW_ONLY_SCENARIOS, HOLD_ONLY_SCENARIOS

__all__ = ["RuleResult", "PolicyDecision", "evaluate_policy", "RESULTS"]

#: Rule results, in the vocabulary the UI and the report use.
RESULTS = ("PASS", "FAIL", "WARNING", "NOT EVALUATED", "NOT APPLICABLE")

#: Required next action for each blocking scenario.
_HOLD_ACTIONS = {
    "Collector Hold": "Obtain an authorized collector hold release",
    "Reversal / Chargeback": "Record the reversal or chargeback treatment; do not apply cash",
    "Refund": "Record the refund treatment; do not apply cash",
    "Credit Memo": "Obtain the credit memo approval and apply it separately",
    "On-account / Unapplied": "Obtain remittance detail identifying the invoices",
    "Lockbox Batch": "Obtain the lockbox batch detail before applying",
}


@dataclass
class RuleResult:
    rule_id: str
    control: str
    result: str
    severity: str  # Blocking, Warning, Info
    explanation: str


@dataclass
class PolicyDecision:
    rules: list[RuleResult] = field(default_factory=list)
    ai_recommendation: str = ""
    candidate_invoices: list[str] = field(default_factory=list)
    proposed_allocation_cents: int = 0
    authorized_allocation_cents: int = 0
    execution_allowed: bool = False
    final_decision: str = ""
    required_action: str = ""
    overridden_by_policy: bool = False
    blocking_reasons: list[str] = field(default_factory=list)
    currency: str = ""

    @property
    def has_proposal(self) -> bool:
        return bool(self.candidate_invoices) and self.proposed_allocation_cents > 0


def _cents(value: Any) -> int:
    try:
        return money_to_cents(value)
    except Exception:
        return 0


def _text(value: Any) -> str:
    try:
        return normalize_text(value)
    except Exception:
        return ""


def evaluate_policy(
    context: Any,
    review_row: Any | None,
    resolution_status: str = "",
    evidence_sufficiency: str = "",
    already_applied: bool = False,
    approval_recorded: bool = False,
) -> PolicyDecision:
    """
    Evaluate the deterministic rules for one receipt's current proposal.

    ``context`` is the agent's ExceptionContext (built with the receipt's
    evidence when documents exist); ``review_row`` the receipt's row in the
    AI Exception Review sheet (or None before any analysis).
    """
    from ar_reconciliation.agent.resolution import EVIDENCE_RESOLVABLE_HOLD_SCENARIOS, parse_allocations

    get = (lambda k: review_row.get(k) if review_row is not None else None)
    decision = PolicyDecision(currency=_text(getattr(context, "currency", "")).upper())
    decision.ai_recommendation = _text(get("Recommended Action")) or "Not analyzed"

    allocations = []
    allocations_text = _text(get("Proposed Allocations"))
    if allocations_text:
        try:
            allocations = parse_allocations(allocations_text)
        except ValueError:
            allocations = []
    if allocations:
        decision.candidate_invoices = [a.invoice for a in allocations]
        decision.proposed_allocation_cents = sum(a.amount_cents for a in allocations)
    else:
        decision.candidate_invoices = [
            v.strip().upper() for v in _text(get("Proposed Invoice Numbers")).split(",") if v.strip()
        ]
        decision.proposed_allocation_cents = _cents(get("Proposed Applied Amount"))

    pool = {c.invoice_number.upper(): c for c in getattr(context, "candidates", [])}
    scenario = _text(getattr(context, "memo_scenario", ""))
    rules = decision.rules
    has_proposal = decision.has_proposal

    def add(rule_id, control, result, severity, explanation):
        rules.append(RuleResult(rule_id, control, result, severity, explanation))
        if result == "FAIL" and severity == "Blocking":
            decision.blocking_reasons.append(f"{control}: {explanation}")

    # FP-01 Customer identity
    if not has_proposal:
        add("FP-01", "Customer Identity", "NOT APPLICABLE", "Info", "No invoice is proposed.")
    else:
        outside = [i for i in decision.candidate_invoices if i not in pool]
        third_party = [i for i in decision.candidate_invoices if i in pool and pool[i].third_party]
        if outside:
            add("FP-01", "Customer Identity", "FAIL", "Blocking",
                f"{', '.join(outside)} are not open AR of the receipt's customer and currency.")
        elif third_party:
            add("FP-01", "Customer Identity", "WARNING", "Warning",
                f"{', '.join(third_party)} belong to a third party named in a supporting document (E2).")
        else:
            add("FP-01", "Customer Identity", "PASS", "Info", "Proposed invoices belong to the receipt's customer.")

    # FP-02 Invoice eligibility / FP-03 hold and dispute restrictions
    if has_proposal:
        blocked = []
        for invoice in decision.candidate_invoices:
            cand = pool.get(invoice)
            if cand is None:
                continue
            status = cand.ar_status.lower()
            if cand.dispute_reason or status in CLOSED_AR_STATUSES or cand.recon_status.lower() in ON_HOLD_RECON_STATUSES:
                blocked.append(f"{invoice} ({cand.dispute_reason or cand.ar_status or cand.recon_status})")
        if blocked:
            add("FP-02", "Invoice Eligibility", "FAIL", "Blocking",
                f"Invoice restricted: {', '.join(blocked)}. Cash is not applied without a release or dispute decision.")
        else:
            add("FP-02", "Invoice Eligibility", "PASS", "Info", "Proposed invoices are open and not restricted.")
    else:
        add("FP-02", "Invoice Eligibility", "NOT APPLICABLE", "Info", "No invoice is proposed.")

    resolvable = (
        scenario in EVIDENCE_RESOLVABLE_HOLD_SCENARIOS and resolution_status in {"Valid", "Downgraded"}
    )
    if scenario in HOLD_ONLY_SCENARIOS and not resolvable:
        add("FP-03", "Hold Restriction", "FAIL", "Blocking",
            f"'{scenario}' requires an accounting decision; cash may not be applied by a matching approval.")
    elif scenario in AGENT_REVIEW_ONLY_SCENARIOS:
        add("FP-03", "Hold Restriction", "WARNING", "Warning",
            f"'{scenario}' is review-only: it can be applied only after a reviewer approves it.")
    else:
        add("FP-03", "Hold Restriction", "PASS", "Info", "No hold or review-only scenario applies.")

    # FP-04 Currency consistency
    if has_proposal:
        mismatched = [i for i in decision.candidate_invoices
                      if i in pool and pool[i].currency.upper() != decision.currency]
        if mismatched:
            add("FP-04", "Currency Consistency", "FAIL", "Blocking",
                f"{', '.join(mismatched)} are not in {decision.currency}.")
        else:
            add("FP-04", "Currency Consistency", "PASS", "Info", f"Receipt and invoices are in {decision.currency}.")
    else:
        add("FP-04", "Currency Consistency", "NOT APPLICABLE", "Info", "No invoice is proposed.")

    # FP-05 Amount conservation
    gross = int(getattr(context, "gross_amount_cents", 0) or 0)
    if has_proposal:
        open_total = sum(pool[i].open_amount_cents for i in decision.candidate_invoices if i in pool)
        if gross <= 0:
            add("FP-05", "Amount Conservation", "FAIL", "Blocking", "The receipt carries no positive cash.")
        elif decision.proposed_allocation_cents > gross:
            add("FP-05", "Amount Conservation", "FAIL", "Blocking",
                f"Proposed {format_money(decision.proposed_allocation_cents)} exceeds the receipt's "
                f"{format_money(gross)}.")
        elif decision.proposed_allocation_cents > open_total:
            add("FP-05", "Amount Conservation", "FAIL", "Blocking",
                f"Proposed {format_money(decision.proposed_allocation_cents)} exceeds the open balance "
                f"{format_money(open_total)}.")
        else:
            add("FP-05", "Amount Conservation", "PASS", "Info",
                "Within the receipt's gross amount and the invoices' open balance.")
    else:
        add("FP-05", "Amount Conservation", "NOT APPLICABLE", "Info", "No amount is proposed.")

    # FP-06 Duplicate prevention
    if already_applied:
        add("FP-06", "Duplicate Prevention", "FAIL", "Blocking", "This receipt has already been applied.")
    elif scenario == "Duplicate Review":
        add("FP-06", "Duplicate Prevention", "WARNING", "Warning",
            "Possible duplicate payment; confirm with the bank and the customer before approving.")
    else:
        add("FP-06", "Duplicate Prevention", "PASS", "Info", "The receipt has not been applied before.")

    # FP-07 Evidence requirements
    documents = getattr(context, "documents", [])
    if not documents:
        add("FP-07", "Evidence Requirements", "NOT EVALUATED", "Info", "No supporting documents are linked.")
    elif evidence_sufficiency in {"Insufficient", "Conflicting"}:
        add("FP-07", "Evidence Requirements", "FAIL", "Blocking",
            f"Supporting evidence is {evidence_sufficiency.lower()}.")
    elif evidence_sufficiency == "Sufficient":
        add("FP-07", "Evidence Requirements", "PASS", "Info", "Supporting documents support the proposal.")
    else:
        add("FP-07", "Evidence Requirements", "NOT EVALUATED", "Info", "Documents are linked but not yet analyzed.")

    # FP-08 Adjustment authorization
    adjustments = _text(get("Proposed Adjustments"))
    if adjustments:
        add("FP-08", "Adjustment Authorization", "PASS", "Info",
            f"Adjustments passed validation: {adjustments}. Reviewer approval still required.")
    else:
        add("FP-08", "Adjustment Authorization", "NOT APPLICABLE", "Info", "No non-cash adjustment is proposed.")

    # FP-09 Validation of the recommendation itself
    if resolution_status == "Rejected":
        add("FP-09", "Proposal Validation", "FAIL", "Blocking", "The proposal failed deterministic validation.")
    elif resolution_status == "Needs Review":
        add("FP-09", "Proposal Validation", "FAIL", "Blocking", "The proposal is not supported well enough to apply.")
    elif has_proposal:
        add("FP-09", "Proposal Validation", "PASS", "Info", "The proposal passed deterministic validation.")
    else:
        add("FP-09", "Proposal Validation", "NOT APPLICABLE", "Info", "No proposal to validate.")

    # FP-10 Approval requirement
    add("FP-10", "Approval Requirement", "PASS" if approval_recorded else "NOT EVALUATED", "Info",
        "Reviewer approval recorded." if approval_recorded else "A reviewer must approve before any cash is applied.")

    blocking = bool(decision.blocking_reasons)
    executable_action = decision.ai_recommendation in {"Auto Match", "Recommend Match"}
    decision.execution_allowed = has_proposal and not blocking and (executable_action or resolution_status in {"Valid", "Downgraded"})
    decision.authorized_allocation_cents = decision.proposed_allocation_cents if decision.execution_allowed else 0
    decision.overridden_by_policy = has_proposal and blocking

    if decision.execution_allowed:
        decision.final_decision = "Eligible for Approval"
        decision.required_action = "Review the proposal and approve or reject it"
    elif blocking and scenario in HOLD_ONLY_SCENARIOS and not resolvable:
        decision.final_decision = "Hold / Escalate"
        decision.required_action = _HOLD_ACTIONS.get(scenario, "Escalate for an accounting decision")
    elif blocking:
        decision.final_decision = "Blocked by Financial Policy"
        first = decision.rules[[r.result for r in decision.rules].index("FAIL")]
        decision.required_action = {
            "FP-01": "Confirm the customer, or obtain documentation of the third-party payment",
            "FP-02": "Resolve the hold or dispute on the invoice",
            "FP-04": "Investigate the currency mismatch",
            "FP-05": "Correct the proposed amount",
            "FP-06": "No action: the receipt is already applied",
            "FP-07": "Obtain complete, consistent supporting evidence",
            "FP-09": "Investigate further; the proposal is not supported",
        }.get(first.rule_id, "Investigate")
    elif decision.ai_recommendation == "Not analyzed":
        decision.final_decision = "Not Analyzed"
        decision.required_action = "Run the investigation"
    else:
        decision.final_decision = decision.ai_recommendation or "Manual Review"
        decision.required_action = {
            "Manual Review": "Investigate the receipt and identify the invoices",
            "Hold / Escalate": "Escalate for an accounting decision",
            "Return to Customer": "Request remittance detail from the customer",
        }.get(decision.ai_recommendation, "Investigate the receipt")
    return decision
