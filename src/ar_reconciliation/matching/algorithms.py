"""
The matching algorithms.

Three strategies, tried in order:

  1. :func:`find_invoice_reference_match` - the memo names the invoice.
     This carries the strongest evidence of intent, so it is tried first.
  2. :func:`find_amount_combination_match` - no usable memo reference, so
     the invoices that add up to the receipt are found by subset sum.
  3. :func:`find_partial_combination_match` - no exact combination, but the
     scenario legitimately leaves a residual and exactly one invoice is
     plausible.

Each function returns a :class:`~ar_reconciliation.matching.models.MatchResult`
or None.  None means "this strategy cannot explain the receipt", not "the
receipt is unmatched".
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.core.primitives import cents_to_money, normalize_text
from ar_reconciliation.core.schema import (
    AR_DUE_DATE_COL,
    AR_INVOICE_COL,
    AR_INVOICE_DATE_COL,
    OUT_MATCHED_BANK_TXN_COL,
)
from ar_reconciliation.matching.candidates import is_open_ar_row
from ar_reconciliation.matching.models import AmbiguousMatch, CandidateInvoice, MatchResult
from ar_reconciliation.matching.subset_sum import find_subset_sums
from ar_reconciliation.rules.policies import (
    AMOUNT_TOLERANCE_CENTS,
    ENGINE_REVIEW_ONLY_SCENARIOS,
    MAX_CANDIDATES_PER_BANK_RECEIPT,
    MAX_PARTIAL_RESIDUAL_RATIO,
    MIN_PARTIAL_RESIDUAL_CENTS,
    NAME_FALLBACK_SCENARIOS,
    PARTIAL_ALLOWANCE_SCENARIOS,
    ROUNDING_TOLERANCE_CENTS,
    SMALL_RESIDUAL_WRITE_OFF_CENTS,
    UNRESTRICTED_PARTIAL_SCENARIOS,
)

__all__ = [
    "find_invoice_reference_match",
    "find_amount_combination_match",
    "find_partial_combination_match",
]


def find_invoice_reference_match(
    ar_df: pd.DataFrame,
    invoice_numbers: list[str],
    scenario: str,
    target_gross_cents: int,
    target_net_cents: int,
    bank_fee_cents: int,
    customer_key: str,
    currency_key: str,
    alt_customer_keys: tuple[str, ...] = (),
) -> MatchResult | None:
    """
    Match a receipt against the invoices its memo names.

    An invoice number on its own is never enough.  The invoice must also
    belong to the same customer and be in the same currency, so a receipt
    cannot be posted to another customer's AR merely because the memo
    contains a duplicated, mistyped, or cross-customer invoice number.
    """
    if not invoice_numbers:
        return None
    if (
        scenario in ENGINE_REVIEW_ONLY_SCENARIOS
        and scenario not in NAME_FALLBACK_SCENARIOS
    ):
        return None

    order_map = {inv: i for i, inv in enumerate(invoice_numbers)}

    # Critical control: an invoice number alone is not sufficient.
    # Restrict memo-based matching to the same customer and currency so that
    # a receipt cannot be posted to another customer's AR merely because the
    # memo contains a duplicated, mistyped, or cross-customer invoice number.
    #
    # alt_customer_keys allows a controlled fallback (missing ERP Customer ID
    # resolved by customer name, or a parent paying a child invoice).  The
    # memo must still name a specific open invoice in the same currency.
    allowed_keys = {customer_key, *[k for k in alt_customer_keys if k]}
    inv_df = ar_df.loc[
        ar_df["__invoice_key"].isin(set(invoice_numbers))
        & (ar_df["__customer_key"].isin(allowed_keys))
        & (ar_df["__currency_key"] == currency_key)
    ].copy()
    if inv_df.empty:
        return None

    # An invoice that was already partially applied in this run stays
    # matchable, so that later instalments against the same invoice can be
    # applied to its remaining balance.  A fully cleared invoice is excluded.
    working_balance = inv_df["__ar_open_amount_cents"]
    fully_cleared = (
        inv_df[OUT_MATCHED_BANK_TXN_COL].fillna("").astype(str).str.strip() != ""
    ) & (working_balance <= 0)
    inv_df = inv_df[~fully_cleared]
    if inv_df.empty:
        return None

    inv_df = inv_df[inv_df.apply(is_open_ar_row, axis=1)]
    if inv_df.empty:
        return None

    inv_df["__memo_order"] = inv_df["__invoice_key"].map(order_map)
    inv_df = inv_df.sort_values(
        ["__memo_order", AR_DUE_DATE_COL, AR_INVOICE_DATE_COL], kind="stable"
    )
    referenced_total_cents = int(inv_df["__ar_open_amount_cents"].sum())

    # --- Bank Fee (S05) ---------------------------------------------------
    # The bank deducts a fee, so the customer remitted gross but the bank
    # received net.  AR must be cleared at GROSS while bank cash reconciles
    # at NET.  The referenced invoice therefore normally equals the NET
    # amount, not the gross amount.
    if scenario == "Bank Fee" or bank_fee_cents > 0:
        if referenced_total_cents == target_net_cents:
            return MatchResult(
                ar_indexes=[int(i) for i in inv_df.index],
                applied_cents=referenced_total_cents,
                residual_cents=0,
                method="Memo Invoice Match - Bank Fee",
                # The fee was deducted before the AR was cleared at net, so it
                # is part of the gross amount that is not applied to AR.
                adjustment_cents=target_gross_cents - referenced_total_cents,
                note=(
                    f"Memo invoice total equals net cash {cents_to_money(target_net_cents):,.2f}. "
                    f"Clear AR at net {cents_to_money(target_net_cents):,.2f}; "
                    f"gross received {cents_to_money(target_gross_cents):,.2f} includes a bank fee of "
                    f"{cents_to_money(bank_fee_cents):,.2f} to be booked separately."
                ),
            )

    if referenced_total_cents == target_gross_cents:
        if bank_fee_cents > 0 or scenario == "Bank Fee":
            return MatchResult(
                ar_indexes=[int(i) for i in inv_df.index],
                applied_cents=referenced_total_cents,
                residual_cents=0,
                method="Memo Invoice Match - Bank Fee",
                note=(
                    f"Memo invoice total equals gross receipt. "
                    f"Clear AR at gross {cents_to_money(target_gross_cents):,.2f}; "
                    f"bank cash is net {cents_to_money(target_net_cents):,.2f}; "
                    f"book bank fee {cents_to_money(bank_fee_cents):,.2f} separately."
                ),
            )
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=referenced_total_cents,
            residual_cents=0,
            method="Memo Invoice Match",
            note="Memo invoice reference and gross receipt amount match open AR.",
        )

    diff_to_gross = target_gross_cents - referenced_total_cents

    if scenario == "Rounding Variance" and abs(diff_to_gross) <= ROUNDING_TOLERANCE_CENTS:
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=referenced_total_cents,
            residual_cents=diff_to_gross,
            method="Memo Invoice Match - Rounding",
            adjustment_cents=diff_to_gross,
            note=f"Small penny rounding variance {cents_to_money(diff_to_gross):,.2f}.",
        )

    # --- Scenarios that legitimately leave a residual ---------------------
    # The memo names the invoice, so a residual is expected.  The residual is
    # still bounded: a gap larger than the allowed proportion means the memo
    # probably points at the wrong invoice and must not be applied.
    if scenario in PARTIAL_ALLOWANCE_SCENARIOS:
        residual_cents = referenced_total_cents - target_gross_cents

        # An instalment may legitimately be much smaller than the invoice it
        # is paying, so no residual cap is applied for those scenarios.
        residual_allowed = scenario in UNRESTRICTED_PARTIAL_SCENARIOS or abs(
            residual_cents
        ) <= max(
            MIN_PARTIAL_RESIDUAL_CENTS,
            int(referenced_total_cents * MAX_PARTIAL_RESIDUAL_RATIO),
        )

        if 0 < target_gross_cents < referenced_total_cents and residual_allowed:
            method = f"Memo Invoice Match - {scenario}"
            if scenario == "Short Payment":
                note = (
                    f"Customer short-paid by {cents_to_money(residual_cents):,.2f}; "
                    f"do not fully close until discount/write-off is approved."
                )
            elif scenario in {"Partial Payment", "Installment"}:
                note = (
                    f"Partial/installment payment. Apply {cents_to_money(target_gross_cents):,.2f}; "
                    f"remaining AR balance is {cents_to_money(residual_cents):,.2f}."
                )
            else:
                note = (
                    f"Applied {cents_to_money(target_gross_cents):,.2f} against referenced AR of "
                    f"{cents_to_money(referenced_total_cents):,.2f}; "
                    f"residual {cents_to_money(residual_cents):,.2f} requires review."
                )
            return MatchResult(
                ar_indexes=[int(i) for i in inv_df.index],
                applied_cents=target_gross_cents,
                residual_cents=residual_cents,
                method=method,
                note=note,
            )

        if target_gross_cents > referenced_total_cents and residual_allowed:
            return MatchResult(
                ar_indexes=[int(i) for i in inv_df.index],
                applied_cents=referenced_total_cents,
                residual_cents=diff_to_gross,
                unapplied_cents=diff_to_gross,
                method=f"Memo Invoice Match - {scenario}",
                note=(
                    f"Payment exceeds referenced open AR by "
                    f"{cents_to_money(diff_to_gross):,.2f}; "
                    f"apply the AR balance and route the excess to unapplied cash review."
                ),
            )

    if (
        scenario == "Small Residual Write-Off"
        and abs(diff_to_gross) <= SMALL_RESIDUAL_WRITE_OFF_CENTS
    ):
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=min(target_gross_cents, referenced_total_cents),
            residual_cents=abs(diff_to_gross),
            method="Memo Invoice Match - Small Residual",
            unapplied_cents=max(diff_to_gross, 0),
            note=(
                f"Small residual {cents_to_money(abs(diff_to_gross)):,.2f} "
                f"requires policy-based write-off review."
            ),
        )

    return None


def find_amount_combination_match(
    candidates_df: pd.DataFrame, target_gross_cents: int
) -> MatchResult | AmbiguousMatch | None:
    """
    Match a receipt against a combination of open invoices.

    Used when the memo carries no usable invoice reference.  The match is
    only made when exactly one set of invoices adds up to the receipt.  When
    two or more sets fit equally well - two invoices of the same amount, or
    one invoice and a pair that totals the same - there is no evidence for
    choosing between them, so an :class:`AmbiguousMatch` is returned and the
    receipt goes to review.
    """
    if candidates_df.empty:
        return None

    candidates_df = candidates_df.sort_values(
        [AR_DUE_DATE_COL, AR_INVOICE_DATE_COL, AR_INVOICE_COL], kind="stable"
    )
    if len(candidates_df) > MAX_CANDIDATES_PER_BANK_RECEIPT:
        candidates_df = candidates_df.head(MAX_CANDIDATES_PER_BANK_RECEIPT)

    candidates = [
        CandidateInvoice(
            ar_index=int(idx),
            amount_cents=int(row["__ar_open_amount_cents"]),
            invoice_number=normalize_text(row[AR_INVOICE_COL]),
        )
        for idx, row in candidates_df.iterrows()
    ]

    solutions = find_subset_sums(
        candidates, target_gross_cents, AMOUNT_TOLERANCE_CENTS, limit=2
    )
    if not solutions:
        return None
    if len(solutions) > 1:
        invoice_by_index = {c.ar_index: c.invoice_number for c in candidates}
        return AmbiguousMatch(
            alternatives=[
                [invoice_by_index[i] for i in solution] for solution in solutions
            ]
        )
    matched_indexes = solutions[0]

    matched_total = sum(
        int(v) for v in candidates_df.loc[matched_indexes, "__ar_open_amount_cents"]
    )
    return MatchResult(
        ar_indexes=[int(i) for i in matched_indexes],
        applied_cents=matched_total,
        residual_cents=0,
        adjustment_cents=target_gross_cents - matched_total,
        method="Customer Currency Amount Combination",
        note=(
            "No usable memo invoice reference; matched by customer, currency, "
            "and exact gross amount combination."
        ),
    )


def find_partial_combination_match(
    candidates_df: pd.DataFrame,
    target_gross_cents: int,
    scenario: str,
) -> MatchResult | None:
    """
    Fallback used when no invoice combination matches the receipt exactly but
    the scenario legitimately leaves a residual (short pay, overpayment,
    partial payment, instalment, small residual write-off, sales tax).

    SAFETY RULE: the fallback only fires when exactly ONE candidate invoice is
    a plausible match, i.e. it is the only invoice whose residual is within
    the allowed proportion of its open amount.  When several invoices look
    equally plausible the receipt is left for manual review rather than
    consuming an invoice that may belong to another receipt.
    """
    if candidates_df.empty:
        return None
    if scenario not in PARTIAL_ALLOWANCE_SCENARIOS:
        return None

    plausible: list[tuple[int, int]] = []  # (index, residual_cents)
    for idx, row in candidates_df.iterrows():
        invoice_cents = int(row["__ar_open_amount_cents"])
        if invoice_cents <= 0:
            continue
        residual_cents = abs(invoice_cents - target_gross_cents)
        # The residual cap always applies here.  An unrestricted residual is
        # only acceptable when the memo names the invoice explicitly, because
        # without a memo reference an invoice must not be consumed on a guess.
        if residual_cents <= max(
            MIN_PARTIAL_RESIDUAL_CENTS,
            int(invoice_cents * MAX_PARTIAL_RESIDUAL_RATIO),
        ):
            plausible.append((int(idx), residual_cents))

    if len(plausible) != 1:
        return None

    idx, residual_cents = plausible[0]
    invoice_cents = int(candidates_df.at[idx, "__ar_open_amount_cents"])

    unapplied_cents = 0
    if target_gross_cents < invoice_cents:
        applied_cents = target_gross_cents
        residual_cents = invoice_cents - target_gross_cents
    else:
        applied_cents = invoice_cents
        residual_cents = target_gross_cents - invoice_cents
        unapplied_cents = residual_cents

    return MatchResult(
        ar_indexes=[idx],
        applied_cents=applied_cents,
        residual_cents=residual_cents,
        unapplied_cents=unapplied_cents,
        method=f"Amount Combination - {scenario}",
        note=(
            f"No exact amount combination for this receipt; applied "
            f"{cents_to_money(applied_cents):,.2f} to the only plausible open invoice "
            f"with a residual of {cents_to_money(residual_cents):,.2f} requiring review."
        ),
    )
