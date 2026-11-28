"""
Evidence-based resolutions and their deterministic validation.

A *resolution* is the concrete proposal for one receipt: how much cash goes
to which invoice (allocations), which non-cash adjustments close the
difference (adjustments), and which document lines support it (citations).

The model, or the evidence rules, only *propose* a resolution.
:func:`validate_resolution` decides whether it is acceptable, with plain
Python and integer cents:

  * every invoice must be open AR in the candidate pool (the receipt's own
    customer and currency, or a third-party customer named in a document -
    decision E2), not disputed and not on hold;
  * each allocation must be positive and no larger than the invoice's open
    balance, and the allocations together no larger than the receipt's gross
    amount (decision E5);
  * an adjustment must be an Early Payment Discount or a Customer Deduction
    (decision E1), must be supported by an extracted fact, and together with
    the allocation may not exceed the open balance.  A discount may not
    exceed the contract percentage of the invoice and must fall inside the
    discount window; a deduction must equal an amount a document states;
  * every citation must point at a linked document and quote text that is
    really in it;
  * conflicting or insufficient evidence keeps the receipt in review.

A resolution that fails a cash check is rejected as a whole; an unsupported
adjustment or citation is dropped and the resolution is marked downgraded.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ar_reconciliation.core.primitives import (
    MoneyParseError,
    format_money,
    money_to_cents,
    normalize_text,
)
from ar_reconciliation.matching.candidates import CLOSED_AR_STATUSES, ON_HOLD_RECON_STATUSES

__all__ = [
    "ADJUSTMENT_TYPES",
    "EVIDENCE_SUFFICIENCY",
    "EVIDENCE_RESOLVABLE_HOLD_SCENARIOS",
    "Allocation",
    "Adjustment",
    "Citation",
    "Resolution",
    "validate_resolution",
    "resolution_id",
    "format_allocations",
    "format_adjustments",
    "format_citations",
    "parse_allocations",
    "parse_adjustments",
    "max_discount_cents",
    "reconcile_recommendation",
]

#: The non-cash adjustments an approved resolution may carry (decision E1).
ADJUSTMENT_TYPES = ("Early Payment Discount", "Customer Deduction")

EVIDENCE_SUFFICIENCY = ("Sufficient", "Insufficient", "Conflicting")

#: Hold-only scenarios whose only problem is missing payment information.
#: A document that names the invoice resolves them, so an evidence-backed,
#: fully validated resolution is not forced to "Hold / Escalate".  Every
#: other hold-only scenario (collector hold, refund, chargeback, credit memo,
#: lockbox batch) stays held whatever the documents say.
EVIDENCE_RESOLVABLE_HOLD_SCENARIOS = frozenset({"On-account / Unapplied"})


@dataclass
class Allocation:
    invoice: str
    amount_cents: int


@dataclass
class Adjustment:
    invoice: str
    type: str
    amount_cents: int


@dataclass
class Citation:
    doc_id: str
    location: str
    quote: str


@dataclass
class Resolution:
    """A validated evidence-based resolution for one receipt."""

    status: str = "Not Applicable"  # Valid, Downgraded, Needs Review, Rejected, Not Applicable
    sufficiency: str = ""
    allocations: list[Allocation] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    third_party_invoices: list[str] = field(default_factory=list)
    resolution_id: str = ""

    @property
    def applicable(self) -> bool:
        """True when the resolution can be approved and applied."""
        return self.status in {"Valid", "Downgraded"} and bool(self.allocations)

    @property
    def cash_cents(self) -> int:
        return sum(a.amount_cents for a in self.allocations)

    @property
    def adjustment_cents(self) -> int:
        return sum(a.amount_cents for a in self.adjustments)


# ---------------------------------------------------------------------------
# Text form, as shown in (and read back from) the review sheet
# ---------------------------------------------------------------------------

def format_allocations(allocations: list[Allocation]) -> str:
    return "; ".join(f"{a.invoice}: {format_money(a.amount_cents)}" for a in allocations)


def format_adjustments(adjustments: list[Adjustment]) -> str:
    return "; ".join(
        f"{a.invoice}: {a.type} {format_money(a.amount_cents)}" for a in adjustments
    )


def format_citations(citations: list[Citation]) -> str:
    return "\n".join(f'{c.doc_id} {c.location}: "{c.quote}"' for c in citations)


_ALLOCATION_ITEM = re.compile(r"^\s*(?P<invoice>[A-Za-z0-9-]+)\s*[:=]\s*(?P<amount>.+?)\s*$")
_ADJUSTMENT_ITEM = re.compile(
    r"^\s*(?P<invoice>[A-Za-z0-9-]+)\s*[:=]\s*(?P<type>[A-Za-z /-]+?)\s+(?P<amount>[-(]?[\d,.$]+\)?)\s*$"
)


def parse_allocations(text: object) -> list[Allocation]:
    """
    Read allocations back from the review sheet ("INV-1: 1,200.00; INV-2: 300.00").

    Raises ValueError on an entry that cannot be read, so a reviewer's typo
    is rejected rather than half-applied.
    """
    allocations: list[Allocation] = []
    for item in normalize_text(text).split(";"):
        if not item.strip():
            continue
        match = _ALLOCATION_ITEM.match(item)
        if not match:
            raise ValueError(f"cannot read the allocation '{item.strip()}'")
        try:
            cents = money_to_cents(match.group("amount"))
        except MoneyParseError as exc:
            raise ValueError(f"cannot read the amount in '{item.strip()}'") from exc
        allocations.append(Allocation(match.group("invoice").upper(), cents))
    return allocations


def parse_adjustments(text: object) -> list[Adjustment]:
    adjustments: list[Adjustment] = []
    for item in normalize_text(text).split(";"):
        if not item.strip():
            continue
        match = _ADJUSTMENT_ITEM.match(item)
        if not match:
            raise ValueError(f"cannot read the adjustment '{item.strip()}'")
        try:
            cents = money_to_cents(match.group("amount"))
        except MoneyParseError as exc:
            raise ValueError(f"cannot read the amount in '{item.strip()}'") from exc
        adjustments.append(
            Adjustment(match.group("invoice").upper(), " ".join(match.group("type").split()), cents)
        )
    return adjustments


def resolution_id(txn_id: str, allocations: list[Allocation], adjustments: list[Adjustment]) -> str:
    """
    A stable identifier for one exact proposal.

    The same receipt with the same allocations and adjustments always gets
    the same ID, so a second attempt to apply it is recognised.
    """
    canonical = "|".join(
        [normalize_text(txn_id).upper()]
        + [f"A:{a.invoice.upper()}={a.amount_cents}" for a in sorted(allocations, key=lambda a: a.invoice)]
        + [
            f"J:{a.invoice.upper()}:{a.type}={a.amount_cents}"
            for a in sorted(adjustments, key=lambda a: (a.invoice, a.type))
        ]
    )
    return "RES-" + hashlib.sha256(canonical.encode()).hexdigest()[:12].upper()


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------

def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(normalize_text(text)[:10])
    except ValueError:
        return None


def max_discount_cents(open_cents: int, discount_bp: int) -> int:
    """The largest discount the terms allow, rounded half-up to the cent."""
    value = Decimal(open_cents) * Decimal(discount_bp) / Decimal(10000)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _quote_in_document(quote: str, lines: list[tuple[str, str]]) -> str | None:
    """The location of the line containing the quote, or None."""
    wanted = _fold(quote).rstrip("…")
    if not wanted:
        return None
    for location, text in lines:
        if wanted in _fold(text):
            return location
    whole = _fold(" ".join(text for _, text in lines))
    return "document" if wanted in whole else None


def _to_cents(value: Any) -> int | None:
    try:
        return money_to_cents(value)
    except MoneyParseError:
        return None


def _discount_support(context: Any, invoice: str, amount_cents: int, open_cents: int, candidate: Any) -> str:
    """Empty when the discount is supported, else the reason it is not."""
    terms = [f for f in context.evidence if f.field == "discount_terms" and f.details.get("discount_bp")]
    if not terms:
        return "no document states early-payment discount terms"
    reasons: list[str] = []
    for fact in terms:
        allowed = max_discount_cents(open_cents, int(fact.details["discount_bp"]))
        if amount_cents > allowed:
            reasons.append(
                f"{format_money(amount_cents)} exceeds {fact.value} of the open balance "
                f"({format_money(allowed)})"
            )
            continue
        days = fact.details.get("discount_days")
        invoice_date = _parse_date(candidate.invoice_date)
        paid_on = _parse_date(context.value_date)
        if days is None or invoice_date is None or paid_on is None:
            reasons.append("the discount window cannot be checked (missing invoice or value date)")
            continue
        deadline = invoice_date + timedelta(days=int(days))
        if paid_on > deadline:
            reasons.append(
                f"the receipt's value date {paid_on.isoformat()} is after the discount "
                f"deadline {deadline.isoformat()}"
            )
            continue
        return ""
    return "; ".join(reasons)


def _deduction_support(context: Any, invoice: str, amount_cents: int, single_invoice: bool) -> str:
    for fact in context.evidence:
        if fact.field != "deduction" or fact.amount_cents != amount_cents:
            continue
        if fact.invoice and fact.invoice.upper() != invoice.upper():
            continue
        if not fact.invoice and not single_invoice:
            continue
        return ""
    return f"no document states a deduction of {format_money(amount_cents)} for {invoice}"


def validate_resolution(
    raw: dict[str, Any],
    context: Any,
    require_citations: bool = True,
) -> Resolution:
    """
    Validate the resolution part of a proposal.

    ``raw`` is the backend's answer (or the reviewer's edited row, at
    apply time).  ``context`` is the :class:`ExceptionContext` with its
    evidence.  ``require_citations`` is switched off at apply time, when
    the reviewer's approval stands in for the citations shown at review.
    """
    resolution = Resolution()
    has_documents = bool(getattr(context, "documents", None))
    raw_allocations = raw.get("proposed_allocations") or []
    if not has_documents and not raw_allocations:
        return resolution  # the legacy, document-free path

    notes = resolution.notes
    sufficiency = normalize_text(raw.get("evidence_sufficiency")).title()
    if sufficiency not in EVIDENCE_SUFFICIENCY:
        sufficiency = "Insufficient" if has_documents else "Sufficient"

    conflicts = [normalize_text(c) for c in raw.get("conflicts") or [] if normalize_text(c)]
    for conflict in getattr(context, "conflicts", []):
        if conflict not in conflicts:
            conflicts.append(conflict)
    resolution.conflicts = conflicts
    if getattr(context, "conflicts", None):
        sufficiency = "Conflicting"
    for warning in getattr(context, "evidence_warnings", []) or []:
        notes.append(warning + ".")

    # --- Citations --------------------------------------------------------
    lines_by_doc = getattr(context, "evidence_lines", {}) or {}
    for item in raw.get("evidence_citations") or []:
        if not isinstance(item, dict):
            continue
        doc_id = normalize_text(item.get("doc_id")).upper()
        quote = normalize_text(item.get("quote"))
        if doc_id not in lines_by_doc:
            notes.append(f"citation of {doc_id or '(no document)'} dropped: not a linked, readable document.")
            continue
        location = _quote_in_document(quote, lines_by_doc[doc_id])
        if location is None:
            notes.append(f"citation of {doc_id} dropped: the quoted text is not in the document.")
            continue
        resolution.citations.append(Citation(doc_id, location, quote))

    # --- Allocations ------------------------------------------------------
    allocations: list[Allocation] = []
    for item in raw_allocations:
        if isinstance(item, Allocation):
            allocations.append(item)
            continue
        if not isinstance(item, dict):
            continue
        invoice = normalize_text(item.get("invoice")).upper()
        cents = _to_cents(item.get("amount"))
        if not invoice or cents is None:
            notes.append(f"allocation {item!r} could not be read; resolution rejected.")
            resolution.status = "Rejected"
            return _finish(resolution, sufficiency, context)
        allocations.append(Allocation(invoice, cents))

    adjustments: list[Adjustment] = []
    for item in raw.get("proposed_adjustments") or []:
        if isinstance(item, Adjustment):
            adjustments.append(item)
            continue
        if not isinstance(item, dict):
            continue
        cents = _to_cents(item.get("amount"))
        adjustments.append(
            Adjustment(
                normalize_text(item.get("invoice")).upper(),
                normalize_text(item.get("type")),
                cents if cents is not None else -1,
            )
        )

    if sufficiency != "Sufficient":
        if allocations or adjustments:
            notes.append(
                f"evidence is {sufficiency.lower()}; no allocation is proposed and the receipt "
                f"stays in review."
            )
        resolution.status = "Needs Review"
        return _finish(resolution, sufficiency, context)

    if not allocations:
        notes.append("no allocation was proposed; the receipt stays in review.")
        resolution.status = "Needs Review"
        return _finish(resolution, sufficiency, context)

    if require_citations and has_documents and not resolution.citations:
        notes.append("no citation could be verified against the documents; the receipt stays in review.")
        resolution.status = "Needs Review"
        return _finish(resolution, sufficiency, context)

    pool = {c.invoice_number.upper(): c for c in context.candidates}
    seen: set[str] = set()
    for allocation in allocations:
        candidate = pool.get(allocation.invoice)
        problem = ""
        if allocation.invoice in seen:
            problem = f"{allocation.invoice} is allocated twice"
        elif candidate is None:
            problem = (
                f"{allocation.invoice} is not open AR of this customer and currency "
                f"(or of a third party named in a document)"
            )
        elif candidate.dispute_reason or candidate.ar_status.lower() in CLOSED_AR_STATUSES:
            problem = (
                f"{allocation.invoice} is {candidate.ar_status.lower() or 'flagged'}"
                f"{' (' + candidate.dispute_reason + ')' if candidate.dispute_reason else ''}; "
                f"cash is not applied to it without a dispute decision"
            )
        elif candidate.recon_status.lower() in ON_HOLD_RECON_STATUSES:
            problem = f"{allocation.invoice} is on hold"
        elif allocation.amount_cents <= 0:
            problem = f"the allocation to {allocation.invoice} is not a positive amount"
        elif allocation.amount_cents > candidate.open_amount_cents:
            problem = (
                f"the allocation of {format_money(allocation.amount_cents)} to {allocation.invoice} "
                f"exceeds its open balance {format_money(candidate.open_amount_cents)}"
            )
        if problem:
            notes.append(f"{problem}; resolution rejected.")
            resolution.status = "Rejected"
            return _finish(resolution, sufficiency, context)
        seen.add(allocation.invoice)
        if candidate.third_party:
            resolution.third_party_invoices.append(allocation.invoice)

    cash_cap = context.gross_amount_cents
    total = sum(a.amount_cents for a in allocations)
    if cash_cap <= 0:
        notes.append("the receipt carries no positive cash; resolution rejected.")
        resolution.status = "Rejected"
        return _finish(resolution, sufficiency, context)
    if total > cash_cap:
        notes.append(
            f"allocations total {format_money(total)} but the receipt carries "
            f"{format_money(cash_cap)}; resolution rejected."
        )
        resolution.status = "Rejected"
        return _finish(resolution, sufficiency, context)

    resolution.allocations = allocations
    status = "Valid"

    # --- Adjustments ------------------------------------------------------
    allocated = {a.invoice: a.amount_cents for a in allocations}
    single_invoice = len(allocations) == 1
    for adjustment in adjustments:
        reason = ""
        candidate = pool.get(adjustment.invoice)
        if adjustment.type not in ADJUSTMENT_TYPES:
            reason = f"'{adjustment.type}' is not an allowed adjustment type"
        elif adjustment.invoice not in allocated or candidate is None:
            reason = f"{adjustment.invoice} receives no cash in this resolution"
        elif adjustment.amount_cents <= 0:
            reason = "the amount is not positive"
        elif allocated[adjustment.invoice] + adjustment.amount_cents > candidate.open_amount_cents:
            reason = (
                f"cash plus adjustment exceeds the open balance "
                f"{format_money(candidate.open_amount_cents)}"
            )
        elif adjustment.type == "Early Payment Discount":
            reason = _discount_support(
                context, adjustment.invoice, adjustment.amount_cents,
                candidate.open_amount_cents, candidate,
            )
        else:
            reason = _deduction_support(
                context, adjustment.invoice, adjustment.amount_cents, single_invoice
            )
        if reason:
            notes.append(
                f"adjustment {adjustment.type} {format_money(max(adjustment.amount_cents, 0))} on "
                f"{adjustment.invoice} dropped: {reason}."
            )
            status = "Downgraded"
            continue
        resolution.adjustments.append(adjustment)

    if resolution.third_party_invoices:
        notes.append(
            "third-party payment: "
            + ", ".join(resolution.third_party_invoices)
            + " belong to "
            + ", ".join(context.third_party_customers or ["another customer"])
            + ", named in a supporting document as the customer being paid for."
        )
    if len(resolution.citations) < len(raw.get("evidence_citations") or []):
        status = "Downgraded"

    resolution.status = status
    resolution.resolution_id = resolution_id(
        context.bank_transaction_id, resolution.allocations, resolution.adjustments
    )
    return _finish(resolution, sufficiency, context)


def _finish(resolution: Resolution, sufficiency: str, context: Any) -> Resolution:
    resolution.sufficiency = sufficiency
    return resolution


def reconcile_recommendation(recommendation: Any, resolution: Resolution, context: Any) -> None:
    """
    Bring the recommendation's summary fields in line with the resolution.

    The single-amount columns of the review sheet are kept for continuity,
    so they must agree with the allocations a reviewer will approve.
    """
    from ar_reconciliation.rules.policies import HOLD_ONLY_SCENARIOS

    if resolution.status == "Not Applicable":
        return
    recommendation.validation_notes.extend(resolution.notes)

    if not resolution.applicable:
        recommendation.proposed_invoices = []
        recommendation.proposed_amount_cents = 0
        recommendation.proposed_residual_cents = 0
        if recommendation.recommended_action in {"Auto Match", "Recommend Match"}:
            recommendation.recommended_action = "Manual Review"
        if resolution.sufficiency == "Conflicting":
            recommendation.confidence = "Low"
        return

    pool = {c.invoice_number.upper(): c for c in context.candidates}
    recommendation.proposed_invoices = [a.invoice for a in resolution.allocations]
    recommendation.proposed_amount_cents = resolution.cash_cents
    adjustments_by_invoice: dict[str, int] = {}
    for adjustment in resolution.adjustments:
        adjustments_by_invoice[adjustment.invoice] = (
            adjustments_by_invoice.get(adjustment.invoice, 0) + adjustment.amount_cents
        )
    recommendation.proposed_residual_cents = sum(
        pool[a.invoice].open_amount_cents - a.amount_cents - adjustments_by_invoice.get(a.invoice, 0)
        for a in resolution.allocations
    )

    if recommendation.recommended_action in {"Manual Review", "Hold / Escalate"}:
        blocked = (
            context.memo_scenario in HOLD_ONLY_SCENARIOS
            and context.memo_scenario not in EVIDENCE_RESOLVABLE_HOLD_SCENARIOS
        )
        if blocked:
            recommendation.validation_notes.append(
                f"scenario '{context.memo_scenario}' needs an accounting decision; the "
                f"evidence-based allocation is shown for information but stays on hold."
            )
            resolution.status = "Needs Review"
            resolution.resolution_id = ""
        else:
            recommendation.recommended_action = "Recommend Match"
            recommendation.validation_notes.append(
                "the supporting documents identify the invoice(s); recommended for a "
                "reviewer to approve."
            )
    if recommendation.recommended_action == "Auto Match":
        recommendation.recommended_action = "Recommend Match"
    if resolution.status == "Downgraded" or resolution.third_party_invoices:
        if recommendation.confidence == "High":
            recommendation.confidence = "Medium"
