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
from ar_reconciliation.agent.rules_backend import rules_backend_analyse
from ar_reconciliation.agent.validator import max_appliable_cents, validate_recommendation
from ar_reconciliation.config import (
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

def apply_approved_recommendations(
    result_df: pd.DataFrame,
    ar_df: pd.DataFrame,
    review_df: pd.DataFrame,
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

    Returns the updated AR frame, the updated result frame and an audit log.
    """
    ar = ar_df.copy()
    result = result_df.copy()
    log: list[dict[str, Any]] = []

    def record(txn_id: str, outcome: str, detail: str) -> None:
        log.append({"Bank Transaction ID": txn_id, "Outcome": outcome, "Detail": detail})

    approved = review_df.loc[
        review_df[REVIEW_APPROVAL_COL].fillna("").astype(str).str.strip().str.upper() == "APPROVE"
    ]

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

    return ar, result, log


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

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

        context = build_context(bank_row, unmatched_row, ar_df, already_matched)

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
            )
            if same_proposal and normalize_text(previous.get("approval")):
                review_df.at[index, REVIEW_APPROVAL_COL] = previous["approval"]
                review_df.at[index, REVIEW_REVIEWER_NOTES_COL] = previous.get("notes", "")

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
        from ar_reconciliation.io.workbook import apply_workbook_formatting
    except ImportError:  # pragma: no cover - only when imported standalone
        return
    apply_workbook_formatting(result_path)


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Load .env before the parser is built: the parser reads the
    # environment for its defaults, so the file has to be in place first.
    load_env()

    args = build_parser().parse_args(argv)

    print("=" * 72)
    print("AI Exception Resolution Agent")
    print("=" * 72)

    ar_df, result_df, review_df, log_df, unmatched_df, backend = analyse_exceptions(
        result_path=args.result,
        backend_name=args.backend,
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

    write_review_sheet(args.result, review_df, log_df)
    print(f"\nWrote '{REVIEW_SHEET}' and '{REVIEW_LOG_SHEET}' to {args.result.name}")

    if not args.apply_approved:
        print(
            "\nNo change was made to AR. Review the sheet, set Approval = APPROVE on the rows "
            "you accept, then re-run with --apply-approved."
        )
        return 0

    # --- Apply the human approvals ---
    print("\nApplying approved recommendations...")
    # Re-read the review sheet so the human edits made in Excel are honoured.
    approved_review = pd.read_excel(args.result, sheet_name=REVIEW_SHEET)
    ar_updated, result_updated, apply_log = apply_approved_recommendations(
        result_df=result_df, ar_df=ar_df, review_df=approved_review
    )

    if apply_log:
        log_df = pd.concat([log_df, pd.DataFrame(apply_log)], ignore_index=True)

    # --- Re-run the controls on the updated data ---
    controls = compute_output_controls(ar_updated, result_updated)

    applied_txns = {
        normalize_key(entry["Bank Transaction ID"])
        for entry in apply_log
        if entry["Outcome"] in {"Applied", "Adjusted"}
    }
    applied_count = len(applied_txns)
    rejected_count = sum(1 for entry in apply_log if entry["Outcome"] == "Rejected")
    print(f"Approved recommendations applied : {applied_count}")
    print(f"Approved recommendations rejected: {rejected_count}")
    print(f"AR applied amount total           : {format_money(controls.ar_applied_cents)}")
    print(f"Bank applied amount total         : {format_money(controls.applied_cents)}")
    print(f"Cash control difference           : {format_money(controls.cash_difference_cents)}")
    print(f"AR roll-forward difference        : {format_money(controls.rollforward_difference_cents)}")

    if not controls.balanced:
        print(
            "\nERROR: the controls do not balance after applying the approvals. "
            "The result workbook was NOT updated.\n  " + controls.describe_breaks()
        )
        return 1

    if REVIEW_APPLIED_COL in review_df.columns:
        review_df[REVIEW_APPLIED_COL] = review_df[REVIEW_APPLIED_COL].astype(object)
        applied_mask = review_df[REVIEW_BANK_TXN_COL].apply(normalize_key).isin(applied_txns)
        review_df.loc[applied_mask, REVIEW_APPLIED_COL] = "Yes"

    summary_df = refresh_summary(
        pd.read_excel(args.result, sheet_name="Summary"), controls, applied_count
    )

    with pd.ExcelWriter(args.result, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        ar_updated.to_excel(writer, index=False, sheet_name="Reconciled Open AR")
        result_updated.to_excel(writer, index=False, sheet_name="Processed Bank Receipts")
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        review_df.to_excel(writer, index=False, sheet_name=REVIEW_SHEET)
        log_df.to_excel(writer, index=False, sheet_name=REVIEW_LOG_SHEET)

    # Replacing a sheet drops its styling, so the report formatting is
    # applied again over the whole workbook.
    from ar_reconciliation.io.workbook import apply_workbook_formatting

    apply_workbook_formatting(args.result)

    print(f"\nUpdated {args.result.name} with the approved applications.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
