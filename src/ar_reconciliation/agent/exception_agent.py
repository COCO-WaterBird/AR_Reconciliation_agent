"""
AI Exception Resolution Agent
=============================

A human-in-the-loop agent that analyses bank receipts which the deterministic
reconciliation engine could not resolve, explains the root cause, and
recommends a specific next action.

Design principles (see README "Design Philosophy"):

    Accounting should be deterministic.  AI should be assistive.

Therefore this module:

  * NEVER decides an accounting outcome on its own.  It produces a
    recommendation with a confidence level and a proposed invoice set.
  * NEVER mutates source data.  The source workbooks are read-only.
  * NEVER writes to the AR ledger.  Recommendations are written to a separate
    review sheet, and are only applied back to AR when a human explicitly
    approves them (``--apply-approved``).
  * NEVER posts a journal entry to a GL.  It only *proposes* GL postings.

Two analysis backends are supported:

  1. ``llm``     An OpenAI-compatible chat-completions endpoint (OpenAI,
                 Azure OpenAI, Ollama, LM Studio, vLLM, ...).  The model
                 receives the receipt, its remittance memo and the candidate
                 open invoices, and returns a structured judgement.
  2. ``rules``   A fully deterministic, offline fallback used when no API key
                 is configured.  It applies the same heuristic scoring that
                 the deterministic engine already uses, so the pipeline stays
                 runnable and testable without network access.

The agent is intentionally conservative.  Any recommendation the validator
cannot prove is safe is downgraded to "Manual Review" rather than guessed.

Usage
-----
    # Analyse exceptions and write the review sheet (no API key needed)
    python scripts/run_agent.py

    # Use an LLM backend
    OPENAI_API_KEY=sk-... python scripts/run_agent.py --model gpt-4o

    # Analyse and then apply only the recommendations a human approved in the
    # "AI Exception Review" sheet (Approval column = APPROVE)
    python scripts/run_agent.py --apply-approved
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ar_reconciliation.agent.context import (
    Candidate,
    ExceptionContext,
    Recommendation,
    build_context,
)
from ar_reconciliation.agent.resolution import (
    Resolution,
    format_adjustments,
    format_allocations,
    format_citations,
    reconcile_recommendation,
    validate_resolution,
)
from ar_reconciliation.agent.rules_backend import rules_backend_analyse
from ar_reconciliation.agent.validator import max_appliable_cents, validate_recommendation
from ar_reconciliation.config import (
    EVIDENCE_DIR,
    RESULT_FILE,
    REVIEW_LOG_SHEET,
    REVIEW_SHEET,
    load_env,
)
from ar_reconciliation.core.primitives import (
    MoneyParseError,
    cents_to_money,
    format_money,
    money_to_cents,
    normalize_key,
    normalize_text,
)
from ar_reconciliation.core.schema import (
    AR_APPLIED_AMOUNT_COL,
    AR_CURRENCY_COL,
    AR_CUSTOMER_ID_COL,
    AR_INVOICE_COL,
    AR_MATCHED_TXN_COL,
    AR_OPEN_AMOUNT_COL,
    AR_RESIDUAL_AMOUNT_COL,
    BANK_ADJUSTMENT_AMOUNT_COL,
    BANK_APPLIED_AMOUNT_TOTAL_COL,
    BANK_HELD_AMOUNT_COL,
    BANK_NET_AMOUNT_COL,
    BANK_TRANSACTION_ID_COL,
    BANK_UNAPPLIED_AMOUNT_COL,
    REVIEW_APPLIED_COL,
    REVIEW_APPROVAL_COL,
    REVIEW_ACCOUNTING_TREATMENT_COL,
    REVIEW_BANK_TXN_COL,
    REVIEW_CANDIDATES_COL,
    REVIEW_CONFIDENCE_COL,
    REVIEW_ENGINE_COL,
    REVIEW_EXCEPTION_TYPE_COL,
    REVIEW_EXPLANATION_COL,
    REVIEW_GL_POSTING_COL,
    REVIEW_PROPOSED_AMOUNT_COL,
    REVIEW_PROPOSED_INVOICES_COL,
    REVIEW_PROPOSED_RESIDUAL_COL,
    REVIEW_RECOMMENDED_ACTION_COL,
    REVIEW_REVIEWER_NOTES_COL,
    REVIEW_ROOT_CAUSE_COL,
    REVIEW_ADJUSTMENTS_COL,
    REVIEW_ALLOCATIONS_COL,
    REVIEW_CITATIONS_COL,
    REVIEW_CONFLICTS_COL,
    REVIEW_DECISION_TS_COL,
    REVIEW_DOCUMENTS_COL,
    REVIEW_EVIDENCE_COL,
    REVIEW_RESOLUTION_ID_COL,
    REVIEW_REVIEWER_COL,
    REVIEW_SUFFICIENCY_COL,
    REVIEW_VALIDATION_COL,
    REVIEW_VALIDATION_NOTES_COL,
    BANK_CURRENCY_COL,
)
from ar_reconciliation.llm.backend import LLMBackend
from ar_reconciliation.services.controls import compute_output_controls
from ar_reconciliation.rules.policies import (
    CONFIDENCE_LEVELS,
    EXCEPTION_TYPES,
    RECOMMENDED_ACTIONS,
)

# ---------------------------------------------------------------------------
# Approval / write-back
# ---------------------------------------------------------------------------

def _decision(value: object) -> str:
    """The reviewer's decision in canonical form (APPROVE, REJECT, MORE_EVIDENCE)."""
    text = "_".join(normalize_text(value).upper().replace("-", " ").split())
    if text in {"REQUEST_MORE_EVIDENCE", "MORE_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
        return "MORE_EVIDENCE"
    return text


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def apply_approved_recommendations(
    result_df: pd.DataFrame,
    ar_df: pd.DataFrame,
    review_df: pd.DataFrame,
    evidence_store: Any | None = None,
    reviewer: str = "",
    records: list[dict[str, Any]] | None = None,
    audit: list[dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """
    Apply the recommendations a human approved in the review sheet.

    Only rows whose Approval cell is exactly "APPROVE" are applied.  The
    approval is a hard gate: nothing is written to AR without it.

    An approval is a decision about a proposal, not a licence to post
    anything typed into the sheet.  The reviewer works in Excel and may have
    edited the invoice or amount cells, so every approved row is validated
    again, against the AR as it stands now, before any cash moves:

      * the receipt must exist in the processed bank receipts and must not
        have been applied already;
      * every invoice must be open AR of the receipt's own customer and
        currency (the same candidate pool the agent was given), and must
        still have an open balance;
      * the amount may not exceed the cash the receipt carries.

    A row that fails any check is rejected as a whole, with the reason in
    the log; nothing is partially posted.  An amount above the invoices'
    remaining balance is capped at that balance and logged as "Adjusted".

    A row with Proposed Allocations is an evidence-based resolution and is
    applied by :func:`_apply_resolution`: every allocation and adjustment is
    validated again against the current AR and the supporting documents, and
    the Resolution ID guarantees it is applied only once.

    REJECT and MORE_EVIDENCE decisions change nothing; they are recorded, and
    the receipt stays in review.  ``records`` receives one approval record
    per decided row and ``audit`` one entry per balance change, when given.

    Returns the updated AR frame, the updated result frame and an audit log.
    """
    ar = ar_df.copy()
    result = result_df.copy()
    # A column that was blank on every row comes back from Excel as float
    # NaN, which cannot take the text written below (a transaction ID, a
    # note, a status).  Such columns are made plain object columns first.
    for frame in (ar, result):
        for column in frame.columns:
            if frame[column].isna().all():
                frame[column] = frame[column].astype(object)
    log: list[dict[str, Any]] = []
    records = records if records is not None else []
    audit = audit if audit is not None else []

    def record(txn_id: str, outcome: str, detail: str) -> None:
        log.append({"Bank Transaction ID": txn_id, "Outcome": outcome, "Detail": detail})

    decisions = review_df[REVIEW_APPROVAL_COL].apply(_decision)

    def approval_record(row: pd.Series, decision: str, outcome: str, detail: str) -> None:
        records.append({
            "Bank Transaction ID": normalize_text(row.get(REVIEW_BANK_TXN_COL, "")),
            "Resolution ID": normalize_text(row.get(REVIEW_RESOLUTION_ID_COL, "")),
            "Decision": decision,
            "Reviewer": normalize_text(row.get(REVIEW_REVIEWER_COL, "")) or normalize_text(reviewer),
            "Decision Timestamp": normalize_text(row.get(REVIEW_DECISION_TS_COL, "")) or _now_iso(),
            "Reviewer Notes": normalize_text(row.get(REVIEW_REVIEWER_NOTES_COL, "")),
            "Approved Allocations": normalize_text(row.get(REVIEW_ALLOCATIONS_COL, ""))
            or (
                f"{normalize_text(row.get(REVIEW_PROPOSED_INVOICES_COL, ''))}: "
                f"{normalize_text(row.get(REVIEW_PROPOSED_AMOUNT_COL, ''))}"
            ),
            "Approved Adjustments": normalize_text(row.get(REVIEW_ADJUSTMENTS_COL, "")),
            "Outcome": outcome,
            "Detail": detail,
            "Processed At": _now_iso(),
        })

    for index, row in review_df.loc[decisions.isin({"REJECT", "MORE_EVIDENCE"})].iterrows():
        # These decisions change nothing, so they are not part of the apply
        # log; they are kept as approval records.
        if decisions[index] == "REJECT":
            detail = "Rejected by the reviewer; nothing was applied and the receipt stays in review."
            approval_record(row, "REJECT", "Not applied", detail)
        else:
            detail = (
                "The reviewer requested more evidence; nothing was applied and the receipt "
                "stays in review."
            )
            approval_record(row, "MORE_EVIDENCE", "More evidence requested", detail)

    approved = review_df.loc[decisions == "APPROVE"]

    if approved.empty:
        return ar, result, log

    for column in (BANK_UNAPPLIED_AMOUNT_COL, BANK_ADJUSTMENT_AMOUNT_COL, BANK_HELD_AMOUNT_COL):
        if column not in result.columns:
            result[column] = ""
        result[column] = result[column].astype(object)

    result_txn_key = result[BANK_TRANSACTION_ID_COL].apply(normalize_key)

    # Idempotency guard: a receipt that has already been applied must not be
    # applied a second time.  Without this a re-run of --apply-approved would
    # settle the same cash twice.
    result_applied_cents = result[BANK_APPLIED_AMOUNT_TOTAL_COL].apply(money_to_cents)
    already_applied_txns: set[str] = {
        txn
        for txn, applied in zip(result_txn_key, result_applied_cents)
        if applied > 0
    }

    for _, row in approved.iterrows():
        txn_id = normalize_text(row.get(REVIEW_BANK_TXN_COL, ""))
        txn_key = normalize_key(txn_id)

        if normalize_text(row.get(REVIEW_ALLOCATIONS_COL, "")):
            log_start = len(log)
            _apply_resolution(
                row, txn_id, txn_key, ar, result, result_txn_key, already_applied_txns,
                evidence_store, record, audit, reviewer,
            )
            entries = log[log_start:]
            outcome = entries[-1]["Outcome"] if entries else "Rejected"
            approval_record(
                row, "APPROVE",
                "Applied" if outcome == "Applied" else ("Already applied" if outcome == "Skipped" else "Rejected"),
                entries[-1]["Detail"] if entries else "",
            )
            continue

        log_start = len(log)
        invoices = [
            v.strip()
            for v in normalize_text(row.get(REVIEW_PROPOSED_INVOICES_COL, "")).split(",")
            if v.strip()
        ]
        try:
            amount_cents = money_to_cents(row.get(REVIEW_PROPOSED_AMOUNT_COL, 0))
        except MoneyParseError:
            record(txn_id, "Rejected", "The approved amount is not a readable number.")
            continue

        if not invoices:
            record(txn_id, "Rejected", "Approved row does not name any invoice.")
            continue

        if txn_key in already_applied_txns:
            record(
                txn_id,
                "Skipped",
                "This receipt has already been applied; it was not applied again.",
            )
            continue

        result_positions = result.index[result_txn_key == txn_key]
        if len(result_positions) != 1:
            record(
                txn_id,
                "Rejected",
                "The receipt is missing from, or repeated in, the processed bank "
                "receipts sheet.",
            )
            continue
        r_idx = result_positions[0]
        bank_row = result.loc[r_idx]

        # --- Re-validate against the AR as it stands now ---------------
        context = build_context(bank_row, bank_row, ar, set())
        pool = {c.invoice_number.upper(): c for c in context.candidates}
        outside_pool = [i for i in invoices if i.upper() not in pool]
        if outside_pool:
            record(
                txn_id,
                "Rejected",
                f"Invoice(s) {', '.join(outside_pool)} are not open AR of this receipt's "
                f"customer and currency, or have no open balance left.",
            )
            continue

        cash_cap = max_appliable_cents(context)
        if amount_cents <= 0 or cash_cap <= 0:
            record(txn_id, "Rejected", "There is no positive amount to apply.")
            continue
        if amount_cents > cash_cap:
            record(
                txn_id,
                "Rejected",
                f"The approved amount {format_money(amount_cents)} exceeds the cash the "
                f"receipt carries ({format_money(cash_cap)}).",
            )
            continue

        # Resolve each invoice to its AR row within the receipt's customer and
        # currency, never across customers.
        ar_invoice_key = ar[AR_INVOICE_COL].apply(normalize_key)
        ar_customer_key = ar[AR_CUSTOMER_ID_COL].apply(normalize_key)
        ar_currency = ar[AR_CURRENCY_COL].apply(lambda v: normalize_text(v).upper())
        target_indexes: list[int] = []
        for invoice_number in invoices:
            candidate = pool[invoice_number.upper()]
            matches = ar.index[
                (ar_invoice_key == normalize_key(invoice_number))
                & (ar_customer_key == normalize_key(candidate.customer_id))
                & (ar_currency == candidate.currency.upper())
            ]
            target_indexes.extend(int(i) for i in matches if int(i) not in target_indexes)
        if len(target_indexes) != len(invoices):
            record(
                txn_id,
                "Rejected",
                "The proposed invoices do not resolve to exactly one AR row each.",
            )
            continue

        remaining_by_index = {
            idx: max(
                money_to_cents(ar.at[idx, AR_OPEN_AMOUNT_COL])
                - money_to_cents(ar.at[idx, AR_APPLIED_AMOUNT_COL]),
                0,
            )
            for idx in target_indexes
        }
        open_cents = sum(remaining_by_index.values())
        applied_cents = min(amount_cents, open_cents)

        if applied_cents != amount_cents:
            record(
                txn_id,
                "Adjusted",
                f"Approved amount {format_money(amount_cents)} capped at the open amount "
                f"{format_money(open_cents)}.",
            )
        else:
            record(
                txn_id,
                "Applied",
                f"Applied {format_money(applied_cents)} to {', '.join(invoices)}.",
            )

        # Distribute the applied cash across the target invoices in the order
        # named; no invoice receives more than its remaining balance.
        running = 0
        for idx in target_indexes:
            share = min(remaining_by_index[idx], applied_cents - running)
            running += share

            previous = money_to_cents(ar.at[idx, AR_APPLIED_AMOUNT_COL])
            cumulative = previous + share
            original_open = money_to_cents(ar.at[idx, AR_OPEN_AMOUNT_COL])
            previous_txns = normalize_text(ar.at[idx, AR_MATCHED_TXN_COL])
            ar.at[idx, AR_MATCHED_TXN_COL] = (
                f"{previous_txns}, {txn_id}" if previous_txns else txn_id
            )
            ar.at[idx, AR_APPLIED_AMOUNT_COL] = cents_to_money(cumulative)
            ar.at[idx, AR_RESIDUAL_AMOUNT_COL] = cents_to_money(max(original_open - cumulative, 0))
            notes = normalize_text(ar.at[idx, "Reconciliation Notes"])
            addition = (
                f"AI-approved application: receipt {txn_id} applied {format_money(share)} to "
                f"{normalize_text(ar.at[idx, AR_INVOICE_COL])}."
            )
            ar.at[idx, "Reconciliation Notes"] = f"{notes} {addition}".strip()
            ar.at[idx, "Suggested Recon Status"] = (
                "Reconciled" if cumulative >= original_open else "Pending Review"
            )

        # --- Update the processed bank receipt --------------------------
        # The receipt leaves the review bucket.  Its gross amount is now
        # split between applied cash, the bank fee, and any cash left on
        # account, so the cash control still ties.
        fee_cents = max(context.bank_fee_cents, 0)
        unapplied_cents = context.gross_amount_cents - fee_cents - applied_cents
        result.at[r_idx, BANK_APPLIED_AMOUNT_TOTAL_COL] = cents_to_money(applied_cents)
        result.at[r_idx, BANK_ADJUSTMENT_AMOUNT_COL] = cents_to_money(fee_cents)
        result.at[r_idx, BANK_UNAPPLIED_AMOUNT_COL] = cents_to_money(unapplied_cents)
        result.at[r_idx, BANK_HELD_AMOUNT_COL] = cents_to_money(0)
        result.at[r_idx, "Suggested Bank Status"] = (
            "Reconciled" if applied_cents >= open_cents and unapplied_cents == 0 else "Pending Review"
        )
        existing_notes = normalize_text(result.at[r_idx, "Processing Notes"])
        result.at[r_idx, "Processing Notes"] = (
            f"{existing_notes} AI-approved match to {', '.join(invoices)} "
            f"({format_money(applied_cents)})."
        ).strip()
        result.at[r_idx, "Match Method"] = "AI Approved Match"
        already_applied_txns.add(txn_key)

    # Approval records for the single-amount (legacy) rows, from their log.
    resolution_txns = {
        normalize_key(r["Bank Transaction ID"]) for r in records if r["Decision"] == "APPROVE"
    }
    for _, row in approved.iterrows():
        key = normalize_key(row.get(REVIEW_BANK_TXN_COL, ""))
        if key in resolution_txns:
            continue
        entries = [e for e in log if normalize_key(e["Bank Transaction ID"]) == key]
        last = entries[-1] if entries else {"Outcome": "Rejected", "Detail": ""}
        outcome = {"Applied": "Applied", "Adjusted": "Applied", "Skipped": "Already applied"}.get(
            last["Outcome"], "Rejected"
        )
        approval_record(row, "APPROVE", outcome, last["Detail"])

    return ar, result, log


def _apply_resolution(
    row: pd.Series,
    txn_id: str,
    txn_key: str,
    ar: pd.DataFrame,
    result: pd.DataFrame,
    result_txn_key: pd.Series,
    already_applied_txns: set[str],
    evidence_store: Any | None,
    record: Any,
    audit: list[dict[str, Any]],
    reviewer: str,
) -> None:
    """
    Apply one approved evidence-based resolution, or reject it with a reason.

    Nothing is written unless every allocation and adjustment passes
    :func:`validate_resolution` against the AR as it stands now.  The
    Resolution ID is recorded on the receipt, so a second attempt is skipped.
    """
    from ar_reconciliation.agent.resolution import (
        parse_adjustments,
        parse_allocations,
        resolution_id,
    )
    from ar_reconciliation.core.schema import (
        BANK_SUPPORTING_DOCS_COL,
        OUT_AI_APPLIED_AMOUNT_COL,
        OUT_APPROVED_ADJUSTMENT_COL,
        OUT_APPROVED_ADJUSTMENT_TYPE_COL,
        OUT_RESOLUTION_ID_COL,
    )

    for frame, columns in (
        (ar, (OUT_AI_APPLIED_AMOUNT_COL, OUT_APPROVED_ADJUSTMENT_COL,
              OUT_APPROVED_ADJUSTMENT_TYPE_COL, OUT_RESOLUTION_ID_COL)),
        (result, (OUT_AI_APPLIED_AMOUNT_COL, OUT_RESOLUTION_ID_COL, BANK_SUPPORTING_DOCS_COL)),
    ):
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.Series([""] * len(frame), index=frame.index, dtype=object)
            else:
                frame[column] = frame[column].astype(object).where(frame[column].notna(), "")

    result_positions = result.index[result_txn_key == txn_key]
    if len(result_positions) != 1:
        record(txn_id, "Rejected",
               "The receipt is missing from, or repeated in, the processed bank receipts sheet.")
        return
    r_idx = result_positions[0]

    if txn_key in already_applied_txns or normalize_text(result.at[r_idx, OUT_RESOLUTION_ID_COL]):
        record(txn_id, "Skipped",
               f"Resolution {normalize_text(result.at[r_idx, OUT_RESOLUTION_ID_COL]) or ''} was "
               f"already applied to this receipt; it was not applied again.".replace("  ", " "))
        return

    try:
        allocations = parse_allocations(row.get(REVIEW_ALLOCATIONS_COL, ""))
        adjustments = parse_adjustments(row.get(REVIEW_ADJUSTMENTS_COL, ""))
    except ValueError as exc:
        record(txn_id, "Rejected", f"The approved resolution cannot be read: {exc}.")
        return

    bank_row = result.loc[r_idx]
    evidence = None
    if evidence_store is not None:
        from ar_reconciliation.evidence.service import evidence_for_receipt

        evidence = evidence_for_receipt(
            evidence_store, txn_id, normalize_text(bank_row.get(BANK_CURRENCY_COL, ""))
        )
        if not evidence.has_documents:
            evidence = None
    context = build_context(bank_row, bank_row, ar, set(), evidence=evidence)

    checked = validate_resolution(
        {
            "proposed_allocations": allocations,
            "proposed_adjustments": adjustments,
            "evidence_sufficiency": "Sufficient",
        },
        context,
        require_citations=False,
    )
    if not checked.applicable:
        record(txn_id, "Rejected",
               "The approved resolution failed revalidation: " + " ".join(checked.notes))
        return
    if len(checked.adjustments) != len(adjustments):
        record(txn_id, "Rejected",
               "An approved adjustment is no longer supported by the documents: "
               + " ".join(checked.notes))
        return

    new_id = resolution_id(txn_id, checked.allocations, checked.adjustments)
    approved_id = normalize_text(row.get(REVIEW_RESOLUTION_ID_COL, ""))
    edited = bool(approved_id) and approved_id != new_id

    # --- Resolve each invoice to exactly one AR row -------------------------
    pool = {c.invoice_number.upper(): c for c in context.candidates}
    ar_invoice_key = ar[AR_INVOICE_COL].apply(normalize_key)
    ar_customer_key = ar[AR_CUSTOMER_ID_COL].apply(normalize_key)
    ar_currency = ar[AR_CURRENCY_COL].apply(lambda v: normalize_text(v).upper())
    targets: dict[str, int] = {}
    for allocation in checked.allocations:
        candidate = pool[allocation.invoice]
        matches = ar.index[
            (ar_invoice_key == normalize_key(allocation.invoice))
            & (ar_customer_key == normalize_key(candidate.customer_id))
            & (ar_currency == candidate.currency.upper())
        ]
        if len(matches) != 1:
            record(txn_id, "Rejected",
                   f"{allocation.invoice} does not resolve to exactly one AR row.")
            return
        targets[allocation.invoice] = int(matches[0])

    adjustment_by_invoice: dict[str, list[Any]] = {}
    for adjustment in checked.adjustments:
        adjustment_by_invoice.setdefault(adjustment.invoice, []).append(adjustment)

    timestamp = _now_iso()
    documents = ", ".join(d.split(" ")[0] for d in context.documents)

    # --- AR -----------------------------------------------------------------
    for allocation in checked.allocations:
        idx = targets[allocation.invoice]
        original_open = money_to_cents(ar.at[idx, AR_OPEN_AMOUNT_COL])
        applied_before = money_to_cents(ar.at[idx, AR_APPLIED_AMOUNT_COL])
        adjusted_before = money_to_cents(ar.at[idx, OUT_APPROVED_ADJUSTMENT_COL])
        remaining_before = max(original_open - applied_before - adjusted_before, 0)
        adjustment_cents = sum(a.amount_cents for a in adjustment_by_invoice.get(allocation.invoice, []))
        applied_after = applied_before + allocation.amount_cents
        adjusted_after = adjusted_before + adjustment_cents
        remaining_after = max(original_open - applied_after - adjusted_after, 0)

        ar.at[idx, AR_APPLIED_AMOUNT_COL] = cents_to_money(applied_after)
        ar.at[idx, OUT_AI_APPLIED_AMOUNT_COL] = cents_to_money(
            money_to_cents(ar.at[idx, OUT_AI_APPLIED_AMOUNT_COL]) + allocation.amount_cents
        )
        if adjustment_cents:
            ar.at[idx, OUT_APPROVED_ADJUSTMENT_COL] = cents_to_money(adjusted_after)
            types = [normalize_text(ar.at[idx, OUT_APPROVED_ADJUSTMENT_TYPE_COL])] + [
                a.type for a in adjustment_by_invoice[allocation.invoice]
            ]
            ar.at[idx, OUT_APPROVED_ADJUSTMENT_TYPE_COL] = ", ".join(dict.fromkeys(t for t in types if t))
        ar.at[idx, AR_RESIDUAL_AMOUNT_COL] = cents_to_money(remaining_after)
        previous_ids = normalize_text(ar.at[idx, OUT_RESOLUTION_ID_COL])
        ar.at[idx, OUT_RESOLUTION_ID_COL] = f"{previous_ids}, {new_id}" if previous_ids else new_id
        previous_txns = normalize_text(ar.at[idx, AR_MATCHED_TXN_COL])
        ar.at[idx, AR_MATCHED_TXN_COL] = f"{previous_txns}, {txn_id}" if previous_txns else txn_id
        note = (
            f"AI-approved resolution {new_id}: receipt {txn_id} applied "
            f"{format_money(allocation.amount_cents)}"
            + (f" and an approved adjustment of {format_money(adjustment_cents)}" if adjustment_cents else "")
            + (f" (third-party payment)" if candidate_is_third_party(pool, allocation.invoice) else "")
            + "."
        )
        notes = normalize_text(ar.at[idx, "Reconciliation Notes"])
        ar.at[idx, "Reconciliation Notes"] = f"{notes} {note}".strip()
        ar.at[idx, "Suggested Recon Status"] = "Reconciled" if remaining_after == 0 else "Pending Review"

        audit.append({
            "Timestamp": timestamp,
            "Event": "Resolution applied",
            "Bank Transaction ID": txn_id,
            "Resolution ID": new_id,
            "Invoice Number": allocation.invoice,
            "Customer ID": pool[allocation.invoice].customer_id,
            "Open Before": cents_to_money(remaining_before),
            "Cash Applied": cents_to_money(allocation.amount_cents),
            "Adjustment Amount": cents_to_money(adjustment_cents),
            "Adjustment Type": ", ".join(a.type for a in adjustment_by_invoice.get(allocation.invoice, [])),
            "Open After": cents_to_money(remaining_after),
            "Supporting Documents": documents,
            "Reviewer": normalize_text(row.get(REVIEW_REVIEWER_COL, "")) or normalize_text(reviewer),
            "Detail": "Third-party payment" if pool[allocation.invoice].third_party else "",
        })

    # --- Bank receipt ---------------------------------------------------------
    gross = context.gross_amount_cents
    applied = checked.cash_cents
    fee = min(max(context.bank_fee_cents, 0), gross - applied)
    unapplied = gross - applied - fee
    result.at[r_idx, BANK_APPLIED_AMOUNT_TOTAL_COL] = cents_to_money(applied)
    result.at[r_idx, BANK_ADJUSTMENT_AMOUNT_COL] = cents_to_money(fee)
    result.at[r_idx, BANK_UNAPPLIED_AMOUNT_COL] = cents_to_money(unapplied)
    result.at[r_idx, BANK_HELD_AMOUNT_COL] = cents_to_money(0)
    result.at[r_idx, OUT_AI_APPLIED_AMOUNT_COL] = cents_to_money(applied)
    result.at[r_idx, OUT_RESOLUTION_ID_COL] = new_id
    result.at[r_idx, BANK_SUPPORTING_DOCS_COL] = documents
    result.at[r_idx, "Suggested Bank Status"] = "Reconciled" if unapplied == 0 else "Pending Review"
    result.at[r_idx, "Match Method"] = "AI Approved Resolution"
    existing_notes = normalize_text(result.at[r_idx, "Processing Notes"])
    result.at[r_idx, "Processing Notes"] = (
        f"{existing_notes} AI-approved resolution {new_id}: "
        f"{format_allocations(checked.allocations)}"
        + (f"; adjustments {format_adjustments(checked.adjustments)}" if checked.adjustments else "")
        + (f"; {format_money(unapplied)} unapplied on account" if unapplied else "")
        + "."
    ).strip()
    already_applied_txns.add(txn_key)

    if unapplied:
        audit.append({
            "Timestamp": timestamp,
            "Event": "Unapplied cash held on account",
            "Bank Transaction ID": txn_id,
            "Resolution ID": new_id,
            "Invoice Number": "",
            "Customer ID": normalize_text(bank_row.get("ERP Customer ID", "")),
            "Open Before": "",
            "Cash Applied": "",
            "Adjustment Amount": "",
            "Adjustment Type": "",
            "Open After": "",
            "Supporting Documents": documents,
            "Reviewer": normalize_text(row.get(REVIEW_REVIEWER_COL, "")) or normalize_text(reviewer),
            "Detail": f"{format_money(unapplied)} of the receipt is not applied to AR.",
        })

    record(
        txn_id,
        "Applied",
        f"Applied resolution {new_id}: {format_allocations(checked.allocations)}"
        + (f"; adjustments {format_adjustments(checked.adjustments)}" if checked.adjustments else "")
        + (f"; {format_money(unapplied)} left unapplied" if unapplied else "")
        + (" (edited by the reviewer and revalidated)" if edited else "")
        + ".",
    )


def candidate_is_third_party(pool: dict[str, Candidate], invoice: str) -> bool:
    candidate = pool.get(invoice.upper())
    return bool(candidate and candidate.third_party)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _evidence_summary(context: ExceptionContext) -> str:
    """One readable line per fact, for the reviewer."""
    lines: list[str] = []
    for fact in context.evidence[:25]:
        amount = (
            f" {format_money(fact.amount_cents)}{' ' + fact.currency if fact.currency else ''}"
            if fact.amount_cents is not None
            else ""
        )
        invoice = f" ({fact.invoice})" if fact.invoice and fact.invoice != fact.value else ""
        lines.append(f"{fact.field}: {fact.value}{invoice}{amount} [{fact.doc_id} {fact.location}]")
    if len(context.evidence) > 25:
        lines.append(f"... {len(context.evidence) - 25} more facts on the Extracted Evidence sheet")
    return "\n".join(lines)


def _evidence_columns(context: ExceptionContext, resolution: Resolution) -> dict[str, Any]:
    """The supporting-document columns of one review row (blank without documents)."""
    documents = list(context.documents)
    if not documents:
        return {
            REVIEW_DOCUMENTS_COL: "",
            REVIEW_EVIDENCE_COL: "",
            REVIEW_CITATIONS_COL: "",
            REVIEW_CONFLICTS_COL: "",
            REVIEW_SUFFICIENCY_COL: "",
            REVIEW_ALLOCATIONS_COL: "",
            REVIEW_ADJUSTMENTS_COL: "",
            REVIEW_VALIDATION_COL: "",
            REVIEW_VALIDATION_NOTES_COL: "",
            REVIEW_RESOLUTION_ID_COL: "",
            REVIEW_REVIEWER_COL: "",
            REVIEW_DECISION_TS_COL: "",
        }
    return {
        REVIEW_DOCUMENTS_COL: "\n".join(documents),
        REVIEW_EVIDENCE_COL: _evidence_summary(context),
        REVIEW_CITATIONS_COL: format_citations(resolution.citations),
        REVIEW_CONFLICTS_COL: "\n".join(resolution.conflicts),
        REVIEW_SUFFICIENCY_COL: resolution.sufficiency,
        REVIEW_ALLOCATIONS_COL: format_allocations(resolution.allocations)
        if resolution.applicable else "",
        REVIEW_ADJUSTMENTS_COL: format_adjustments(resolution.adjustments)
        if resolution.applicable else "",
        REVIEW_VALIDATION_COL: resolution.status,
        REVIEW_VALIDATION_NOTES_COL: "\n".join(resolution.notes),
        REVIEW_RESOLUTION_ID_COL: resolution.resolution_id if resolution.applicable else "",
        REVIEW_REVIEWER_COL: "",
        REVIEW_DECISION_TS_COL: "",
    }


def analyse_exceptions(
    result_path: Path = RESULT_FILE,
    backend_name: str = "rules",
    model: str = "gpt-4o",
    base_url: str | None = None,
    api_key: str | None = None,
    concurrency: int = 1,
    limit: int = 0,
    disable_thinking: bool = False,
    num_ctx: int | None = None,
    num_thread: int | None = None,
    keep_alive: str | int | None = None,
    evidence_store: Any | None = None,
    llm_extract: bool = False,
    evidence_rows: list[dict[str, Any]] | None = None,
    backend_override: Any | None = None,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    "LLMBackend | None",
]:
    """
    Analyse every unmatched receipt and return the review, log and context
    frames together with the updated AR and processed-receipt frames.

    ``evidence_store`` is the :class:`~ar_reconciliation.evidence.DocumentStore`
    holding the supporting documents.  Receipts with linked documents are
    analysed from their evidence; every other receipt is analysed exactly as
    before.  ``llm_extract`` adds the LLM fact extractor (LLM backend only;
    off by default, decision E3).  ``evidence_rows``, when given, receives one
    row per extracted fact for the Extracted Evidence sheet.
    ``backend_override`` replaces the LLM backend (used by tests to supply a
    mocked model).
    """
    if not result_path.exists():
        raise FileNotFoundError(
            f"{result_path.name} was not found. Run scripts/run_reconciliation.py first to produce it."
        )

    result_df = pd.read_excel(result_path, sheet_name="Processed Bank Receipts")
    unmatched_df = pd.read_excel(result_path, sheet_name="Unmatched Bank Receipts")
    ar_df = pd.read_excel(result_path, sheet_name="Reconciled Open AR")

    if limit > 0:
        unmatched_df = unmatched_df.head(limit)

    backend = None
    if backend_name == "llm":
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Use --backend rules to run the offline "
                "deterministic analyzer instead."
            )
        base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        backend = LLMBackend(
            api_key=api_key,
            model=model,
            base_url=base_url,
            disable_thinking=disable_thinking,
            num_ctx=num_ctx,
            num_thread=num_thread,
            keep_alive=keep_alive,
        )
        engine_label = f"LLM ({model})"
    else:
        engine_label = "Rules (deterministic)"
    if backend_override is not None:
        backend = backend_override
        engine_label = f"LLM ({getattr(backend_override, 'model', 'custom')})"

    llm_extractor = None
    if llm_extract and backend is not None and evidence_store is not None:
        from ar_reconciliation.evidence.extraction import LLMExtractor

        llm_extractor = LLMExtractor(backend)

    bank_lookup = {
        normalize_key(row.get(BANK_TRANSACTION_ID_COL, "")): row
        for _, row in result_df.iterrows()
    }

    # Invoices already settled by the deterministic engine.  The agent must not
    # propose them again, because the receipt was already applied to them.
    ar_invoice_key = ar_df[AR_INVOICE_COL].apply(normalize_key)
    ar_applied_cents = ar_df[AR_APPLIED_AMOUNT_COL].apply(money_to_cents)
    ar_open_cents = ar_df[AR_OPEN_AMOUNT_COL].apply(money_to_cents)
    already_matched: set[str] = {
        normalize_text(invoice).upper()
        for invoice, applied, open_cents in zip(ar_invoice_key, ar_applied_cents, ar_open_cents)
        if applied > 0 and applied >= open_cents
    }

    review_rows: list[dict[str, Any]] = []
    log_rows: list[dict[str, Any]] = []

    def analyse_one(unmatched_row: pd.Series) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Analyse one unmatched receipt.  Returns the review row and the log rows."""
        local_log: list[dict[str, Any]] = []
        txn_id = normalize_text(unmatched_row.get(BANK_TRANSACTION_ID_COL, ""))
        bank_row = bank_lookup.get(normalize_key(txn_id))
        if bank_row is None:
            local_log.append({
                "Bank Transaction ID": txn_id,
                "Outcome": "Skipped",
                "Detail": "Receipt is not present in the processed bank receipts sheet.",
            })
            return None, local_log

        evidence = None
        if evidence_store is not None:
            from ar_reconciliation.evidence.service import evidence_for_receipt

            evidence = evidence_for_receipt(
                evidence_store,
                txn_id,
                normalize_text(bank_row.get(BANK_CURRENCY_COL, "")),
                llm_extractor,
            )
            for note in evidence.notes:
                local_log.append({"Bank Transaction ID": txn_id, "Outcome": "Evidence", "Detail": note})
            for unreadable in evidence.unreadable:
                local_log.append({
                    "Bank Transaction ID": txn_id,
                    "Outcome": "Evidence",
                    "Detail": f"Document could not be read: {unreadable}",
                })
            if not evidence.has_documents:
                evidence = None

        context = build_context(
            bank_row, unmatched_row, ar_df, already_matched, evidence=evidence
        )

        if backend is not None:
            try:
                raw = backend.analyse(context)
                source = engine_label
            except Exception as exc:
                local_log.append({
                    "Bank Transaction ID": txn_id,
                    "Outcome": "Model Error",
                    "Detail": f"{type(exc).__name__}: {exc}. Fell back to the rules analyzer.",
                })
                raw = rules_backend_analyse(context)
                source = "Rules (deterministic, model error fallback)"
        else:
            raw = rules_backend_analyse(context)
            source = engine_label

        recommendation = validate_recommendation(raw, context)
        recommendation.analysed_by = source
        resolution = validate_resolution(raw, context)
        reconcile_recommendation(recommendation, resolution, context)

        for note in recommendation.validation_notes:
            local_log.append({
                "Bank Transaction ID": txn_id,
                "Outcome": "Validation",
                "Detail": note,
            })

        review_row = {
            REVIEW_BANK_TXN_COL: recommendation.bank_transaction_id,
            REVIEW_EXCEPTION_TYPE_COL: recommendation.exception_type,
            REVIEW_ROOT_CAUSE_COL: recommendation.root_cause,
            REVIEW_RECOMMENDED_ACTION_COL: recommendation.recommended_action,
            REVIEW_CONFIDENCE_COL: recommendation.confidence,
            REVIEW_PROPOSED_INVOICES_COL: ", ".join(recommendation.proposed_invoices),
            REVIEW_PROPOSED_AMOUNT_COL: cents_to_money(recommendation.proposed_amount_cents),
            REVIEW_PROPOSED_RESIDUAL_COL: cents_to_money(recommendation.proposed_residual_cents),
            REVIEW_CANDIDATES_COL: ", ".join(recommendation.candidates_considered),
            REVIEW_ACCOUNTING_TREATMENT_COL: recommendation.accounting_treatment,
            REVIEW_GL_POSTING_COL: recommendation.gl_posting,
            REVIEW_EXPLANATION_COL: recommendation.explanation,
            REVIEW_ENGINE_COL: recommendation.analysed_by,
            REVIEW_APPROVAL_COL: "",
            REVIEW_REVIEWER_NOTES_COL: "",
            REVIEW_APPLIED_COL: "No",
        }
        review_row.update(_evidence_columns(context, resolution))
        if evidence_rows is not None and context.evidence:
            for fact in context.evidence:
                evidence_rows.append({
                    "Bank Transaction ID": txn_id,
                    "Document ID": fact.doc_id,
                    "Field": fact.field,
                    "Value": fact.value,
                    "Invoice": fact.invoice,
                    "Amount": cents_to_money(fact.amount_cents) if fact.amount_cents is not None else "",
                    "Currency": fact.currency,
                    "Location": fact.location,
                    "Quote": fact.quote,
                    "Extracted By": "LLM (verified)" if fact.method == "llm" else "Rules",
                })
        return review_row, local_log

    # --- Run the analysis, optionally in parallel ---
    #
    # A local model on a laptop takes 20-50 seconds per receipt, so the 64
    # unmatched receipts take a while.  A thread pool shortens that
    # considerably on a local server, because the requests are I/O bound while
    # the model is generating.  The rules backend is fast enough that it stays
    # single threaded.
    unmatched_rows = list(unmatched_df.iterrows())

    if backend is not None and concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(lambda pair: analyse_one(pair[1]), unmatched_rows))
        for review_row, local_log in results:
            if review_row is not None:
                review_rows.append(review_row)
            log_rows.extend(local_log)
    else:
        for _, unmatched_row in unmatched_rows:
            review_row, local_log = analyse_one(unmatched_row)
            if review_row is not None:
                review_rows.append(review_row)
            log_rows.extend(local_log)

    review_df = pd.DataFrame(review_rows)
    # Give the log its headers even when nothing was logged, so the sheet keeps
    # a stable shape instead of turning into a blank tab.
    log_df = pd.DataFrame(log_rows, columns=["Bank Transaction ID", "Outcome", "Detail"])

    # Preserve any approval a reviewer already entered.  Re-running the
    # analysis must not silently discard a human decision.  The engine
    # carries the review sheet over when it regenerates the workbook, so the
    # decisions live there.
    #
    # An approval is only carried over while the proposal itself is
    # unchanged.  If the recommendation moved to a different invoice or a
    # different amount, the old approval no longer applies and the row is
    # sent back for review.
    existing_approvals = read_existing_approvals(result_path)
    if existing_approvals:
        for index, row in review_df.iterrows():
            previous = existing_approvals.get(normalize_key(row[REVIEW_BANK_TXN_COL]))
            if previous is None:
                continue
            same_proposal = (
                normalize_text(previous.get("invoices")).upper()
                == normalize_text(row[REVIEW_PROPOSED_INVOICES_COL]).upper()
                and money_to_cents(previous.get("amount"))
                == money_to_cents(row[REVIEW_PROPOSED_AMOUNT_COL])
                and normalize_text(previous.get("allocations")).upper()
                == normalize_text(row.get(REVIEW_ALLOCATIONS_COL, "")).upper()
                and normalize_text(previous.get("adjustments")).upper()
                == normalize_text(row.get(REVIEW_ADJUSTMENTS_COL, "")).upper()
            )
            if same_proposal and normalize_text(previous.get("approval")):
                review_df.at[index, REVIEW_APPROVAL_COL] = previous["approval"]
                review_df.at[index, REVIEW_REVIEWER_NOTES_COL] = previous.get("notes", "")
                if REVIEW_REVIEWER_COL in review_df.columns:
                    review_df.at[index, REVIEW_REVIEWER_COL] = previous.get("reviewer", "")
                    review_df.at[index, REVIEW_DECISION_TS_COL] = previous.get("decided_at", "")
                if previous.get("applied") == "Yes":
                    review_df.at[index, REVIEW_APPLIED_COL] = "Yes"

    return ar_df, result_df, review_df, log_df, unmatched_df, backend


def read_existing_approvals(result_path: Path) -> dict[str, dict[str, Any]]:
    """
    Read the approvals a reviewer already entered.

    The decisions live in the review sheet inside the result workbook, which
    is where the reviewer works.  The reconciliation engine carries that
    sheet over when it regenerates the workbook, so a decision survives a
    re-run of the engine.
    """
    approvals: dict[str, dict[str, Any]] = {}

    if not result_path.exists():
        return approvals

    try:
        frame = pd.read_excel(result_path, sheet_name=REVIEW_SHEET)
    except Exception:
        return approvals

    if frame.empty or REVIEW_BANK_TXN_COL not in frame.columns:
        return approvals

    for _, row in frame.iterrows():
        txn_id = normalize_text(row.get(REVIEW_BANK_TXN_COL, ""))
        if not txn_id:
            continue
        approvals[normalize_key(txn_id)] = {
            "approval": normalize_text(row.get(REVIEW_APPROVAL_COL, "")),
            "notes": normalize_text(row.get(REVIEW_REVIEWER_NOTES_COL, "")),
            "invoices": normalize_text(row.get(REVIEW_PROPOSED_INVOICES_COL, "")),
            "amount": row.get(REVIEW_PROPOSED_AMOUNT_COL, 0),
            "allocations": normalize_text(row.get(REVIEW_ALLOCATIONS_COL, "")),
            "adjustments": normalize_text(row.get(REVIEW_ADJUSTMENTS_COL, "")),
            "reviewer": normalize_text(row.get(REVIEW_REVIEWER_COL, "")),
            "decided_at": normalize_text(row.get(REVIEW_DECISION_TS_COL, "")),
            "applied": normalize_text(row.get(REVIEW_APPLIED_COL, "")),
        }

    return approvals


def write_review_sheet(
    result_path: Path,
    review_df: pd.DataFrame,
    log_df: pd.DataFrame,
) -> None:
    """Write the review and log sheets into the result workbook."""
    with pd.ExcelWriter(result_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        review_df.to_excel(writer, index=False, sheet_name=REVIEW_SHEET)
        log_df.to_excel(writer, index=False, sheet_name=REVIEW_LOG_SHEET)

    # The reviewer works through this sheet, so it needs the same header
    # freeze, auto-filter and column widths as the engine's own sheets.
    # The formatting helper lives in the io module; it is imported here
    # rather than duplicated, so both writers stay in step.
    try:
        from ar_reconciliation.io.report_views import refresh_report_views
        from ar_reconciliation.io.workbook import apply_workbook_formatting
    except ImportError:  # pragma: no cover - only when imported standalone
        return
    apply_workbook_formatting(result_path)
    refresh_report_views(result_path)


def refresh_summary(
    summary_df: pd.DataFrame, controls: Any, approved_count: int
) -> pd.DataFrame:
    """
    Bring the Summary sheet's control lines up to date after approvals.

    The engine's counts describe the deterministic run and are left as they
    were; the applied-cash and control lines are replaced with the values
    after the approved applications, and one line records how many were
    applied.
    """
    from ar_reconciliation.services.controls import summary_rows

    m = cents_to_money
    updates: dict[str, Any] = {
        "Reconciled AR applied amount": m(controls.ar_applied_cents),
        "Reconciled bank applied amount": m(controls.applied_cents),
        "Reconciled control difference": m(controls.applied_difference_cents),
    }
    updates.update({metric: value for metric, value in summary_rows(controls)})

    summary = summary_df.copy()
    summary["Value"] = summary["Value"].astype(object)
    for metric, value in updates.items():
        mask = summary["Metric"] == metric
        if mask.any():
            summary.loc[mask, "Value"] = value

    line = "AI-approved applications applied"
    if (summary["Metric"] == line).any():
        summary.loc[summary["Metric"] == line, "Value"] = approved_count
    else:
        summary = pd.concat(
            [summary, pd.DataFrame([[line, approved_count]], columns=["Metric", "Value"])],
            ignore_index=True,
        )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse unreconciled bank receipts and recommend next actions.",
    )
    parser.add_argument(
        "--result",
        type=Path,
        default=RESULT_FILE,
        help="Reconciliation result workbook to analyse (default: %(default)s)",
    )
    parser.add_argument(
        "--backend",
        choices=("rules", "llm"),
        default="rules",
        help="Analysis backend. 'rules' is deterministic and offline; 'llm' calls an "
        "OpenAI-compatible endpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AR_AGENT_MODEL", "gpt-4o"),
        help="Model identifier for the LLM backend (default: %(default)s)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL"),
        help="Custom API base URL for the LLM backend (e.g. http://localhost:11434/v1)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key for the LLM backend (default: the OPENAI_API_KEY environment variable)",
    )
    parser.add_argument(
        "--no-think",
        dest="disable_thinking",
        action="store_true",
        help="Disable the reasoning mode of the qwen3 family of models. It is slower "
        "and competes with the schema constraint for the token budget.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Analyse only the first N unmatched receipts (0 = all). Useful for a "
        "smoke test against a local model.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of receipts to analyse in parallel. Useful with a local model, "
        "where each receipt takes 20-50 seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="Context window for a local model, in tokens. A smaller window keeps the "
        "KV cache small and the machine responsive (default: the server's own setting)",
    )
    parser.add_argument(
        "--num-thread",
        type=int,
        default=None,
        help="CPU threads a local model may use. Capping this keeps the desktop "
        "responsive while the model runs (default: the server's own setting)",
    )
    parser.add_argument(
        "--keep-alive",
        default=None,
        help="How long a local model stays loaded after the last request, e.g. '5m' "
        "or '0' to unload it immediately and give the memory back (default: the "
        "server's own setting)",
    )
    parser.add_argument(
        "--apply-approved",
        action="store_true",
        help="Apply the recommendations approved in the review sheet (Approval = APPROVE) "
        "back to AR, then re-run the control totals.",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=EVIDENCE_DIR if EVIDENCE_DIR.exists() else None,
        help="Supporting-document store (see ar-evidence). Receipts with linked documents "
        "are analysed from their evidence (default: data/evidence when it exists)",
    )
    parser.add_argument(
        "--llm-extract",
        action="store_true",
        help="Also extract document facts with the LLM (LLM backend only). Off by default: "
        "only masked facts and short quotes are otherwise sent to the model.",
    )
    parser.add_argument(
        "--reviewer",
        default=os.environ.get("AR_REVIEWER", ""),
        help="Name recorded in the approval records and audit trail for decisions that "
        "do not carry a reviewer name in the sheet.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Load .env before the parser is built: the parser reads the
    # environment for its defaults, so the file has to be in place first.
    load_env()

    args = build_parser().parse_args(argv)

    print("=" * 72)
    print("AI Exception Resolution Agent")
    print("=" * 72)

    from ar_reconciliation.agent.workflow import apply_decisions, run_analysis

    analysis = run_analysis(
        args.result,
        backend_name=args.backend,
        evidence_dir=args.evidence_dir,
        llm_extract=args.llm_extract,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        concurrency=max(1, args.concurrency),
        limit=max(0, args.limit),
        disable_thinking=args.disable_thinking,
        num_ctx=args.num_ctx,
        num_thread=args.num_thread,
        keep_alive=args.keep_alive,
    )
    review_df, backend = analysis.review_df, analysis.backend

    if review_df.empty:
        print("No unmatched receipts to analyse.")
        return 0

    # --- Summary ---
    by_action = review_df[REVIEW_RECOMMENDED_ACTION_COL].value_counts().to_dict()
    by_confidence = review_df[REVIEW_CONFIDENCE_COL].value_counts().to_dict()
    by_exception = review_df[REVIEW_EXCEPTION_TYPE_COL].value_counts().to_dict()

    print(f"\nAnalysed {len(review_df)} unmatched receipts with the {review_df[REVIEW_ENGINE_COL].iloc[0]} backend.")
    print("\nRecommended actions:")
    for action, count in by_action.items():
        print(f"  {action:<24} {count}")
    print("\nConfidence:")
    for level in CONFIDENCE_LEVELS:
        if level in by_confidence:
            print(f"  {level:<24} {by_confidence[level]}")
    print("\nException types:")
    for exception_type, count in sorted(by_exception.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {exception_type:<28} {count}")

    # On a paid endpoint the token totals are what turns into the bill, so
    # they are worth showing.  The rules backend never calls the model.
    if backend is not None:
        prompt_tokens = getattr(backend, "prompt_tokens", 0)
        completion_tokens = getattr(backend, "completion_tokens", 0)
        if prompt_tokens or completion_tokens:
            total_tokens = prompt_tokens + completion_tokens
            print(
                f"\nTokens used: {prompt_tokens} prompt + {completion_tokens} completion "
                f"= {total_tokens} total"
            )

    if not analysis.evidence_df.empty:
        resolved = int((review_df[REVIEW_VALIDATION_COL] == "Valid").sum() + (review_df[REVIEW_VALIDATION_COL] == "Downgraded").sum())
        print(
            f"\nSupporting documents: {len(analysis.documents_df)} stored; "
            f"{analysis.evidence_df['Bank Transaction ID'].nunique()} receipts with evidence; "
            f"{resolved} evidence-based resolutions ready for review."
        )
    print(f"\nWrote '{REVIEW_SHEET}' and '{REVIEW_LOG_SHEET}' to {args.result.name}")

    if not args.apply_approved:
        print(
            "\nNo change was made to AR. Review the sheet, set Approval = APPROVE on the rows "
            "you accept, then re-run with --apply-approved."
        )
        return 0

    # --- Apply the human decisions ---
    print("\nApplying approved recommendations...")
    # The review sheet is read again, so decisions made in Excel or the review
    # UI are honoured.
    outcome = apply_decisions(args.result, evidence_dir=args.evidence_dir, reviewer=args.reviewer)
    controls = outcome.controls

    print(f"Approved recommendations applied : {outcome.applied_count}")
    print(f"Approved recommendations rejected: {outcome.rejected_count}")
    print(f"AR applied amount total           : {format_money(controls.ar_applied_cents)}")
    print(f"Bank applied amount total         : {format_money(controls.applied_cents)}")
    print(f"Cash control difference           : {format_money(controls.cash_difference_cents)}")
    print(f"AR roll-forward difference        : {format_money(controls.rollforward_difference_cents)}")
    print(f"Run status                        : {outcome.run_status}")

    if outcome.failed_path is not None:
        print(
            "\nERROR: the controls do not balance after applying the approvals. "
            "The result workbook was NOT updated.\n  " + controls.describe_breaks()
            + f"\n  Diagnostics: {outcome.failed_path.name}"
        )
        return 1

    print(f"\nUpdated {args.result.name} with the approved applications.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
