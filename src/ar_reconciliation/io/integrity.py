"""
Input integrity checks run before any matching.

A reconciliation is only as good as the records it is given.  The checks in
this module find the records that cannot be matched safely and hold them out
of the matching loop, with a reason a reviewer can act on:

  * an amount cell that cannot be read as money (never a silent zero);
  * a bank transaction ID that is blank or appears more than once, because
    the same receipt imported twice would otherwise be applied twice;
  * an invoice number that appears more than once for the same customer,
    because a receipt naming it cannot tell the two rows apart.

The checks never modify a source column.  They write their findings to an
internal ``__dq_reason`` working column, which the engine reads to hold a
row out and to build the Data Quality Checks sheet.
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.core.primitives import (
    MoneyParseError,
    money_to_cents,
    normalize_invoice,
    normalize_key,
    normalize_text,
)

__all__ = [
    "DQ_REASON_COL",
    "parse_money_column",
    "flag_bank_integrity_issues",
    "flag_ar_integrity_issues",
]

#: Internal working column holding the reason a row is held out of matching.
#: Empty means the row passed every check.
DQ_REASON_COL = "__dq_reason"


def parse_money_column(df: pd.DataFrame, column: str) -> tuple[pd.Series, pd.Series]:
    """
    Convert a money column to integer cents without aborting the run.

    Returns the cents (0 where a cell could not be read) and, aligned to the
    same index, the error text for each unreadable cell ("" elsewhere).
    """
    cents: dict[object, int] = {}
    errors: dict[object, str] = {}
    for index, value in df[column].items():
        try:
            cents[index] = money_to_cents(value)
            errors[index] = ""
        except MoneyParseError:
            cents[index] = 0
            errors[index] = f"{column} {normalize_text(value)!r} is not a readable amount"
    return (
        pd.Series(cents, index=df.index, dtype="int64"),
        pd.Series(errors, index=df.index, dtype=object),
    )


def _append_reason(df: pd.DataFrame, mask: pd.Series, reason: pd.Series | str) -> None:
    """Add a reason to the rows in ``mask``, keeping any reason already there."""
    if DQ_REASON_COL not in df.columns:
        df[DQ_REASON_COL] = pd.Series([""] * len(df), index=df.index, dtype=object)
    if isinstance(reason, str):
        reason = pd.Series(reason, index=df.index, dtype=object)
    existing = df[DQ_REASON_COL]
    combined = existing.where(existing == "", existing + "; ") + reason
    df.loc[mask, DQ_REASON_COL] = combined[mask]


def flag_bank_integrity_issues(
    bank_df: pd.DataFrame,
    transaction_id_col: str,
    money_errors: list[pd.Series],
) -> None:
    """Flag bank receipts that must be held out of matching."""
    if DQ_REASON_COL not in bank_df.columns:
        bank_df[DQ_REASON_COL] = pd.Series([""] * len(bank_df), index=bank_df.index, dtype=object)

    for errors in money_errors:
        _append_reason(bank_df, errors != "", errors)

    id_key = bank_df[transaction_id_col].apply(normalize_key)
    _append_reason(
        bank_df,
        id_key == "",
        "Bank Transaction ID is blank, so the receipt cannot be traced or de-duplicated",
    )

    counts = id_key.map(id_key.value_counts())
    repeated = (id_key != "") & (counts > 1)
    if repeated.any():
        reason = bank_df[transaction_id_col].apply(normalize_text).radd(
            "Bank Transaction ID "
        ) + counts.astype(str).radd(" appears ").add(
            " times in the bank file; none of the copies is applied until the "
            "duplicate import is resolved"
        )
        _append_reason(bank_df, repeated, reason)


def flag_ar_integrity_issues(
    ar_df: pd.DataFrame,
    invoice_col: str,
    money_errors: list[pd.Series],
) -> None:
    """
    Flag AR rows that must be held out of matching.

    Expects the ``__customer_key`` working column to be present already.
    """
    if DQ_REASON_COL not in ar_df.columns:
        ar_df[DQ_REASON_COL] = pd.Series([""] * len(ar_df), index=ar_df.index, dtype=object)

    for errors in money_errors:
        _append_reason(ar_df, errors != "", errors)

    invoice_key = ar_df[invoice_col].apply(normalize_invoice)
    pair = ar_df["__customer_key"].astype(str) + "|" + invoice_key
    counts = pair.map(pair.value_counts())
    repeated = (invoice_key != "") & (counts > 1)
    if repeated.any():
        reason = invoice_key.radd("Invoice Number ") + counts.astype(str).radd(
            " appears "
        ).add(
            " times for the same customer; a receipt naming it cannot tell the "
            "rows apart"
        )
        _append_reason(ar_df, repeated, reason)
