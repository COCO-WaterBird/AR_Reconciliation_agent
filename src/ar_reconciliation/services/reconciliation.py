"""
The reconciliation service.

This is the workflow that ties the layers together: read the source
workbooks, prepare working columns, match every receipt, write the result
workbook, and verify the control totals.

The matching rules themselves live in :mod:`ar_reconciliation.matching`.  This
module owns the order of operations and the bookkeeping around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ar_reconciliation.config import (
    AR_FILE,
    AR_SHEET,
    BANK_FILE,
    BANK_SHEET,
    RESULT_FILE,
)
from ar_reconciliation.core.primitives import (
    cents_to_money,
    money_to_cents,
    normalize_invoice,
    normalize_key,
    normalize_text,
)
from ar_reconciliation.core.schema import (
    AR_CUSTOMER_ID_COL,
    AR_CUSTOMER_NAME_COL,
    AR_CURRENCY_COL,
    AR_DUE_DATE_COL,
    AR_INVOICE_COL,
    AR_INVOICE_DATE_COL,
    AR_OPEN_AMOUNT_COL,
    AR_RECON_STATUS_COL,
    AR_PARENT_CUSTOMER_ID_COL,
    AR_STATUS_COL,
    BANK_ADJUSTMENT_AMOUNT_COL,
    BANK_APPLIED_AMOUNT_TOTAL_COL,
    BANK_CUSTOMER_ID_COL,
    BANK_CUSTOMER_NAME_COL,
    BANK_CURRENCY_COL,
    BANK_FEES_COL,
    BANK_GROSS_AMOUNT_COL,
    BANK_HELD_AMOUNT_COL,
    BANK_MEMO_COL,
    BANK_NET_AMOUNT_COL,
    BANK_POSTING_DATE_COL,
    BANK_PROCESSING_NOTES_COL,
    BANK_RECON_BATCH_ID_COL,
    BANK_REMITTER_NAME_COL,
    BANK_STATUS_COL,
    BANK_TRANSACTION_ID_COL,
    BANK_UNAPPLIED_AMOUNT_COL,
    BANK_VALUE_DATE_COL,
    OUT_APPLIED_AMOUNT_COL,
    OUT_EXTRACTED_INVOICES_COL,
    OUT_MATCHED_AMOUNT_COL,
    OUT_MATCHED_BANK_TXN_COL,
    OUT_MATCHED_PAYMENT_DATE_COL,
    OUT_MATCH_METHOD_BANK_COL,
    OUT_MATCH_METHOD_COL,
    OUT_MEMO_SCENARIO_COL,
    OUT_RECON_NOTES_COL,
    OUT_RESIDUAL_AMOUNT_COL,
    OUT_SUGGESTED_BANK_STATUS_COL,
    OUT_SUGGESTED_RECON_STATUS_COL,
)
from ar_reconciliation.io.integrity import (
    DQ_REASON_COL,
    flag_ar_integrity_issues,
    flag_bank_integrity_issues,
    parse_money_column,
)
from ar_reconciliation.io.workbook import (
    apply_workbook_formatting,
    ensure_output_columns,
    read_preserved_sheets,
    require_columns,
)
from ar_reconciliation.matching.algorithms import (
    find_amount_combination_match,
    find_invoice_reference_match,
    find_partial_combination_match,
)
from ar_reconciliation.matching.application import apply_match, build_payment_signature
from ar_reconciliation.matching.models import AmbiguousMatch
from ar_reconciliation.matching.candidates import (
    build_candidate_mask,
    build_name_fallback_keys,
)
from ar_reconciliation.matching.memo import classify_memo, extract_invoice_numbers
from ar_reconciliation.services.review_rows import (
    mark_duplicate_receipt,
    route_to_review,
    summarise_candidates,
)
from ar_reconciliation.rules.policies import (
    ENGINE_REVIEW_ONLY_SCENARIOS,
    ENGINE_REVIEW_ONLY_SCENARIOS as REVIEW_ONLY_SCENARIOS,
    NAME_FALLBACK_SCENARIOS,
    UNRESTRICTED_PARTIAL_SCENARIOS,
)
from ar_reconciliation.services.controls import (
    ControlResult,
    compute_controls,
    control_break_rows,
    is_source_reconciled,
    summary_rows,
)

#: The scenarios the engine refuses to auto-reconcile.  Re-exported under the
#: original name so existing callers keep working.
__all__ = [
    "ReconciliationService",
    "ReconciliationResult",
    "run_reconciliation",
    "REVIEW_ONLY_SCENARIOS",
]


@dataclass
class ReconciliationResult:
    """What a reconciliation run produced."""

    output_path: Path
    matched_receipt_count: int = 0
    matched_invoice_count: int = 0
    pending_review_count: int = 0
    unmatched_count: int = 0
    data_quality_count: int = 0
    reconciled_ar_total_cents: int = 0
    reconciled_bank_total_cents: int = 0
    control_difference_cents: int = 0
    controls: ControlResult | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def control_difference(self) -> float:
        return cents_to_money(self.control_difference_cents)


class ReconciliationService:
    """
    The deterministic reconciliation workflow.

    The service holds no matching rules of its own; it sequences the
    matching layer and owns the bookkeeping between the steps.
    """

    def __init__(
        self,
        bank_path: Path | None = None,
        ar_path: Path | None = None,
        output_path: Path | None = None,
    ) -> None:
        self.bank_path = Path(bank_path) if bank_path is not None else BANK_FILE
        self.ar_path = Path(ar_path) if ar_path is not None else AR_FILE
        self.output_path = (
            Path(output_path) if output_path is not None else RESULT_FILE
        )

    # -- Source preparation ------------------------------------------------

    def load_sources(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Read and validate the two source workbooks."""
        bank_df = pd.read_excel(self.bank_path, sheet_name=BANK_SHEET)
        ar_df = pd.read_excel(self.ar_path, sheet_name=AR_SHEET)

        require_columns(
            bank_df,
            [
                BANK_TRANSACTION_ID_COL,
                BANK_VALUE_DATE_COL,
                BANK_CUSTOMER_NAME_COL,
                BANK_CUSTOMER_ID_COL,
                BANK_CURRENCY_COL,
                BANK_GROSS_AMOUNT_COL,
                BANK_FEES_COL,
                BANK_NET_AMOUNT_COL,
                BANK_STATUS_COL,
                BANK_MEMO_COL,
            ],
            self.bank_path.name,
        )
        require_columns(
            ar_df,
            [
                AR_CUSTOMER_ID_COL,
                AR_CUSTOMER_NAME_COL,
                AR_INVOICE_COL,
                AR_INVOICE_DATE_COL,
                AR_DUE_DATE_COL,
                AR_CURRENCY_COL,
                AR_OPEN_AMOUNT_COL,
                AR_STATUS_COL,
                AR_RECON_STATUS_COL,
            ],
            self.ar_path.name,
        )

        return bank_df, ar_df

    @staticmethod
    def prepare_frames(
        bank_df: pd.DataFrame, ar_df: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Add the output columns and the internal working columns.

        Working columns are prefixed with ``__`` and dropped again before
        the workbook is written.
        """
        ar_result_columns = [
            OUT_MATCHED_BANK_TXN_COL,
            OUT_MATCHED_PAYMENT_DATE_COL,
            OUT_MATCHED_AMOUNT_COL,
            OUT_APPLIED_AMOUNT_COL,
            OUT_RESIDUAL_AMOUNT_COL,
            OUT_MATCH_METHOD_COL,
            OUT_SUGGESTED_RECON_STATUS_COL,
            OUT_RECON_NOTES_COL,
        ]
        bank_result_columns = [
            BANK_RECON_BATCH_ID_COL,
            BANK_PROCESSING_NOTES_COL,
            OUT_EXTRACTED_INVOICES_COL,
            OUT_MEMO_SCENARIO_COL,
            OUT_MATCH_METHOD_BANK_COL,
            OUT_SUGGESTED_BANK_STATUS_COL,
            BANK_APPLIED_AMOUNT_TOTAL_COL,
            BANK_UNAPPLIED_AMOUNT_COL,
            BANK_ADJUSTMENT_AMOUNT_COL,
            BANK_HELD_AMOUNT_COL,
        ]

        # Columns the engine reads but does not require.  A source file
        # without them is still valid; they default to blank.
        for frame, column in (
            (bank_df, BANK_POSTING_DATE_COL),
            (bank_df, BANK_REMITTER_NAME_COL),
            (ar_df, AR_PARENT_CUSTOMER_ID_COL),
        ):
            if column not in frame.columns:
                frame[column] = ""

        ensure_output_columns(ar_df, ar_result_columns)
        ensure_output_columns(bank_df, bank_result_columns)

        # Clear prior-run output values.  The engine may be re-run against an
        # output workbook, so stale matches must not influence the current
        # reconciliation.
        ar_df.loc[:, ar_result_columns] = ""
        bank_df.loc[:, bank_result_columns] = ""

        # --- Working columns ---
        # An amount that cannot be read becomes 0 here and its row is held
        # out of matching by the integrity checks below - it is never
        # silently treated as a real zero.
        bank_df["__gross_amount_cents"], gross_errors = parse_money_column(
            bank_df, BANK_GROSS_AMOUNT_COL
        )
        bank_df["__bank_fee_cents"], fee_errors = parse_money_column(
            bank_df, BANK_FEES_COL
        )
        bank_df["__net_amount_cents"], net_errors = parse_money_column(
            bank_df, BANK_NET_AMOUNT_COL
        )
        ar_df["__ar_open_amount_cents"], open_errors = parse_money_column(
            ar_df, AR_OPEN_AMOUNT_COL
        )
        # Original open amount, kept so that cumulative applied amounts and
        # the remaining balance can be recalculated across multiple
        # instalments.
        ar_df["__ar_original_open_cents"] = ar_df["__ar_open_amount_cents"]

        bank_df["__customer_key"] = bank_df[BANK_CUSTOMER_ID_COL].apply(normalize_key)
        bank_df.loc[bank_df["__customer_key"] == "", "__customer_key"] = (
            bank_df[BANK_CUSTOMER_NAME_COL].apply(normalize_key)
        )
        ar_df["__customer_key"] = ar_df[AR_CUSTOMER_ID_COL].apply(normalize_key)
        ar_df.loc[ar_df["__customer_key"] == "", "__customer_key"] = (
            ar_df[AR_CUSTOMER_NAME_COL].apply(normalize_key)
        )

        bank_df["__currency_key"] = bank_df[BANK_CURRENCY_COL].apply(
            lambda x: normalize_text(x).upper()
        )
        ar_df["__currency_key"] = ar_df[AR_CURRENCY_COL].apply(
            lambda x: normalize_text(x).upper()
        )
        ar_df["__invoice_key"] = ar_df[AR_INVOICE_COL].apply(normalize_invoice)

        flag_bank_integrity_issues(
            bank_df, BANK_TRANSACTION_ID_COL, [gross_errors, fee_errors, net_errors]
        )
        flag_ar_integrity_issues(ar_df, AR_INVOICE_COL, [open_errors])

        bank_df[OUT_EXTRACTED_INVOICES_COL] = bank_df[BANK_MEMO_COL].apply(
            lambda x: ", ".join(extract_invoice_numbers(x))
        )
        bank_df[OUT_MEMO_SCENARIO_COL] = bank_df[BANK_MEMO_COL].apply(classify_memo)

        return bank_df, ar_df

    # -- Data quality ------------------------------------------------------

    @staticmethod
    def collect_data_quality_issues(
        ar_df: pd.DataFrame, bank_df: pd.DataFrame | None = None
    ) -> list[dict[str, object]]:
        """
        Find source rows that cannot be matched safely.

        Reported, never silently skipped:

          * AR rows that are closed or reconciled but still carry a non-zero
            open amount;
          * AR rows and bank receipts held out by the input integrity checks
            (unreadable amount, duplicated invoice number, blank or repeated
            Bank Transaction ID).
        """
        issues: list[dict[str, object]] = []

        if bank_df is not None and DQ_REASON_COL in bank_df.columns:
            flagged = bank_df.loc[bank_df[DQ_REASON_COL].astype(object).fillna("") != ""]
            for _, row in flagged.iterrows():
                issues.append(
                    {
                        "Issue": row[DQ_REASON_COL],
                        "Source": "Bank Receipts",
                        BANK_TRANSACTION_ID_COL: row.get(BANK_TRANSACTION_ID_COL, ""),
                        AR_CUSTOMER_ID_COL: row.get(BANK_CUSTOMER_ID_COL, ""),
                        "Customer Name": row.get(BANK_CUSTOMER_NAME_COL, ""),
                        "Recommendation": (
                            "Correct the bank record and re-run. The receipt was "
                            "not applied and is held for review."
                        ),
                    }
                )

        if DQ_REASON_COL in ar_df.columns:
            flagged = ar_df.loc[ar_df[DQ_REASON_COL].astype(object).fillna("") != ""]
            for idx, row in flagged.iterrows():
                issues.append(
                    {
                        "Issue": row[DQ_REASON_COL],
                        "Source": "Open AR",
                        "AR Row Index": idx,
                        AR_CUSTOMER_ID_COL: row.get(AR_CUSTOMER_ID_COL, ""),
                        "Customer Name": row.get(AR_CUSTOMER_NAME_COL, ""),
                        AR_INVOICE_COL: row.get(AR_INVOICE_COL, ""),
                        AR_OPEN_AMOUNT_COL: row.get(AR_OPEN_AMOUNT_COL, ""),
                        AR_STATUS_COL: row.get(AR_STATUS_COL, ""),
                        AR_RECON_STATUS_COL: row.get(AR_RECON_STATUS_COL, ""),
                        "Recommendation": (
                            "Correct the AR record and re-run. The invoice was "
                            "excluded from matching."
                        ),
                    }
                )

        bad_ar_mask = (ar_df["__ar_open_amount_cents"] != 0) & (
            ar_df[AR_STATUS_COL]
            .astype(object).fillna("")
            .str.strip()
            .str.lower()
            .isin({"closed"})
            | ar_df[AR_RECON_STATUS_COL]
            .astype(object).fillna("")
            .str.strip()
            .str.lower()
            .isin({"reconciled", "matched", "closed"})
        )

        for idx, row in ar_df.loc[bad_ar_mask].iterrows():
            issues.append(
                {
                    "Issue": "Closed/reconciled AR row has non-zero Open Amount",
                    "Source": "Open AR",
                    "AR Row Index": idx,
                    AR_CUSTOMER_ID_COL: row.get(AR_CUSTOMER_ID_COL, ""),
                    "Customer Name": row.get(AR_CUSTOMER_NAME_COL, ""),
                    AR_INVOICE_COL: row.get(AR_INVOICE_COL, ""),
                    AR_OPEN_AMOUNT_COL: row.get(AR_OPEN_AMOUNT_COL, ""),
                    AR_STATUS_COL: row.get(AR_STATUS_COL, ""),
                    AR_RECON_STATUS_COL: row.get(AR_RECON_STATUS_COL, ""),
                    "Recommendation": (
                        "Exclude from auto-match and review data sync/source mapping."
                    ),
                }
            )

        return issues

    # -- The matching loop -------------------------------------------------

    @staticmethod
    def order_receipts(bank_df: pd.DataFrame) -> pd.DataFrame:
        """
        Put the receipts in the order they should be processed.

        Chronological first, so the earliest valid receipt claims an invoice
        and later identical receipts are routed to duplicate review.

        Within that, two passes: receipts whose memo names an invoice are
        processed first, because they carry the strongest evidence of intent.
        The remaining receipts must not steal an invoice that a
        memo-referenced receipt already named.
        """
        bank_df = bank_df.sort_values(
            [BANK_VALUE_DATE_COL, BANK_POSTING_DATE_COL, BANK_TRANSACTION_ID_COL],
            kind="stable",
        )

        has_memo_invoice = bank_df[BANK_MEMO_COL].apply(
            lambda v: bool(extract_invoice_numbers(v))
        )
        bank_df["__has_memo_invoice"] = has_memo_invoice
        return pd.concat(
            [bank_df.loc[has_memo_invoice], bank_df.loc[~has_memo_invoice]],
            axis=0,
        )

    def reconcile(
        self,
        ar_df: pd.DataFrame,
        bank_df: pd.DataFrame,
    ) -> dict[str, Any]:
        """
        Match every receipt against the open AR.

        Returns the frames plus the counters the summary sheet reports.
        """
        unmatched_bank_rows: list[dict[str, object]] = []
        data_quality_rows = self.collect_data_quality_issues(ar_df, bank_df)

        matched_receipt_count = 0
        matched_invoice_count = 0
        pending_review_count = 0

        bank_df = self.order_receipts(bank_df)

        # Signature -> first successfully applied bank transaction ID.
        # Because the bank data is sorted chronologically, the earliest valid
        # receipt is applied and later identical receipts are routed to
        # duplicate review.
        processed_payment_signatures: dict[tuple[str, str, int, tuple[str, ...]], str] = {}

        for bank_index, bank_row in bank_df.iterrows():
            # Skip rows already reconciled in source data (read-only check).
            if is_source_reconciled(bank_row[BANK_STATUS_COL]):
                continue

            target_gross_cents = int(bank_row["__gross_amount_cents"])
            bank_fee_cents = int(bank_row["__bank_fee_cents"])
            target_net_cents = int(bank_row["__net_amount_cents"])
            scenario = normalize_text(bank_row[OUT_MEMO_SCENARIO_COL])
            invoice_numbers = extract_invoice_numbers(bank_row[BANK_MEMO_COL])
            extracted_invoices = ", ".join(invoice_numbers)

            payment_signature = build_payment_signature(
                customer_key=bank_row["__customer_key"],
                currency_key=bank_row["__currency_key"],
                gross_amount_cents=target_gross_cents,
                invoice_numbers=invoice_numbers,
            )

            # The candidate pool is worked out before any of the early exits
            # below, so that every receipt that reaches the review sheet
            # carries the open AR it could plausibly belong to - including the
            # receipts that are rejected for a reason unrelated to matching (a
            # duplicate, a bad amount, a review-only scenario).
            alt_customer_keys = build_name_fallback_keys(ar_df, bank_row)
            customer_key = bank_row["__customer_key"]
            currency_key = bank_row["__currency_key"]
            ar_mask = build_candidate_mask(
                ar_df, customer_key, currency_key, alt_customer_keys
            )
            candidates_df = ar_df.loc[ar_mask].copy()
            candidate_info = summarise_candidates(
                ar_df=ar_df,
                candidates_df=candidates_df,
                invoice_numbers=invoice_numbers,
                target_gross_cents=target_gross_cents,
            )

            # Held out by the input integrity checks: an unreadable amount or
            # a blank / repeated transaction ID.  Never applied.
            integrity_reason = normalize_text(bank_row.get(DQ_REASON_COL, ""))
            if integrity_reason:
                route_to_review(
                    bank_df=bank_df,
                    bank_index=bank_index,
                    bank_row=bank_row,
                    reason=f"Held by input integrity checks: {integrity_reason}.",
                    unmatched_rows=unmatched_bank_rows,
                    scenario=scenario,
                    extracted_invoices=extracted_invoices,
                    status="Exception",
                    candidate_info=candidate_info,
                )
                continue

            # Equal instalments against the same invoice are a legitimate
            # pattern (D3), so instalment memos are exempt from the signature
            # check.  They stay protected against over-application by the
            # invoice's remaining balance.
            if (
                payment_signature is not None
                and scenario not in UNRESTRICTED_PARTIAL_SCENARIOS
                and payment_signature in processed_payment_signatures
            ):
                mark_duplicate_receipt(
                    bank_df=bank_df,
                    bank_index=bank_index,
                    bank_row=bank_row,
                    original_transaction_id=processed_payment_signatures[
                        payment_signature
                    ],
                    invoice_numbers=invoice_numbers,
                    unmatched_bank_rows=unmatched_bank_rows,
                    candidate_info=candidate_info,
                )
                pending_review_count += 1
                continue

            if target_gross_cents <= 0 or target_net_cents < 0:
                reason = (
                    "Negative or zero receipt amount; route to "
                    "reversal/refund/chargeback review."
                )
                route_to_review(
                    bank_df=bank_df,
                    bank_index=bank_index,
                    bank_row=bank_row,
                    reason=reason,
                    unmatched_rows=unmatched_bank_rows,
                    scenario=scenario,
                    extracted_invoices=extracted_invoices,
                    candidate_info=candidate_info,
                )
                pending_review_count += 1
                continue

            if target_gross_cents - bank_fee_cents != target_net_cents:
                reason = (
                    "Gross Receipt Amount minus Bank Fees does not equal Net Amount."
                )
                route_to_review(
                    bank_df=bank_df,
                    bank_index=bank_index,
                    bank_row=bank_row,
                    reason=reason,
                    unmatched_rows=unmatched_bank_rows,
                    scenario=scenario,
                    extracted_invoices=extracted_invoices,
                    candidate_info=candidate_info,
                )
                pending_review_count += 1
                continue

            if (
                scenario in ENGINE_REVIEW_ONLY_SCENARIOS
                and scenario not in NAME_FALLBACK_SCENARIOS
            ):
                reason = f"{scenario}: not eligible for full auto-reconciliation."
                route_to_review(
                    bank_df=bank_df,
                    bank_index=bank_index,
                    bank_row=bank_row,
                    reason=reason,
                    unmatched_rows=unmatched_bank_rows,
                    scenario=scenario,
                    extracted_invoices=extracted_invoices,
                    candidate_info=candidate_info,
                )
                pending_review_count += 1
                continue

            match_result = find_invoice_reference_match(
                ar_df=ar_df,
                invoice_numbers=invoice_numbers,
                scenario=scenario,
                target_gross_cents=target_gross_cents,
                target_net_cents=target_net_cents,
                bank_fee_cents=bank_fee_cents,
                customer_key=customer_key,
                currency_key=currency_key,
                alt_customer_keys=alt_customer_keys,
            )

            if match_result is None:
                if candidates_df.empty:
                    reason = (
                        "No open AR candidates found for the same ERP Customer ID/"
                        "customer name and currency."
                    )
                    route_to_review(
                        bank_df=bank_df,
                        bank_index=bank_index,
                        bank_row=bank_row,
                        reason=reason,
                        unmatched_rows=unmatched_bank_rows,
                        scenario=scenario,
                        extracted_invoices=extracted_invoices,
                        status="Exception",
                        candidate_info=candidate_info,
                    )
                    continue

                match_result = find_amount_combination_match(
                    candidates_df, target_gross_cents
                )
                if isinstance(match_result, AmbiguousMatch):
                    # Several invoice sets explain the receipt equally (D1).
                    # Choosing one would be a guess, so a human decides.
                    options = " | ".join(
                        " + ".join(alt) for alt in match_result.alternatives
                    )
                    route_to_review(
                        bank_df=bank_df,
                        bank_index=bank_index,
                        bank_row=bank_row,
                        reason=(
                            "Ambiguous amount match: more than one set of open "
                            "invoices equals the gross receipt amount (for "
                            f"example {options}). Confirm the intended invoices "
                            "with the customer."
                        ),
                        unmatched_rows=unmatched_bank_rows,
                        scenario=scenario,
                        extracted_invoices=extracted_invoices,
                        candidate_info=candidate_info,
                    )
                    pending_review_count += 1
                    continue
                if match_result is None:
                    # Second chance: the scenario may legitimately leave a
                    # residual.
                    match_result = find_partial_combination_match(
                        candidates_df, target_gross_cents, scenario
                    )
                if match_result is None:
                    reason = (
                        "No exact invoice reference or customer/currency amount "
                        "combination equals the gross receipt amount."
                    )
                    route_to_review(
                        bank_df=bank_df,
                        bank_index=bank_index,
                        bank_row=bank_row,
                        reason=reason,
                        unmatched_rows=unmatched_bank_rows,
                        scenario=scenario,
                        extracted_invoices=extracted_invoices,
                        status="Exception",
                        candidate_info=candidate_info,
                    )
                    continue

            close_invoice = (
                match_result.residual_cents == 0
                and "Partial" not in match_result.method
                and "Short Payment" not in match_result.method
            )

            # A residual-bearing match across several invoices (D2): the
            # receipt does not say how the shortfall or excess is split
            # between them, so the allocation is left to a human.
            if not close_invoice and len(match_result.ar_indexes) > 1:
                named = ", ".join(
                    ar_df.loc[match_result.ar_indexes, AR_INVOICE_COL].astype(str)
                )
                route_to_review(
                    bank_df=bank_df,
                    bank_index=bank_index,
                    bank_row=bank_row,
                    reason=(
                        f"{match_result.method}: the receipt leaves a residual "
                        f"across several invoices ({named}); the allocation "
                        "between them is not stated, so it was not applied."
                    ),
                    unmatched_rows=unmatched_bank_rows,
                    scenario=scenario,
                    extracted_invoices=extracted_invoices,
                    candidate_info=candidate_info,
                )
                pending_review_count += 1
                continue
            receipt_added, invoice_added = apply_match(
                ar_df=ar_df,
                bank_df=bank_df,
                bank_index=bank_index,
                bank_row=bank_row,
                result=match_result,
                currency_key=bank_row["__currency_key"],
                close_invoice=close_invoice,
            )
            matched_receipt_count += receipt_added
            matched_invoice_count += invoice_added

            if payment_signature is not None:
                processed_payment_signatures[payment_signature] = normalize_text(
                    bank_row[BANK_TRANSACTION_ID_COL]
                )

            if (
                "Fee" in match_result.method
                or "Rounding" in match_result.method
                or "Overpayment" in match_result.method
                or "Small Residual" in match_result.method
            ):
                pending_review_count += 1

        return {
            "bank_df": bank_df,
            "ar_df": ar_df,
            "unmatched_bank_rows": unmatched_bank_rows,
            "data_quality_rows": data_quality_rows,
            "matched_receipt_count": matched_receipt_count,
            "matched_invoice_count": matched_invoice_count,
            "pending_review_count": pending_review_count,
        }

    # -- Control totals ----------------------------------------------------

    @staticmethod
    def compute_control_totals(
        ar_df: pd.DataFrame, bank_df: pd.DataFrame
    ) -> tuple[int, int, int]:
        """
        Compare the AR and bank sides of the reconciliation.

        Compare only the amounts generated by this program and use the same
        Suggested status definition on both sides.  Open Amount is source data
        and must not be used as the primary applied-cash control total.

        NOTE: an invoice may be settled by several receipts (instalments), so
        the AR side holds the cumulative applied amount for that invoice while
        the bank side holds one amount per receipt.  Both sides are compared
        over ALL applied rows (not only "Reconciled" ones) so that a
        multi-instalment invoice is counted once on the AR side and once per
        receipt on the bank side.
        """
        ar_total = int(ar_df[OUT_APPLIED_AMOUNT_COL].apply(money_to_cents).sum())
        bank_total = int(
            bank_df[BANK_APPLIED_AMOUNT_TOTAL_COL].apply(money_to_cents).sum()
        )
        return ar_total, bank_total, bank_total - ar_total

    # -- Output ------------------------------------------------------------

    @staticmethod
    def build_output_frames(
        ar_df: pd.DataFrame,
        bank_df: pd.DataFrame,
        unmatched_bank_rows: list[dict[str, object]],
        data_quality_rows: list[dict[str, object]],
        summary_df: pd.DataFrame,
    ) -> dict[str, pd.DataFrame]:
        """Assemble the frames that go into the result workbook."""
        # Fill AR reconciliation status for invoices that were not matched.
        # Blank means the AR row never received a reconciliation result in
        # this run.  Use an explicit status for reporting clarity.
        ar_df[OUT_SUGGESTED_RECON_STATUS_COL] = (
            ar_df[OUT_SUGGESTED_RECON_STATUS_COL].astype(object).fillna("").astype(str).str.strip()
        )
        ar_df.loc[
            ar_df[OUT_SUGGESTED_RECON_STATUS_COL] == "",
            OUT_SUGGESTED_RECON_STATUS_COL,
        ] = "Unmatched"

        # --- Drop internal working columns before output ---
        helper_cols = [
            "__gross_amount_cents",
            "__bank_fee_cents",
            "__net_amount_cents",
            "__ar_open_amount_cents",
            "__ar_original_open_cents",
            "__customer_key",
            "__currency_key",
            "__invoice_key",
            "__memo_order",
            "__has_memo_invoice",
            DQ_REASON_COL,
        ]
        ar_output = ar_df.drop(
            columns=[c for c in helper_cols if c in ar_df.columns], errors="ignore"
        )
        bank_output = bank_df.drop(
            columns=[c for c in helper_cols if c in bank_df.columns], errors="ignore"
        )

        unmatched_df = pd.DataFrame(unmatched_bank_rows)

        # Give the data quality sheet its headers even when nothing was
        # flagged.  Without this the sheet is written completely empty, so a
        # reviewer cannot tell "no issues found" apart from "the check never
        # ran".
        data_quality_df = pd.DataFrame(
            data_quality_rows,
            columns=[
                "Issue",
                "Source",
                BANK_TRANSACTION_ID_COL,
                "AR Row Index",
                AR_CUSTOMER_ID_COL,
                "Customer Name",
                AR_INVOICE_COL,
                AR_OPEN_AMOUNT_COL,
                AR_STATUS_COL,
                AR_RECON_STATUS_COL,
                "Recommendation",
            ],
        )

        return {
            "Reconciled Open AR": ar_output,
            "Processed Bank Receipts": bank_output,
            "Unmatched Bank Receipts": unmatched_df,
            "Data Quality Checks": data_quality_df,
            "Summary": summary_df,
        }

    def write_workbook(
        self,
        frames: dict[str, pd.DataFrame],
    ) -> None:
        """
        Write the result workbook.

        The sheets the AI exception agent owns are carried over, so a re-run
        of the engine does not destroy every approval a reviewer entered.
        """
        preserved = read_preserved_sheets(self.output_path)

        with pd.ExcelWriter(self.output_path, engine="openpyxl") as writer:
            for sheet_name, frame in frames.items():
                frame.to_excel(writer, index=False, sheet_name=sheet_name)
            for sheet_name, frame in preserved.items():
                frame.to_excel(writer, index=False, sheet_name=sheet_name)

        from ar_reconciliation.io.report_views import refresh_report_views

        refresh_report_views(self.output_path)
        apply_workbook_formatting(self.output_path)

    def _write_failed_diagnostics(
        self,
        ar_df: pd.DataFrame,
        bank_df: pd.DataFrame,
        unmatched_bank_rows: list,
        data_quality_rows: list,
        controls,
    ) -> Path:
        """
        Keep the evidence of a failed run without finalising it.

        The result workbook is never written when a control breaks.  The
        diagnostics go to a separate ``..._FAILED.xlsx`` file, marked FAILED,
        with a Control Breaks sheet naming every receipt and invoice that
        does not tie.
        """
        failed_path = self.output_path.with_name(f"{self.output_path.stem}_FAILED.xlsx")
        summary_df = pd.DataFrame(
            [["Run Status", "FAILED"], *summary_rows(controls)[1:]],
            columns=["Metric", "Value"],
        )
        frames = self.build_output_frames(
            ar_df=ar_df,
            bank_df=bank_df,
            unmatched_bank_rows=unmatched_bank_rows,
            data_quality_rows=data_quality_rows,
            summary_df=summary_df,
        )
        frames["Control Breaks"] = control_break_rows(controls)
        try:
            with pd.ExcelWriter(failed_path, engine="openpyxl") as writer:
                for sheet_name, frame in frames.items():
                    frame.to_excel(writer, index=False, sheet_name=sheet_name)
            apply_workbook_formatting(failed_path)
        except Exception:  # diagnostics must never hide the original failure
            pass
        return failed_path

    # -- The whole run -----------------------------------------------------

    def run(self, verbose: bool = True) -> ReconciliationResult:
        """
        Run the reconciliation end to end.

        Raises:
            ValueError: if the control totals do not balance.  Nothing is
                written in that case.
        """
        if verbose:
            print("Reading input workbooks...")

        bank_df, ar_df = self.load_sources()
        bank_df, ar_df = self.prepare_frames(bank_df, ar_df)

        if verbose:
            print("Starting reconciliation...")

        outcome = self.reconcile(ar_df=ar_df, bank_df=bank_df)

        ar_df = outcome["ar_df"]
        bank_df = outcome["bank_df"]
        unmatched_bank_rows = outcome["unmatched_bank_rows"]
        data_quality_rows = outcome["data_quality_rows"]

        ar_total, bank_total, difference = self.compute_control_totals(
            ar_df, bank_df
        )
        controls = compute_controls(ar_df, bank_df, invoice_col=AR_INVOICE_COL)
        if not controls.balanced:
            failed_path = self._write_failed_diagnostics(
                ar_df, bank_df, unmatched_bank_rows, data_quality_rows, controls
            )
            raise ValueError(
                "Reconciliation controls do not balance; the result workbook was not "
                f"written. Run status FAILED; diagnostics in {failed_path.name}. "
                + controls.describe_breaks()
            )

        summary_df = pd.DataFrame(
            [
                ["Matched bank receipts", outcome["matched_receipt_count"]],
                ["Matched AR invoice rows", outcome["matched_invoice_count"]],
                ["Pending review receipts", outcome["pending_review_count"]],
                [
                    "Unmatched / exception bank receipts",
                    len(unmatched_bank_rows),
                ],
                ["Data quality issues", len(data_quality_rows)],
                ["Reconciled AR applied amount", cents_to_money(ar_total)],
                ["Reconciled bank applied amount", cents_to_money(bank_total)],
                ["Reconciled control difference", cents_to_money(difference)],
                *summary_rows(controls),
                ["Output file", self.output_path.name],
            ],
            columns=["Metric", "Value"],
        )

        if verbose:
            print(f"Matched bank receipts              : {outcome['matched_receipt_count']}")
            print(f"Matched AR invoice rows            : {outcome['matched_invoice_count']}")
            print(f"Pending review receipts            : {outcome['pending_review_count']}")
            print(
                f"Unmatched / exception bank receipts: {len(unmatched_bank_rows)}"
            )
            print(f"Data quality issues                : {len(data_quality_rows)}")
            print(f"Writing output workbook            : {self.output_path.name}")

        frames = self.build_output_frames(
            ar_df=ar_df,
            bank_df=bank_df,
            unmatched_bank_rows=unmatched_bank_rows,
            data_quality_rows=data_quality_rows,
            summary_df=summary_df,
        )
        self.write_workbook(frames)

        if verbose:
            print("Done.")

        return ReconciliationResult(
            output_path=self.output_path,
            matched_receipt_count=outcome["matched_receipt_count"],
            matched_invoice_count=outcome["matched_invoice_count"],
            pending_review_count=outcome["pending_review_count"],
            unmatched_count=len(unmatched_bank_rows),
            data_quality_count=len(data_quality_rows),
            reconciled_ar_total_cents=ar_total,
            reconciled_bank_total_cents=bank_total,
            control_difference_cents=difference,
            controls=controls,
        )


def run_reconciliation(
    bank_path: Path | None = None,
    ar_path: Path | None = None,
    output_path: Path | None = None,
    verbose: bool = True,
) -> ReconciliationResult:
    """Run the deterministic reconciliation with the default paths."""
    service = ReconciliationService(
        bank_path=bank_path, ar_path=ar_path, output_path=output_path
    )
    return service.run(verbose=verbose)
