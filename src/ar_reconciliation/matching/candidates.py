"""
Deciding which invoices a receipt is allowed to be matched against.

This is where the customer, currency and AR-status controls live.  Keeping
it apart from the matching algorithms makes the controls easy to audit:
every reason an invoice can be excluded is in this one file.
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.core.primitives import normalize_key, normalize_text
from ar_reconciliation.core.schema import (
    AR_CUSTOMER_ID_COL,
    AR_CUSTOMER_NAME_COL,
    AR_PARENT_CUSTOMER_ID_COL,
    AR_RECON_STATUS_COL,
    AR_STATUS_COL,
    BANK_CUSTOMER_ID_COL,
    BANK_CUSTOMER_NAME_COL,
    BANK_REMITTER_NAME_COL,
    OUT_MATCHED_BANK_TXN_COL,
)

__all__ = [
    "is_open_ar_row",
    "build_candidate_mask",
    "build_name_fallback_keys",
    "CLOSED_AR_STATUSES",
    "ON_HOLD_RECON_STATUSES",
]

#: AR statuses that mean the invoice is finished and must not be matched.
CLOSED_AR_STATUSES = {"closed", "paid", "cancelled", "void", "written off", "disputed"}

#: Reconciliation statuses that mean the invoice is finished.
FINAL_RECON_STATUSES = {"reconciled", "matched", "closed"}

#: Reconciliation statuses that hold the invoice back from matching.
ON_HOLD_RECON_STATUSES = {"on hold"}


def is_open_ar_row(ar_row: pd.Series) -> bool:
    """
    Determine if an AR row is eligible for matching.

    Reads only ORIGINAL source columns - never looks at output columns.
    Allow any AR Status that is not explicitly closed/paid/cancelled.
    """
    ar_status = normalize_text(ar_row.get(AR_STATUS_COL, "")).lower()
    recon_status = normalize_text(ar_row.get(AR_RECON_STATUS_COL, "")).lower()
    amount_cents = int(ar_row.get("__ar_open_amount_cents", 0))
    if amount_cents <= 0:
        return False
    # Exclude only rows that are explicitly finalized.
    if recon_status in FINAL_RECON_STATUSES:
        return False
    if ar_status in CLOSED_AR_STATUSES:
        return False
    if recon_status in ON_HOLD_RECON_STATUSES:
        return False
    return True


def build_candidate_mask(
    ar_df: pd.DataFrame,
    customer_key: str,
    currency_key: str,
    alt_customer_keys: tuple[str, ...] = (),
) -> pd.Series:
    """
    Build the pool of open AR rows that may be used for amount-combination
    matching.

    An invoice that was already partially applied in this run stays in the
    pool so that later instalments can be matched against its remaining
    balance.  A fully cleared invoice is excluded.
    """
    allowed_keys = {customer_key, *[k for k in alt_customer_keys if k]}
    already_matched = (
        ar_df[OUT_MATCHED_BANK_TXN_COL].fillna("").astype(str).str.strip() != ""
    )
    fully_cleared = already_matched & (ar_df["__ar_open_amount_cents"] <= 0)

    recon = ar_df[AR_RECON_STATUS_COL].fillna("").str.strip().str.lower()
    ar_status = ar_df[AR_STATUS_COL].fillna("").str.strip().str.lower()

    return (
        (ar_df["__customer_key"].isin(allowed_keys))
        & (ar_df["__currency_key"] == currency_key)
        & (ar_df["__ar_open_amount_cents"] > 0)
        & (~fully_cleared)
        & (~recon.isin(FINAL_RECON_STATUSES))
        & (~ar_status.isin(CLOSED_AR_STATUSES))
        & (~recon.isin(ON_HOLD_RECON_STATUSES))
    )


def build_name_fallback_keys(
    ar_df: pd.DataFrame,
    bank_row: pd.Series,
) -> tuple[str, ...]:
    """
    Build controlled fallback customer keys for receipts that cannot be
    matched on ERP Customer ID alone.

    Two cases are supported:

    1. Missing ERP Customer ID (S15)
       The bank feed has no ERP Customer ID.  The receipt may still be
       matched when the customer name resolves to exactly one ERP customer
       that has open AR in the same currency.  Ambiguous names return no key
       so the receipt is routed to review rather than guessed.

    2. Parent pays child (S22)
       The receipt carries the parent ERP Customer ID but the memo names a
       child invoice.  The child customer key is returned as an alternative
       so the memo invoice can be resolved, but the invoice reference must
       still match exactly.
    """
    keys: list[str] = []

    bank_customer_id = normalize_text(bank_row.get(BANK_CUSTOMER_ID_COL, ""))

    # Case 1: resolve customer name -> ERP customer id.
    # Only applied when the bank feed has no ERP Customer ID at all.
    if not bank_customer_id:
        names = [
            normalize_key(bank_row.get(BANK_CUSTOMER_NAME_COL, "")),
            normalize_key(bank_row.get(BANK_REMITTER_NAME_COL, "")),
        ]
        names = [n for n in names if n]

        ar_names = ar_df[AR_CUSTOMER_NAME_COL].apply(normalize_key)
        for name in names:
            matched_names = ar_df.loc[ar_names == name, AR_CUSTOMER_ID_COL]
            unique_ids = sorted(
                {normalize_key(v) for v in matched_names if normalize_key(v)}
            )
            if len(unique_ids) == 1:
                keys.append(unique_ids[0])
                break

    # Case 2: parent paying a child invoice.  The receipt carries the
    # parent ERP Customer ID, but the memo names a child invoice.  The child
    # customer key is added as an alternative so the memo invoice can be
    # resolved.
    parent_key = normalize_key(bank_customer_id)
    if parent_key:
        child_mask = ar_df[AR_PARENT_CUSTOMER_ID_COL].apply(normalize_key) == parent_key
        for child_id in ar_df.loc[child_mask, AR_CUSTOMER_ID_COL]:
            child_key = normalize_key(child_id)
            if child_key and child_key != parent_key and child_key not in keys:
                keys.append(child_key)

    return tuple(keys)
