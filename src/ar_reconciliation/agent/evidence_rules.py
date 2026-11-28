"""
The deterministic evidence analysis.

This is the offline counterpart of the LLM's evidence reasoning, and the
path the rules backend takes when a receipt has supporting documents.  It
reads only the extracted facts and the candidate pool, and it proposes a
resolution only when the documents say so plainly:

  * a remittance or email that allocates amounts to named invoices;
  * a single invoice named in a document, paid in full, in part or as a
    scheduled instalment;
  * several invoices named in a document whose open balances add up exactly
    to the receipt;
  * a short payment explained by a stated deduction or by early-payment
    discount terms, which become proposed adjustments (decision E1).

When the documents conflict, name no usable invoice, or allocate more than
the receipt carries, it says so and proposes nothing.  Every number it
proposes is still checked again by :func:`validate_resolution`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ar_reconciliation.agent.resolution import max_discount_cents
from ar_reconciliation.core.primitives import format_money
from ar_reconciliation.rules.policies import SCENARIO_TO_EXCEPTION_TYPE

__all__ = ["analyse_with_evidence"]


def _cite(fact: Any) -> dict[str, str]:
    return {"doc_id": fact.doc_id, "location": fact.location, "quote": fact.quote}


def _review(context: Any, sufficiency: str, reason: str, cited: list[Any], conflicts: list[str]) -> dict[str, Any]:
    documents = ", ".join(context.documents)
    return {
        "exception_type": SCENARIO_TO_EXCEPTION_TYPE.get(context.memo_scenario, "Unknown"),
        "root_cause": reason,
        "recommended_action": "Manual Review",
        "confidence": "Low",
        "proposed_invoices": [],
        "proposed_amount": 0,
        "proposed_residual": 0,
        "accounting_treatment": "No cash is applied. The receipt stays in Needs Review until "
        "the evidence is complete and consistent.",
        "gl_posting": "No GL posting is required.",
        "explanation": f"Reviewed {documents}. {reason} Request clarified remittance detail "
        f"from the customer before applying the cash.",
        "proposed_allocations": [],
        "proposed_adjustments": [],
        "evidence_citations": [_cite(f) for f in cited],
        "conflicts": conflicts,
        "evidence_sufficiency": sufficiency,
    }


def _discount_fits(context: Any, candidate: Any, short_cents: int) -> Any | None:
    """The discount-terms fact that explains a short payment exactly, if any."""
    paid_on = _date(context.value_date)
    invoice_date = _date(candidate.invoice_date)
    for fact in context.evidence:
        if fact.field != "discount_terms" or not fact.details.get("discount_bp"):
            continue
        if max_discount_cents(candidate.open_amount_cents, int(fact.details["discount_bp"])) != short_cents:
            continue
        days = fact.details.get("discount_days")
        if paid_on and invoice_date and days is not None:
            if paid_on <= invoice_date + timedelta(days=int(days)):
                return fact
    return None


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def analyse_with_evidence(context: Any) -> dict[str, Any] | None:
    """
    Propose a resolution from the documents, or ``None`` when the receipt
    has no documents (the caller then uses the original rules analysis).
    """
    if not context.documents:
        return None
    if context.gross_amount_cents <= 0:
        return None  # refunds and reversals are never applied; the original rules hold them

    facts = context.evidence
    if not facts:
        reason = "The linked documents contain no readable payment facts."
        if context.unreadable_documents:
            reason = "The linked documents could not be read: " + "; ".join(context.unreadable_documents) + "."
        return _review(context, "Insufficient", reason, [], [])

    if context.conflicts:
        cited = [f for f in facts if f.field in {"allocation", "payment_amount", "discount_terms"}]
        return _review(
            context,
            "Conflicting",
            "The supporting documents disagree: " + "; ".join(context.conflicts) + ".",
            cited,
            list(context.conflicts),
        )

    pool = {c.invoice_number.upper(): c for c in context.candidates}
    cash = context.gross_amount_cents
    warnings: list[str] = []
    outside = sorted(
        {f.invoice.upper() for f in facts if f.invoice and f.invoice.upper() not in pool}
    )
    if context.evidence_warnings:
        warnings.append(
            "One document contains text addressed to an AI system; it was ignored. "
            "Confirm the remittance with the customer."
        )
    if outside:
        warnings.append(
            "The documents also name " + ", ".join(outside) + ", which are not open AR of this "
            "customer and currency; they were ignored."
        )

    cited: list[Any] = []
    allocations: dict[str, int] = {}
    kind = ""

    allocation_facts = [f for f in facts if f.field == "allocation" and f.invoice.upper() in pool]
    if allocation_facts:
        for fact in allocation_facts:
            invoice = fact.invoice.upper()
            if invoice not in allocations:
                allocations[invoice] = fact.amount_cents or 0
                cited.append(fact)
        kind = "remittance"
    else:
        referenced: list[str] = []
        for fact in facts:
            invoice = fact.invoice.upper()
            if fact.field == "invoice_number" and invoice in pool and invoice not in referenced:
                referenced.append(invoice)
                cited.append(fact)
        if len(referenced) == 1:
            invoice = referenced[0]
            open_cents = pool[invoice].open_amount_cents
            allocations[invoice] = min(cash, open_cents)
            kind = "single"
        elif len(referenced) > 1:
            total_open = sum(pool[i].open_amount_cents for i in referenced)
            if total_open == cash:
                for invoice in referenced:
                    allocations[invoice] = pool[invoice].open_amount_cents
                kind = "multi"
            else:
                return _review(
                    context,
                    "Insufficient",
                    f"The documents name {', '.join(referenced)} (open "
                    f"{format_money(total_open)}) but do not say how the "
                    f"{format_money(cash)} receipt is split between them.",
                    cited,
                    [],
                )

    if not allocations:
        return _review(
            context,
            "Insufficient",
            "The documents do not name an open invoice of this customer." + (" " + warnings[0] if warnings else ""),
            [f for f in facts if f.invoice][:5],
            [],
        )

    total = sum(allocations.values())
    if total > cash:
        return _review(
            context,
            "Conflicting",
            f"The documents allocate {format_money(total)} but the receipt carries "
            f"{format_money(cash)}.",
            cited,
            [f"Documents allocate {format_money(total)}; the receipt is {format_money(cash)}"],
        )
    for invoice, amount in allocations.items():
        if amount > pool[invoice].open_amount_cents:
            return _review(
                context,
                "Conflicting",
                f"The documents allocate {format_money(amount)} to {invoice}, more than its open "
                f"balance {format_money(pool[invoice].open_amount_cents)}.",
                cited,
                [f"{invoice} allocation exceeds its open balance"],
            )

    # --- Explain any short payment ---------------------------------------
    adjustments: list[dict[str, Any]] = []
    descriptions: list[str] = []
    partial_invoices: list[str] = []
    single = len(allocations) == 1
    for invoice, amount in allocations.items():
        candidate = pool[invoice]
        short = candidate.open_amount_cents - amount
        if short <= 0:
            continue
        deduction = next(
            (
                f for f in facts
                if f.field == "deduction" and f.amount_cents == short
                and ((f.invoice and f.invoice.upper() == invoice) or (not f.invoice and single))
            ),
            None,
        )
        if deduction is not None:
            adjustments.append({"invoice": invoice, "type": "Customer Deduction", "amount": short / 100})
            cited.append(deduction)
            descriptions.append(f"a {format_money(short)} customer deduction on {invoice}")
            continue
        discount = _discount_fits(context, candidate, short)
        if discount is not None:
            adjustments.append({"invoice": invoice, "type": "Early Payment Discount", "amount": short / 100})
            cited.append(discount)
            descriptions.append(f"a {format_money(short)} early-payment discount on {invoice} ({discount.value})")
            continue
        partial_invoices.append(invoice)

    installment = next(
        (f for f in facts if f.field == "installment" and f.amount_cents == total), None
    )
    if installment is not None:
        cited.append(installment)
    payer = next((f for f in facts if f.field == "payer_on_behalf_of"), None)
    third_party = [i for i in allocations if pool[i].third_party]
    if third_party and payer is not None:
        cited.append(payer)
    totals = [f for f in facts if f.field == "payment_amount" and f.amount_cents == cash]
    cited.extend(totals[:1])

    unapplied = cash - total

    # --- Classify and explain ---------------------------------------------
    if third_party:
        exception_type = "Wrong Legal Entity"
    elif adjustments:
        exception_type = "Short Payment"
    elif installment is not None or partial_invoices:
        exception_type = "Partial Payment"
    elif unapplied > 0:
        exception_type = "Overpayment"
    else:
        exception_type = SCENARIO_TO_EXCEPTION_TYPE.get(context.memo_scenario, "Unknown")
        if exception_type in {"Unknown", "On-Account / Unapplied", "Duplicate Payment"}:
            exception_type = "Ambiguous Invoice Reference"

    allocation_text = ", ".join(f"{format_money(a)} to {i}" for i, a in allocations.items())
    parts = [f"Apply {allocation_text}."]
    if descriptions:
        parts.append("Close the difference as " + " and ".join(descriptions) + ".")
    if installment is not None:
        parts.append(f"The amount matches a scheduled instalment ({installment.location}).")
    if partial_invoices:
        parts.append("Leave the remaining balance open on " + ", ".join(partial_invoices) + ".")
    if unapplied > 0:
        parts.append(f"Hold {format_money(unapplied)} as unapplied cash on account.")
    if third_party:
        parts.append(
            f"The payer pays on behalf of {payer.value if payer else 'another customer'}; "
            f"confirm the relationship before approving."
        )
    parts.extend(warnings)

    root_cause = {
        "remittance": "The bank memo did not carry the allocation; the customer's remittance "
        "or email states it.",
        "single": "The bank memo did not identify the invoice; a supporting document does.",
        "multi": "The receipt pays several invoices that only the supporting document names.",
    }[kind]

    high = (
        kind == "remittance"
        and not adjustments
        and not partial_invoices
        and unapplied == 0
        and not third_party
        and not warnings
    )
    return {
        "exception_type": exception_type,
        "root_cause": root_cause,
        "recommended_action": "Recommend Match",
        "confidence": "High" if high else "Medium",
        "proposed_invoices": list(allocations),
        "proposed_amount": total / 100,
        "proposed_residual": 0,
        "accounting_treatment": (
            "Apply the cash as allocated"
            + ("; record the approved adjustment(s) as non-cash credits to AR" if adjustments else "")
            + ("; hold the excess on account" if unapplied > 0 else "")
            + "."
        ),
        "gl_posting": (
            "Dr Cash / Cr Accounts Receivable for the applied cash"
            + ("; Dr Sales Discounts or Deductions / Cr Accounts Receivable for the adjustment"
               if adjustments else "")
            + ("; Cr Unapplied Cash for the excess" if unapplied > 0 else "")
            + "."
        ),
        "explanation": " ".join(parts),
        "proposed_allocations": [
            {"invoice": i, "amount": a / 100} for i, a in allocations.items()
        ],
        "proposed_adjustments": adjustments,
        "evidence_citations": [_cite(f) for f in cited],
        "conflicts": [],
        "evidence_sufficiency": "Sufficient",
    }
