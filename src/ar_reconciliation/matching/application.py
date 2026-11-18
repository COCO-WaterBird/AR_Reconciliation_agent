"""
Writing a match back to the workbook.

This module owns the invariant that makes the control totals balance:

    SUM(Applied Amount across all matched AR rows) == Bank Gross Receipt Amount

It also owns the guarantee that source columns are never touched.  Every
write here goes to an output column.
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.core.primitives import (
    cents_to_money,
    money_to_cents,
    normalize_invoice,
    normalize_text,
)
from ar_reconciliation.core.schema import (
    AR_INVOICE_COL,
    BANK_APPLIED_AMOUNT_TOTAL_COL,
    BANK_PROCESSING_NOTES_COL,
    BANK_RECON_BATCH_ID_COL,
    BANK_TRANSACTION_ID_COL,
    BANK_VALUE_DATE_COL,
    OUT_APPLIED_AMOUNT_COL,
    OUT_MATCHED_AMOUNT_COL,
    OUT_MATCHED_BANK_TXN_COL,
    OUT_MATCHED_PAYMENT_DATE_COL,
    OUT_MATCH_METHOD_BANK_COL,
    OUT_MATCH_METHOD_COL,
    OUT_RECON_NOTES_COL,
    OUT_RESIDUAL_AMOUNT_COL,
    OUT_SUGGESTED_BANK_STATUS_COL,
    OUT_SUGGESTED_RECON_STATUS_COL,
)
from ar_reconciliation.matching.models import MatchResult

__all__ = ["apply_match", "build_payment_signature"]

#: Match methods that leave something outstanding, so the invoice is not
#: fully closed even when the residual is zero.
_UNSETTLED_METHOD_MARKERS = (
    "Fee",
    "Rounding",
    "Partial",
    "Overpayment",
    "Short Payment",
    "Small Residual",
)


def apply_match(
    ar_df: pd.DataFrame,
    bank_df: pd.DataFrame,
    bank_index: int,
    bank_row: pd.Series,
    result: MatchResult,
    currency_key: str,
    close_invoice: bool,
) -> tuple[int, int]:
    """
    Write reconciliation results back to dataframes.

    GUARANTEE: Only new output columns are written.
    The following source columns are NEVER modified:
      - "Open Amount"
      - "AR Status"
      - "Reconciliation Status"
      - "Status"

    Returns:
        The number of receipts applied (always 1) and the number of AR rows
        the match touched.
    """
    batch_id = f"AUTO-{bank_row[BANK_TRANSACTION_ID_COL]}"
    invoice_numbers = ar_df.loc[result.ar_indexes, AR_INVOICE_COL].astype(str).tolist()

    # Use one shared rule for both AR and bank status.  This prevents one
    # side from being marked Reconciled while the other side remains Pending
    # Review.
    is_fully_reconciled = (
        close_invoice
        and result.residual_cents == 0
        and not any(marker in result.method for marker in _UNSETTLED_METHOD_MARKERS)
    )
    suggested_ar_status = "Reconciled" if is_fully_reconciled else "Pending Review"

    # --- Pre-compute per-invoice applied_cents so they sum to result.applied_cents ---
    #
    # Core invariant:
    #   SUM(Applied Amount across all matched AR rows) == Bank Gross Receipt Amount
    #
    # Strategy:
    #   Each invoice gets its own open amount as its share.
    #   The last invoice absorbs any rounding remainder so the total is exact.
    #
    invoice_open_cents_list = [
        int(ar_df.at[idx, "__ar_open_amount_cents"]) for idx in result.ar_indexes
    ]
    total_invoice_open = sum(invoice_open_cents_list)

    if close_invoice:
        # Distribute result.applied_cents proportionally by each invoice open
        # amount.  The last invoice absorbs the rounding remainder so
        # SUM == result.applied_cents exactly.
        per_invoice_applied: list[int] = []
        running = 0
        for i, open_c in enumerate(invoice_open_cents_list):
            if i == len(invoice_open_cents_list) - 1:
                # Last invoice: remainder ensures exact total.
                per_invoice_applied.append(result.applied_cents - running)
            else:
                if total_invoice_open > 0:
                    # Proportional share: invoice_open / total_open * applied_total.
                    share = int(
                        round(open_c / total_invoice_open * result.applied_cents)
                    )
                else:
                    share = 0
                per_invoice_applied.append(share)
                running += share
        per_invoice_residual = [0] * len(result.ar_indexes)
    else:
        # Partial / short-pay: only ever single-invoice;
        # applied = result.applied_cents.
        per_invoice_applied = [result.applied_cents]
        per_invoice_residual = [
            max(invoice_open_cents_list[0] - result.applied_cents, 0)
        ]

    # --- Write per-invoice output columns ---
    for i, idx in enumerate(result.ar_indexes):
        applied_cents = per_invoice_applied[i]
        residual_cents = per_invoice_residual[i]

        # An invoice may be paid by several receipts (instalments).  The
        # applied amount, the transaction list and the residual are therefore
        # accumulated rather than overwritten, so the AR row shows the full
        # settlement history and the remaining balance.
        previous_applied = money_to_cents(ar_df.at[idx, OUT_APPLIED_AMOUNT_COL])
        cumulative_applied = previous_applied + applied_cents

        original_open_cents = int(ar_df.at[idx, "__ar_original_open_cents"])
        remaining_balance = max(original_open_cents - cumulative_applied, 0)
        excess = max(cumulative_applied - original_open_cents, 0)
        if close_invoice:
            residual_cents = excess
        else:
            residual_cents = remaining_balance + excess

        previous_txns = normalize_text(ar_df.at[idx, OUT_MATCHED_BANK_TXN_COL])
        matched_txn_id = bank_row[BANK_TRANSACTION_ID_COL]
        if previous_txns and matched_txn_id not in previous_txns.split(", "):
            matched_txn_id = f"{previous_txns}, {matched_txn_id}"

        previous_dates = normalize_text(ar_df.at[idx, OUT_MATCHED_PAYMENT_DATE_COL])
        payment_date = bank_row[BANK_VALUE_DATE_COL]
        if previous_dates:
            payment_date = f"{previous_dates}, {payment_date}"

        ar_df.at[idx, OUT_MATCHED_BANK_TXN_COL] = matched_txn_id
        ar_df.at[idx, OUT_MATCHED_PAYMENT_DATE_COL] = payment_date
        ar_df.at[idx, OUT_MATCHED_AMOUNT_COL] = cents_to_money(cumulative_applied)
        ar_df.at[idx, OUT_APPLIED_AMOUNT_COL] = cents_to_money(cumulative_applied)
        ar_df.at[idx, OUT_RESIDUAL_AMOUNT_COL] = cents_to_money(residual_cents)
        ar_df.at[idx, OUT_MATCH_METHOD_COL] = result.method
        ar_df.at[idx, OUT_SUGGESTED_RECON_STATUS_COL] = suggested_ar_status
        ar_df.at[idx, OUT_RECON_NOTES_COL] = (
            f"{result.method}: bank receipt {bank_row[BANK_TRANSACTION_ID_COL]} "
            f"matched invoice(s) {', '.join(invoice_numbers)}; "
            f"applied {cents_to_money(applied_cents):,.2f} {currency_key} "
            f"(cumulative {cents_to_money(cumulative_applied):,.2f}; "
            f"remaining {cents_to_money(remaining_balance):,.2f}). "
            f"{result.note}"
        )
        # The source columns are deliberately NOT touched:
        # "Open Amount", "AR Status", "Reconciliation Status".

        # Update the internal working column so subsequent installment
        # payments see the reduced balance, without touching the original
        # Open Amount column.  The balance is reduced for every applied
        # invoice, including partial ones, so later instalments can be
        # matched to the remainder.
        new_working_balance = max(
            int(ar_df.at[idx, "__ar_open_amount_cents"]) - applied_cents, 0
        )
        ar_df.at[idx, "__ar_open_amount_cents"] = new_working_balance

    # --- Write bank receipt output columns ---
    suggested_bank_status = "Reconciled" if is_fully_reconciled else "Pending Review"
    bank_df.at[bank_index, BANK_RECON_BATCH_ID_COL] = batch_id
    bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL] = (
        f"{result.method}; matched invoices: {', '.join(invoice_numbers)}. {result.note}"
    )
    bank_df.at[bank_index, OUT_MATCH_METHOD_BANK_COL] = result.method
    bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = suggested_bank_status
    bank_df.at[bank_index, BANK_APPLIED_AMOUNT_TOTAL_COL] = cents_to_money(
        result.applied_cents
    )
    # The source column is deliberately NOT touched: "Status".

    return 1, len(result.ar_indexes)


def build_payment_signature(
    customer_key: str,
    currency_key: str,
    gross_amount_cents: int,
    invoice_numbers: list[str],
) -> tuple[str, str, int, tuple[str, ...]] | None:
    """
    Build a deterministic signature for duplicate-payment detection.

    Only receipts with explicit invoice references are included. This avoids
    falsely treating two unrelated on-account receipts with the same amount
    as duplicates.
    """
    normalized_invoices = tuple(
        sorted({normalize_invoice(v) for v in invoice_numbers if normalize_invoice(v)})
    )
    if not normalized_invoices:
        return None
    return (customer_key, currency_key, gross_amount_cents, normalized_invoices)
