"""
Reconciliation controls.

Three checks, each of which can actually fail:

1. **Cash control** (bank side).  Every receipt's gross amount must land in
   exactly one place::

       gross receipt == applied to AR
                      + unapplied cash (overpayment kept on account)
                      + fee / rounding adjustment
                      + held for review (not applied at all)
                      + already reconciled in the source (skipped)

   The applied, unapplied and adjustment amounts are stated by the matching
   branch that produced them, and the held amount by the routing step, so
   none of them is derived as "whatever is left".  The check is made per
   receipt, and a receipt that does not tie is named.

2. **Applied cash agreement** (bank vs AR).  The cash the bank side says it
   applied must equal what the AR side received.

3. **AR roll-forward**.  For every AR row::

       open amount before the run - applied in this run == open amount remaining

   The remaining balance is the engine's own working balance, which is
   reduced independently of the cumulative applied column, so an invoice
   that was applied beyond its open amount breaks this check.

Money is compared in integer cents throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ar_reconciliation.core.primitives import cents_to_money, money_to_cents, normalize_text
from ar_reconciliation.core.schema import (
    AR_INVOICE_COL,
    AR_OPEN_AMOUNT_COL,
    BANK_ADJUSTMENT_AMOUNT_COL,
    BANK_GROSS_AMOUNT_COL,
    BANK_APPLIED_AMOUNT_TOTAL_COL,
    BANK_HELD_AMOUNT_COL,
    BANK_STATUS_COL,
    BANK_TRANSACTION_ID_COL,
    BANK_UNAPPLIED_AMOUNT_COL,
    OUT_APPLIED_AMOUNT_COL,
)

__all__ = [
    "SOURCE_RECONCILED_BANK_STATUSES",
    "ControlResult",
    "is_source_reconciled",
    "compute_controls",
    "compute_output_controls",
    "summary_rows",
]

#: Bank statuses meaning the receipt was already reconciled before this run.
#: Such a receipt is skipped by the engine and accounted for as "skipped".
SOURCE_RECONCILED_BANK_STATUSES = frozenset({"reconciled", "matched", "closed"})


def is_source_reconciled(status: object) -> bool:
    return normalize_text(status).lower() in SOURCE_RECONCILED_BANK_STATUSES


def _cents(series: pd.Series) -> pd.Series:
    return series.apply(money_to_cents).astype("int64")


@dataclass
class ControlResult:
    """The outcome of the three controls, in integer cents."""

    # Cash control
    receipts_gross_cents: int = 0
    applied_cents: int = 0
    unapplied_cents: int = 0
    adjustment_cents: int = 0
    held_cents: int = 0
    skipped_cents: int = 0
    cash_difference_cents: int = 0
    cash_breaks: list[str] = field(default_factory=list)
    unreadable_receipts: int = 0

    # Bank vs AR applied cash
    ar_applied_cents: int = 0
    applied_difference_cents: int = 0

    # AR roll-forward
    ar_opening_cents: int = 0
    ar_remaining_cents: int = 0
    rollforward_difference_cents: int = 0
    rollforward_breaks: list[str] = field(default_factory=list)

    @property
    def balanced(self) -> bool:
        return (
            self.cash_difference_cents == 0
            and not self.cash_breaks
            and self.applied_difference_cents == 0
            and self.rollforward_difference_cents == 0
            and not self.rollforward_breaks
        )

    def describe_breaks(self) -> str:
        """A one-paragraph explanation of what does not tie."""
        parts: list[str] = []
        if self.cash_difference_cents or self.cash_breaks:
            parts.append(
                f"Cash control difference {cents_to_money(self.cash_difference_cents):,.2f}"
                + (
                    f" (receipts not tying: {', '.join(self.cash_breaks[:10])})"
                    if self.cash_breaks
                    else ""
                )
            )
        if self.applied_difference_cents:
            parts.append(
                f"Bank applied {cents_to_money(self.applied_cents):,.2f} vs AR applied "
                f"{cents_to_money(self.ar_applied_cents):,.2f}"
            )
        if self.rollforward_difference_cents or self.rollforward_breaks:
            parts.append(
                f"AR roll-forward difference "
                f"{cents_to_money(self.rollforward_difference_cents):,.2f}"
                + (
                    f" (AR rows not tying: {', '.join(self.rollforward_breaks[:10])})"
                    if self.rollforward_breaks
                    else ""
                )
            )
        return "; ".join(parts)


def compute_controls(
    ar_df: pd.DataFrame,
    bank_df: pd.DataFrame,
    invoice_col: str = "Invoice Number",
) -> ControlResult:
    """
    Run the three controls over the engine's working frames.

    Expects the working columns ``__gross_amount_cents`` on the bank frame and
    ``__ar_original_open_cents`` / ``__ar_open_amount_cents`` on the AR frame.
    """
    result = ControlResult()

    # --- 1. Cash control ------------------------------------------------
    gross = bank_df["__gross_amount_cents"].astype("int64")
    applied = _cents(bank_df[BANK_APPLIED_AMOUNT_TOTAL_COL])
    unapplied = _cents(bank_df[BANK_UNAPPLIED_AMOUNT_COL])
    adjustment = _cents(bank_df[BANK_ADJUSTMENT_AMOUNT_COL])
    held = _cents(bank_df[BANK_HELD_AMOUNT_COL])
    skipped_mask = bank_df[BANK_STATUS_COL].apply(is_source_reconciled)
    skipped = gross.where(skipped_mask, 0)

    accounted = applied + unapplied + adjustment + held + skipped
    row_difference = gross - accounted

    result.receipts_gross_cents = int(gross.sum())
    result.applied_cents = int(applied.sum())
    result.unapplied_cents = int(unapplied.sum())
    result.adjustment_cents = int(adjustment.sum())
    result.held_cents = int(held.sum())
    result.skipped_cents = int(skipped.sum())
    result.cash_difference_cents = int(row_difference.sum())
    result.cash_breaks = [
        normalize_text(txn)
        for txn in bank_df.loc[row_difference != 0, BANK_TRANSACTION_ID_COL]
    ]
    if "__dq_reason" in bank_df.columns:
        result.unreadable_receipts = int(
            bank_df["__dq_reason"].fillna("").astype(str).str.contains("not a readable amount").sum()
        )

    # --- 2. Bank vs AR applied cash --------------------------------------
    ar_applied = _cents(ar_df[OUT_APPLIED_AMOUNT_COL])
    result.ar_applied_cents = int(ar_applied.sum())
    result.applied_difference_cents = result.applied_cents - result.ar_applied_cents

    # --- 3. AR roll-forward ----------------------------------------------
    opening = ar_df["__ar_original_open_cents"].astype("int64")
    remaining = ar_df["__ar_open_amount_cents"].astype("int64")
    ar_row_difference = opening - ar_applied - remaining

    result.ar_opening_cents = int(opening.sum())
    result.ar_remaining_cents = int(remaining.sum())
    result.rollforward_difference_cents = int(ar_row_difference.sum())
    result.rollforward_breaks = [
        normalize_text(inv) for inv in ar_df.loc[ar_row_difference != 0, invoice_col]
    ]

    return result


def compute_output_controls(ar_df: pd.DataFrame, bank_df: pd.DataFrame) -> ControlResult:
    """
    Run the same controls on the frames as they appear in the result workbook.

    Used after ``--apply-approved``, when the engine's working columns are no
    longer available.  They are rebuilt from the output columns:

      * gross cents from ``Gross Receipt Amount`` (an unreadable cell counts
        as 0, as it did in the engine run);
      * opening AR from the untouched source ``Open Amount``;
      * remaining AR as opening minus applied, floored at zero for an invoice
        that received cash.  An invoice applied beyond its open amount
        therefore breaks the roll-forward.
    """
    from ar_reconciliation.io.integrity import parse_money_column

    bank = bank_df.copy()
    ar = ar_df.copy()
    for column in (
        BANK_UNAPPLIED_AMOUNT_COL,
        BANK_ADJUSTMENT_AMOUNT_COL,
        BANK_HELD_AMOUNT_COL,
    ):
        if column not in bank.columns:
            bank[column] = ""
        bank[column] = bank[column].fillna("")
    bank[BANK_APPLIED_AMOUNT_TOTAL_COL] = bank[BANK_APPLIED_AMOUNT_TOTAL_COL].fillna("")
    bank["__gross_amount_cents"], _ = parse_money_column(bank, BANK_GROSS_AMOUNT_COL)

    ar[OUT_APPLIED_AMOUNT_COL] = ar[OUT_APPLIED_AMOUNT_COL].fillna("")
    opening, _ = parse_money_column(ar, AR_OPEN_AMOUNT_COL)
    applied = _cents(ar[OUT_APPLIED_AMOUNT_COL])
    ar["__ar_original_open_cents"] = opening
    ar["__ar_open_amount_cents"] = (opening - applied).where(
        applied == 0, (opening - applied).clip(lower=0)
    )
    return compute_controls(ar, bank, invoice_col=AR_INVOICE_COL)


def summary_rows(controls: ControlResult) -> list[list[object]]:
    """The control lines shown on the Summary sheet, as [metric, value] pairs."""
    m = cents_to_money
    return [
        ["CASH CONTROL", ""],
        ["Total bank receipts (gross)", m(controls.receipts_gross_cents)],
        ["  Applied to AR", m(controls.applied_cents)],
        ["  Unapplied cash (overpayments on account)", m(controls.unapplied_cents)],
        ["  Fee / rounding adjustments", m(controls.adjustment_cents)],
        ["  Held for review (not applied)", m(controls.held_cents)],
        ["  Already reconciled in source (skipped)", m(controls.skipped_cents)],
        ["Cash control difference", m(controls.cash_difference_cents)],
        ["Receipts with unreadable amounts (excluded)", controls.unreadable_receipts],
        ["AR ROLL-FORWARD", ""],
        ["Open AR before this run", m(controls.ar_opening_cents)],
        ["  Less: applied in this run", m(controls.ar_applied_cents)],
        ["Open AR remaining", m(controls.ar_remaining_cents)],
        ["AR roll-forward difference", m(controls.rollforward_difference_cents)],
    ]
