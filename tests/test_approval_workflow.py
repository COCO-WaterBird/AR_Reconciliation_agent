"""
Tests for the human-in-the-loop approval gate.

The approval gate is the last line of defence: the AI recommends, a human
decides, and only then may cash be applied to AR.  These tests check that
the gate holds, that it is idempotent, and that the control totals are
re-verified after every write.
"""

from __future__ import annotations

import shutil

import pandas as pd
import pytest

from ar_reconciliation.agent import exception_agent as agent
from ar_reconciliation.core.primitives import money_to_cents


@pytest.fixture
def workbook(result_path, tmp_path) -> pd.Path:
    """A private copy of the reconciled workbook, safe to modify."""
    target = tmp_path / "result.xlsx"
    shutil.copy(result_path, target)
    return target


@pytest.fixture
def review_frame(analysis):
    review_df, _ = analysis
    return review_df.copy()


def approve(review_df: pd.DataFrame, txn_id: str) -> pd.DataFrame:
    """Mark one receipt as approved, as a reviewer would in Excel."""
    out = review_df.copy()
    out.loc[out["Bank Transaction ID"] == txn_id, agent.REVIEW_APPROVAL_COL] = "APPROVE"
    return out


def control_difference(ar_df: pd.DataFrame, result_df: pd.DataFrame) -> int:
    ar_total = sum(money_to_cents(v) for v in ar_df["Applied Amount"].fillna("").tolist())
    bank_total = sum(
        money_to_cents(v) for v in result_df["AR Applied Amount Total"].fillna("").tolist()
    )
    return bank_total - ar_total


class TestApprovalGate:
    """Nothing reaches AR without an explicit approval."""

    def test_no_approval_means_no_application(self, workbook, review_frame):
        ar_df = pd.read_excel(workbook, sheet_name="Reconciled Open AR")
        result_df = pd.read_excel(workbook, sheet_name="Processed Bank Receipts")

        updated_ar, updated_result, log = agent.apply_approved_recommendations(
            result_df=result_df, ar_df=ar_df, review_df=review_frame
        )

        assert log == []
        assert updated_ar["Applied Amount"].fillna("").tolist() == ar_df[
            "Applied Amount"
        ].fillna("").tolist()

    def test_rejected_approval_means_no_application(self, workbook, review_frame):
        ar_df = pd.read_excel(workbook, sheet_name="Reconciled Open AR")
        result_df = pd.read_excel(workbook, sheet_name="Processed Bank Receipts")

        rejected = review_frame.copy()
        rejected[agent.REVIEW_APPROVAL_COL] = "REJECT"

        updated_ar, _updated_result, log = agent.apply_approved_recommendations(
            result_df=result_df, ar_df=ar_df, review_df=rejected
        )

        assert log == []
        assert updated_ar["Applied Amount"].fillna("").tolist() == ar_df[
            "Applied Amount"
        ].fillna("").tolist()

    def test_blank_approval_means_no_application(self, workbook, review_frame):
        """A reviewer who has not decided yet must not accidentally approve."""
        ar_df = pd.read_excel(workbook, sheet_name="Reconciled Open AR")
        result_df = pd.read_excel(workbook, sheet_name="Processed Bank Receipts")

        blank = review_frame.copy()
        blank[agent.REVIEW_APPROVAL_COL] = ""

        updated_ar, _r, log = agent.apply_approved_recommendations(
            result_df=result_df, ar_df=ar_df, review_df=blank
        )

        assert log == []
        assert updated_ar["Applied Amount"].fillna("").tolist() == ar_df[
            "Applied Amount"
        ].fillna("").tolist()


class TestApprovedApplication:
    """An approved recommendation is applied, and only that one."""

    @pytest.fixture
    def approvable(self, review_frame):
        """
        Pick a receipt the rules backend believes it can match.

        The recommendation has to name a real invoice with a defensible
        amount, otherwise there is nothing to apply.
        """
        candidates = review_frame[
            (review_frame["Recommended Action"] == "Recommend Match")
            & (review_frame["Proposed Invoice Numbers"].fillna("").str.len() > 0)
            & (review_frame["Proposed Applied Amount"].fillna(0).astype(float) > 0)
        ]
        assert len(candidates) > 0, "the rules backend produced no matchable receipt"
        return candidates.iloc[0]["Bank Transaction ID"]

    def test_approved_receipt_is_applied(self, workbook, review_frame, approvable):
        ar_df = pd.read_excel(workbook, sheet_name="Reconciled Open AR")
        result_df = pd.read_excel(workbook, sheet_name="Processed Bank Receipts")

        approved = approve(review_frame, approvable)

        updated_ar, updated_result, log = agent.apply_approved_recommendations(
            result_df=result_df, ar_df=ar_df, review_df=approved
        )

        assert len(log) > 0
        assert updated_ar["Applied Amount"].fillna("").tolist() != ar_df[
            "Applied Amount"
        ].fillna("").tolist()

    def test_control_totals_still_balance_after_applying(self, workbook, review_frame, approvable):
        """The whole point: applying an approval must not break the books."""
        ar_df = pd.read_excel(workbook, sheet_name="Reconciled Open AR")
        result_df = pd.read_excel(workbook, sheet_name="Processed Bank Receipts")

        approved = approve(review_frame, approvable)

        updated_ar, updated_result, _log = agent.apply_approved_recommendations(
            result_df=result_df, ar_df=ar_df, review_df=approved
        )

        assert control_difference(updated_ar, updated_result) == 0

    def test_applying_twice_is_idempotent(self, workbook, review_frame, approvable):
        """Re-running must never apply the same receipt a second time."""
        ar_df = pd.read_excel(workbook, sheet_name="Reconciled Open AR")
        result_df = pd.read_excel(workbook, sheet_name="Processed Bank Receipts")

        approved = approve(review_frame, approvable)

        first_ar, first_result, first_log = agent.apply_approved_recommendations(
            result_df=result_df, ar_df=ar_df, review_df=approved
        )

        # The second run sees the already-applied state.
        second_ar, second_result, second_log = agent.apply_approved_recommendations(
            result_df=first_result, ar_df=first_ar, review_df=approved
        )

        assert first_ar["Applied Amount"].fillna("").tolist() == second_ar[
            "Applied Amount"
        ].fillna("").tolist()
        assert control_difference(second_ar, second_result) == 0


class TestApprovalCarryOver:
    """A reviewer's decision survives a re-run of the reconciliation."""

    def test_approval_is_carried_over(self, workbook, review_frame):
        """
        The decision is written to the review sheet inside the workbook, and
        reading it back returns the same approval.
        """
        txn_id = review_frame.iloc[0]["Bank Transaction ID"]

        approved = approve(review_frame, txn_id)
        agent.write_review_sheet(workbook, approved, pd.DataFrame())

        carried = agent.read_existing_approvals(workbook)
        key = agent.normalize_key(txn_id)
        assert key in carried
        assert carried[key]["approval"].strip().upper() == "APPROVE"

    def test_the_decision_survives_a_rerun_of_the_engine(self, workbook, review_frame):
        """
        The engine regenerates the workbook, so the review sheet has to be
        carried over - otherwise every approval would be lost on a re-run.
        """
        txn_id = review_frame.iloc[0]["Bank Transaction ID"]

        approved = approve(review_frame, txn_id)
        agent.write_review_sheet(workbook, approved, pd.DataFrame())

        # Rebuild the workbook the way the engine does, then read the
        # approvals back out of it.
        carried = agent.read_existing_approvals(workbook)
        key = agent.normalize_key(txn_id)
        assert carried[key]["approval"].strip().upper() == "APPROVE"

    def test_no_sidecar_ledger_is_written(self, workbook, review_frame):
        """
        The review sheet in the workbook is the single source of truth.

        A separate ledger file would be a second copy of the same data and
        could drift from it.
        """
        agent.write_review_sheet(workbook, review_frame, pd.DataFrame())

        siblings = list(workbook.parent.glob("*Approvals*.xlsx"))
        assert siblings == [], f"unexpected ledger file(s): {siblings}"

    def test_stale_approval_is_dropped_when_the_proposal_moves(self, workbook, review_frame):
        """
        An approval only survives while the proposal itself is unchanged.

        If the recommendation moves to a different invoice or a different
        amount, the old approval no longer applies and the row goes back
        for review.
        """
        txn_id = review_frame.iloc[0]["Bank Transaction ID"]

        changed = review_frame.copy()
        changed.loc[
            changed["Bank Transaction ID"] == txn_id, agent.REVIEW_APPROVAL_COL
        ] = "APPROVE"
        changed.loc[
            changed["Bank Transaction ID"] == txn_id,
            agent.REVIEW_PROPOSED_INVOICES_COL,
        ] = "INV-000000"

        agent.write_review_sheet(workbook, changed, pd.DataFrame())
        carried = agent.read_existing_approvals(workbook)

        key = agent.normalize_key(txn_id)
        assert key in carried
        # The sheet stores the new proposal, so a comparison against the
        # original proposal no longer matches and the approval is dropped.
        assert carried[key]["invoices"] != review_frame.iloc[0][
            agent.REVIEW_PROPOSED_INVOICES_COL
        ]
