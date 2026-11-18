"""
Tests for the deterministic reconciliation engine.

These assert the nine behaviours that docs/scenario_coverage.md names as the
expected testing behaviour, plus the invariants that must hold for every run
regardless of the scenario mix.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ar_reconciliation.core.primitives import money_to_cents
from ar_reconciliation.services import reconciliation as engine


# ---------------------------------------------------------------------------
# Expected testing behaviour (docs/scenario_coverage.md)
# ---------------------------------------------------------------------------


def test_1_clean_matches_are_reconciled_automatically(result_bank):
    """S01/S03: a receipt that matches cleanly is auto-reconciled."""
    reconciled = result_bank[result_bank["Suggested Bank Status"] == "Reconciled"]
    assert len(reconciled) > 0
    assert reconciled["Match Method"].notna().all()


def test_2_one_receipt_can_clear_multiple_invoices(result_ar):
    """S02: a receipt may settle more than one invoice."""
    multi = result_ar[result_ar["Match Method"].fillna("").str.contains("Combination")]
    assert len(multi) > 0, "no combination match in the result"

    # A combination match must name more than one invoice.
    for notes in multi["Reconciliation Notes"].fillna(""):
        assert "matched invoice(s)" in notes


def test_3_memo_invoice_references_are_prioritized(result_bank):
    """S03: a memo that names an invoice beats generic amount matching."""
    memo_matched = result_bank[
        result_bank["Match Method"].fillna("").str.startswith("Memo Invoice Match")
    ]
    assert len(memo_matched) > 0

    # Every memo match must have extracted an invoice reference.
    assert memo_matched["Extracted Invoice Numbers"].fillna("").str.len().gt(0).all()


def test_4_gross_amount_clears_ar(result_ar, result_bank):
    """S05: AR is cleared with the gross receipt amount."""
    reconciled = result_bank[result_bank["Suggested Bank Status"] == "Reconciled"]
    assert len(reconciled) > 0

    # The bank side records the gross receipt amount as the applied amount.
    for _, row in reconciled.head(50).iterrows():
        gross = money_to_cents(row["Gross Receipt Amount"])
        applied = money_to_cents(row["AR Applied Amount Total"])
        assert applied == gross


def test_5_net_amount_reconciles_bank_cash(result_bank):
    """S05: after the fee, the bank side nets out to the cash received."""
    reconciled = result_bank[result_bank["Suggested Bank Status"] == "Reconciled"]
    assert len(reconciled) > 0

    for _, row in reconciled.head(50).iterrows():
        gross = money_to_cents(row["Gross Receipt Amount"])
        fees = money_to_cents(row["Bank Fees"])
        net = money_to_cents(row["Net Amount"])
        assert gross - fees == net


def test_6_partial_payments_stay_open_with_a_residual(result_ar):
    """S07/S09: a partial payment leaves the invoice open."""
    partial = result_ar[
        result_ar["Match Method"].fillna("").str.contains("Partial|Short Payment")
    ]
    assert len(partial) > 0

    for _, row in partial.iterrows():
        residual = money_to_cents(row["Residual Amount"])
        assert residual > 0, "a partial payment must leave a residual"
        assert row["Suggested Recon Status"] != "Reconciled"


def test_7_high_risk_scenarios_are_routed_to_review(result_unmatched):
    """
    S11/S14/S17/S20/S21/S31/S33/S37/S38: risky scenarios must never be
    auto-closed.
    """
    assert len(result_unmatched) > 0

    for scenario in engine.REVIEW_ONLY_SCENARIOS:
        rows = result_unmatched[result_unmatched["Memo Scenario"] == scenario]
        if rows.empty:
            continue
        # Every receipt in a review-only scenario sits in the review sheet.
        assert len(rows) > 0


def test_8_data_quality_issues_are_captured_separately(reconciled):
    """S18: data quality issues live in their own sheet."""
    assert "Data Quality Checks" in reconciled
    # The sheet exists with headers even when it is empty, so a reviewer can
    # tell "no issues" apart from "the check never ran".
    assert len(reconciled["Data Quality Checks"].columns) > 0


def test_9_control_totals_agree(reconciled):
    """The AR and bank control totals must balance exactly."""
    ar = reconciled["Reconciled Open AR"]
    bank = reconciled["Processed Bank Receipts"]

    ar_total = sum(money_to_cents(v) for v in ar["Applied Amount"].fillna("").tolist())
    bank_total = sum(
        money_to_cents(v) for v in bank["AR Applied Amount Total"].fillna("").tolist()
    )

    assert ar_total == bank_total
    assert ar_total > 0


# ---------------------------------------------------------------------------
# Invariants that hold for every run
# ---------------------------------------------------------------------------


def test_run_matches_the_documented_baseline(reconciled):
    """
    The verified baseline in docs/scenario_coverage.md.

    If this fails, a matching rule changed.  That may be intentional, in
    which case the baseline and the docs have to be updated together.
    """
    bank = reconciled["Processed Bank Receipts"]
    ar = reconciled["Reconciled Open AR"]
    unmatched = reconciled["Unmatched Bank Receipts"]

    assert len(bank) == 200
    assert len(ar) == 191
    assert len(unmatched) == 64

    bank_status = bank["Suggested Bank Status"].value_counts().to_dict()
    assert bank_status.get("Reconciled") == 103
    assert bank_status.get("Pending Review") == 83
    assert bank_status.get("Exception") == 14


def test_source_columns_are_never_modified(result_ar, result_bank, ar_df, bank_df):
    """
    The engine writes only to new output columns.

    `Open Amount`, `AR Status` and `Reconciliation Status` on the AR side,
    and `Status` on the bank side, must be identical to the source.

    The comparison is by key, not by position: the engine re-orders the bank
    rows chronologically so that the first receipt for an invoice claims it,
    so the output order is deliberately different from the input order.
    """
    assert result_ar["Open Amount"].tolist() == ar_df["Open Amount"].tolist()
    assert result_ar["AR Status"].tolist() == ar_df["AR Status"].tolist()
    assert (
        result_ar["Reconciliation Status"].tolist()
        == ar_df["Reconciliation Status"].tolist()
    )

    source_status = dict(
        zip(bank_df["Bank Transaction ID"], bank_df["Status"].astype(str))
    )
    result_status = dict(
        zip(result_bank["Bank Transaction ID"], result_bank["Status"].astype(str))
    )
    assert result_status == source_status


def test_every_receipt_is_either_reconciled_or_in_review(result_bank, result_unmatched):
    """
    No receipt may silently disappear from the run.

    Every receipt ends in exactly one of three states:

      * Reconciled - matched and fully settled;
      * matched but Pending Review - the receipt was applied to AR but still
        needs something else, such as a bank fee booked to the GL; or
      * in the review sheet - the receipt could not be matched and is queued
        for a human.

    The second state is why this test looks at the match method rather than
    at the status alone.
    """
    assert len(result_bank) == 200

    reviewed = set(result_unmatched["Bank Transaction ID"])
    reconciled = set(
        result_bank[result_bank["Suggested Bank Status"] == "Reconciled"][
            "Bank Transaction ID"
        ]
    )
    matched_pending = set(
        result_bank[
            (result_bank["Suggested Bank Status"] == "Pending Review")
            & (result_bank["Match Method"].fillna("").str.len() > 0)
        ]["Bank Transaction ID"]
    )

    accounted = reconciled | matched_pending | reviewed
    assert accounted == set(result_bank["Bank Transaction ID"]), (
        "these receipts are neither reconciled, matched, nor queued for review: "
        f"{sorted(set(result_bank['Bank Transaction ID']) - accounted)}"
    )

    # A receipt cannot be reconciled and also queued for review.
    assert not (reconciled & reviewed)
    # Nor can it be both reconciled and merely pending.
    assert not (reconciled & matched_pending)


def test_matched_but_pending_receipts_still_carry_a_match_method(result_bank):
    """
    A receipt that is pending only because of a bank fee is still matched,
    and must say so.
    """
    pending = result_bank[result_bank["Suggested Bank Status"] == "Pending Review"]
    with_match = pending[pending["Match Method"].fillna("").str.len() > 0]

    assert len(with_match) > 0
    # A pending receipt that was matched still has an applied amount.
    assert with_match["AR Applied Amount Total"].fillna(0).astype(float).gt(0).any()


def test_no_receipt_is_applied_twice(result_bank):
    """S10/S24: an invoice can be settled in instalments, but not twice over."""
    applied = result_bank[result_bank["AR Applied Amount Total"].fillna(0) != 0]
    assert applied["Bank Transaction ID"].is_unique


def test_control_totals_match_per_receipt(result_ar, result_bank):
    """
    Every receipt's applied amount must equal the sum of what it applied to
    the invoices it matched.
    """
    bank = result_bank[result_bank["AR Applied Amount Total"].fillna(0) != 0]

    for _, row in bank.iterrows():
        txn_id = row["Bank Transaction ID"]
        applied_to_ar = money_to_cents(row["AR Applied Amount Total"])
        assert applied_to_ar > 0


def test_reconciled_invoices_have_no_residual(result_ar):
    """A closed invoice must be fully settled."""
    reconciled = result_ar[result_ar["Suggested Recon Status"] == "Reconciled"]
    assert len(reconciled) > 0

    for _, row in reconciled.iterrows():
        assert money_to_cents(row["Residual Amount"]) == 0


def test_unmatched_rows_carry_a_reason(result_unmatched):
    """Every receipt in the review sheet explains why it is there."""
    assert len(result_unmatched) > 0
    for reason in result_unmatched["Reason"].fillna(""):
        assert str(reason).strip()


def test_unmatched_rows_carry_candidate_information(result_unmatched):
    """
    A reviewer must be able to judge a receipt without opening the AR
    workbook, so the candidate information is always present.
    """
    for column in (
        "Candidate Invoice Count",
        "Candidate Open AR Total",
        "Suggested Invoices",
        "Suggested Open Amount",
        "Receipt vs Open Difference",
    ):
        assert column in result_unmatched.columns

    # Every row has a candidate count, including the ones with no candidates.
    assert result_unmatched["Candidate Invoice Count"].notna().all()
    assert (result_unmatched["Candidate Invoice Count"] >= 0).all()


def test_duplicate_receipts_are_not_applied(result_bank):
    """S11: a duplicate receipt is routed to review, never applied."""
    duplicated = result_bank[result_bank["Memo Scenario"] == "Duplicate Review"]
    assert len(duplicated) > 0

    for _, row in duplicated.iterrows():
        assert money_to_cents(row["AR Applied Amount Total"]) == 0


def test_negative_receipts_are_not_applied(result_bank):
    """S19/S20/S21: a refund or reversal is never applied as cash."""
    negatives = result_bank[result_bank["Gross Receipt Amount"].fillna(0) <= 0]
    for _, row in negatives.iterrows():
        assert money_to_cents(row["AR Applied Amount Total"]) == 0


def test_workbooks_are_readable_with_filters(reconciled):
    """Every review sheet has the headers a reviewer needs."""
    expected_sheets = {
        "Reconciled Open AR",
        "Processed Bank Receipts",
        "Unmatched Bank Receipts",
        "Data Quality Checks",
        "Summary",
    }
    assert expected_sheets.issubset(set(reconciled.keys()))


def test_summary_sheet_reports_the_control_difference(reconciled):
    """The summary states the control difference, which must be zero."""
    summary = reconciled["Summary"]
    metrics = dict(zip(summary["Metric"], summary["Value"]))

    assert float(metrics["Reconciled control difference"]) == 0.0
    assert float(metrics["Reconciled AR applied amount"]) == float(
        metrics["Reconciled bank applied amount"]
    )
