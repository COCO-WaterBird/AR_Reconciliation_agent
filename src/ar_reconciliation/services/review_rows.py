"""
Building the rows that go to the review sheet.

The review sheet is the surface a human works on, so a row has to carry
everything needed to judge a receipt without opening the AR workbook: why
it was not reconciled, which invoices it could plausibly belong to, and how
far the receipt is from settling them.
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.core.primitives import (
    cents_to_money,
    normalize_invoice,
    normalize_text,
)
from ar_reconciliation.core.schema import (
    AR_INVOICE_COL,
    BANK_APPLIED_AMOUNT_TOTAL_COL,
    BANK_CURRENCY_COL,
    BANK_CUSTOMER_ID_COL,
    BANK_CUSTOMER_NAME_COL,
    BANK_FEES_COL,
    BANK_GROSS_AMOUNT_COL,
    BANK_HELD_AMOUNT_COL,
    BANK_MEMO_COL,
    BANK_NET_AMOUNT_COL,
    BANK_POSTING_DATE_COL,
    BANK_PROCESSING_NOTES_COL,
    BANK_RECON_BATCH_ID_COL,
    BANK_REMITTER_NAME_COL,
    BANK_TRANSACTION_ID_COL,
    BANK_VALUE_DATE_COL,
    OUT_EXTRACTED_INVOICES_COL,
    OUT_MATCH_METHOD_BANK_COL,
    OUT_MEMO_SCENARIO_COL,
    OUT_SUGGESTED_BANK_STATUS_COL,
    UNMATCHED_CANDIDATE_COUNT_COL,
    UNMATCHED_CANDIDATE_TOTAL_COL,
    UNMATCHED_DIFFERENCE_COL,
    UNMATCHED_REASON_COL,
    UNMATCHED_SUGGESTED_INVOICES_COL,
    UNMATCHED_SUGGESTED_OPEN_COL,
)

__all__ = [
    "append_unmatched",
    "route_to_review",
    "summarise_candidates",
    "mark_duplicate_receipt",
]


def append_unmatched(
    unmatched_rows: list[dict[str, object]],
    bank_row: pd.Series,
    reason: str,
    scenario: str = "",
    extracted_invoices: str = "",
    candidate_count: int | None = None,
    candidate_total: float | None = None,
    suggested_invoices: str = "",
    suggested_open_amount: float | None = None,
    difference: float | None = None,
) -> None:
    """
    Record one receipt that could not be auto-reconciled.

    The row carries everything a reviewer needs to judge the receipt without
    opening the AR workbook: which invoices are still open, how much they
    are worth, and how far the receipt is from settling them.
    """
    row: dict[str, object] = {
        BANK_TRANSACTION_ID_COL: bank_row.get(BANK_TRANSACTION_ID_COL, ""),
        BANK_VALUE_DATE_COL: bank_row.get(BANK_VALUE_DATE_COL, ""),
        BANK_POSTING_DATE_COL: bank_row.get(BANK_POSTING_DATE_COL, ""),
        BANK_CUSTOMER_NAME_COL: bank_row.get(BANK_CUSTOMER_NAME_COL, ""),
        BANK_REMITTER_NAME_COL: bank_row.get(BANK_REMITTER_NAME_COL, ""),
        BANK_CUSTOMER_ID_COL: bank_row.get(BANK_CUSTOMER_ID_COL, ""),
        BANK_CURRENCY_COL: bank_row.get(BANK_CURRENCY_COL, ""),
        BANK_GROSS_AMOUNT_COL: bank_row.get(BANK_GROSS_AMOUNT_COL, ""),
        BANK_FEES_COL: bank_row.get(BANK_FEES_COL, ""),
        BANK_NET_AMOUNT_COL: bank_row.get(BANK_NET_AMOUNT_COL, ""),
        BANK_MEMO_COL: bank_row.get(BANK_MEMO_COL, ""),
        OUT_MEMO_SCENARIO_COL: scenario,
        OUT_EXTRACTED_INVOICES_COL: extracted_invoices,
        UNMATCHED_REASON_COL: reason,
    }
    if candidate_count is not None:
        row[UNMATCHED_CANDIDATE_COUNT_COL] = candidate_count
    if candidate_total is not None:
        row[UNMATCHED_CANDIDATE_TOTAL_COL] = candidate_total
    if suggested_invoices:
        row[UNMATCHED_SUGGESTED_INVOICES_COL] = suggested_invoices
    if suggested_open_amount is not None:
        row[UNMATCHED_SUGGESTED_OPEN_COL] = suggested_open_amount
    if difference is not None:
        row[UNMATCHED_DIFFERENCE_COL] = difference
    unmatched_rows.append(row)


def route_to_review(
    bank_df: pd.DataFrame,
    bank_index: int,
    bank_row: pd.Series,
    reason: str,
    unmatched_rows: list[dict[str, object]],
    scenario: str = "",
    extracted_invoices: str = "",
    status: str = "Pending Review",
    candidate_info: dict[str, object] | None = None,
) -> None:
    """
    Send one receipt to the review queue and record why.

    Every receipt that cannot be auto-reconciled goes through this one
    function, so the notes, the suggested status and the review-sheet row
    always agree with each other.  Keeping this in a single place is what
    stops a new output column from being added to one branch and forgotten
    in the other four.
    """
    info = candidate_info or {}

    append_unmatched(
        unmatched_rows,
        bank_row,
        reason,
        scenario,
        extracted_invoices,
        candidate_count=info.get("candidate_count"),
        candidate_total=info.get("candidate_total"),
        suggested_invoices=info.get("suggested_invoices", ""),
        suggested_open_amount=info.get("suggested_open_amount"),
        difference=info.get("difference"),
    )
    bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL] = reason
    bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = status
    _record_held_amount(bank_df, bank_index, bank_row)


def _record_held_amount(
    bank_df: pd.DataFrame, bank_index: int, bank_row: pd.Series
) -> None:
    """
    Record the gross amount of a receipt that was not applied.

    The cash control needs every receipt's gross amount to land in exactly
    one bucket; for a receipt held in review, that bucket is this one.
    """
    gross_cents = int(bank_row.get("__gross_amount_cents", 0) or 0)
    bank_df.at[bank_index, BANK_HELD_AMOUNT_COL] = cents_to_money(gross_cents)


def summarise_candidates(
    ar_df: pd.DataFrame,
    candidates_df: pd.DataFrame,
    invoice_numbers: list[str],
    target_gross_cents: int,
) -> dict[str, object]:
    """
    Describe the open AR a receipt could plausibly belong to.

    Two groups are reported:

      * the invoices the memo names explicitly, and
      * the remaining open invoices for the same customer and currency.

    The reviewer can then see not only what is open, but how far the receipt
    is from settling it.
    """
    candidate_count = len(candidates_df)
    candidate_total_cents = int(candidates_df["__ar_open_amount_cents"].sum())

    suggested_invoices = ""
    suggested_open_cents: int | None = None
    difference: float | None = None

    # Prefer the invoices the memo names: they are the strongest evidence of
    # intent, so they are what a reviewer should look at first.
    referenced = candidates_df[
        candidates_df["__invoice_key"].isin(
            [normalize_invoice(v) for v in invoice_numbers]
        )
    ]

    if not referenced.empty:
        suggested = referenced
    else:
        suggested = candidates_df

    if not suggested.empty:
        suggested_invoices = ", ".join(
            suggested[AR_INVOICE_COL].astype(str).tolist()[:5]
        )
        suggested_open_cents = int(suggested["__ar_open_amount_cents"].sum())
        difference = cents_to_money(abs(target_gross_cents - suggested_open_cents))

    return {
        "candidate_count": candidate_count,
        "candidate_total": cents_to_money(candidate_total_cents),
        "suggested_invoices": suggested_invoices,
        "suggested_open_amount": (
            cents_to_money(suggested_open_cents)
            if suggested_open_cents is not None
            else None
        ),
        "difference": difference,
    }


def mark_duplicate_receipt(
    bank_df: pd.DataFrame,
    bank_index: int,
    bank_row: pd.Series,
    original_transaction_id: str,
    invoice_numbers: list[str],
    unmatched_bank_rows: list[dict[str, object]],
    candidate_info: dict[str, object] | None = None,
) -> None:
    """
    Route a suspected duplicate receipt to review without applying it.

    The earlier receipt is the one that was applied, because the bank data
    is processed chronologically.  The later, identical receipt is never
    applied automatically - a duplicate payment has to be confirmed by a
    human before any cash is posted twice.
    """
    current_transaction_id = normalize_text(bank_row.get(BANK_TRANSACTION_ID_COL, ""))
    extracted_invoices = ", ".join(invoice_numbers)
    reason = (
        f"Possible duplicate payment: {current_transaction_id} has the same customer, "
        f"currency, gross amount, and invoice reference as earlier receipt "
        f"{original_transaction_id}. The earlier receipt was applied; this receipt "
        f"was not applied to AR."
    )

    info = candidate_info or {}
    append_unmatched(
        unmatched_bank_rows,
        bank_row,
        reason,
        scenario="Duplicate Review",
        extracted_invoices=extracted_invoices,
        candidate_count=info.get("candidate_count"),
        candidate_total=info.get("candidate_total"),
        suggested_invoices=info.get("suggested_invoices", ""),
        suggested_open_amount=info.get("suggested_open_amount"),
        difference=info.get("difference"),
    )

    bank_df.at[bank_index, BANK_RECON_BATCH_ID_COL] = ""
    bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL] = reason
    bank_df.at[bank_index, OUT_EXTRACTED_INVOICES_COL] = extracted_invoices
    bank_df.at[bank_index, OUT_MEMO_SCENARIO_COL] = "Duplicate Review"
    bank_df.at[bank_index, OUT_MATCH_METHOD_BANK_COL] = "Duplicate Payment Check"
    bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = "Pending Review"
    bank_df.at[bank_index, BANK_APPLIED_AMOUNT_TOTAL_COL] = 0.0
    _record_held_amount(bank_df, bank_index, bank_row)
