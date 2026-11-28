"""
Tests for the AI exception resolution agent.

The agent's safety property is that it can never talk the system into
applying an unsupported amount.  Most of these tests therefore drive the
validator with deliberately wrong answers and check that each one is
repaired or downgraded.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ar_reconciliation.agent import exception_agent as agent
from ar_reconciliation.core.primitives import money_to_cents


def make_context(
    *,
    net_cents: int = 812_375,
    gross_cents: int = 812_375,
    fee_cents: int = 0,
    candidate_invoices: tuple[tuple[str, int], ...] = (("INV-900175", 837_500),),
    scenario: str = "Short Payment",
) -> agent.ExceptionContext:
    """Build an ExceptionContext for the validator tests."""
    candidates = [
        agent.Candidate(
            invoice_number=invoice,
            customer_id="CUST-1015",
            customer_name="BrightPath Education Inc.",
            currency="USD",
            open_amount_cents=open_cents,
            due_date="2026-07-15",
            invoice_date="2026-06-01",
            dispute_reason="",
            residual_cents=open_cents,
            score=1.0,
        )
        for invoice, open_cents in candidate_invoices
    ]
    return agent.ExceptionContext(
        bank_transaction_id="BTRX-000158",
        value_date="2026-06-04",
        customer_name="BrightPath Education Inc.",
        remitter_name="BrightPath Education Inc.",
        customer_id="CUST-1015",
        currency="USD",
        gross_amount_cents=gross_cents,
        bank_fee_cents=fee_cents,
        net_amount_cents=net_cents,
        memo="INV-900175; tax withheld 251.25",
        memo_scenario=scenario,
        extracted_invoices="INV-900175",
        engine_reason="Short Payment: not eligible for full auto-reconciliation.",
        candidates=candidates,
    )


def valid_answer() -> dict:
    return {
        "exception_type": "Short Payment",
        "root_cause": "The payer withheld a freight claim deduction.",
        "recommended_action": "Recommend Match",
        "confidence": "High",
        "proposed_invoices": ["INV-900175"],
        "proposed_amount": 8123.75,
        "proposed_residual": 251.25,
        "accounting_treatment": "Apply the net cash and raise a deduction case.",
        "gl_posting": "Dr Bank / Cr AR",
        "explanation": "Test answer.",
    }


class TestValidatorRejections:
    """Each of these is a way a model could try to overstate its certainty."""

    def test_invoice_outside_the_candidate_pool_is_discarded(self):
        raw = valid_answer()
        raw["proposed_invoices"] = ["INV-999999", "INV-900175"]

        recommendation = agent.validate_recommendation(raw, make_context())

        assert "INV-999999" not in recommendation.proposed_invoices
        assert "INV-900175" in recommendation.proposed_invoices
        assert any("not in the candidate pool" in note for note in recommendation.validation_notes)

    def test_amount_above_the_cash_received_is_capped(self):
        raw = valid_answer()
        raw["proposed_amount"] = 999_999.99

        recommendation = agent.validate_recommendation(raw, make_context())

        assert recommendation.proposed_amount_cents == 812_375
        assert any("exceeds the cash received" in note for note in recommendation.validation_notes)

    def test_residual_is_recomputed_from_the_ar_data(self):
        raw = valid_answer()
        raw["proposed_residual"] = 10.00

        recommendation = agent.validate_recommendation(raw, make_context())

        # 8375.00 open minus 8123.75 applied is 251.25.
        assert recommendation.proposed_residual_cents == 25_125
        assert any("does not agree with the AR data" in note for note in recommendation.validation_notes)

    def test_unknown_exception_type_is_mapped(self):
        raw = valid_answer()
        raw["exception_type"] = "Something The Model Invented"

        recommendation = agent.validate_recommendation(raw, make_context())

        assert recommendation.exception_type == "Unknown"
        assert any("outside the controlled taxonomy" in note for note in recommendation.validation_notes)

    def test_unknown_action_is_downgraded(self):
        raw = valid_answer()
        raw["recommended_action"] = "Post It Anyway"

        recommendation = agent.validate_recommendation(raw, make_context())

        assert recommendation.recommended_action == "Manual Review"

    def test_unknown_confidence_is_downgraded(self):
        raw = valid_answer()
        raw["confidence"] = "Very High"

        recommendation = agent.validate_recommendation(raw, make_context())

        assert recommendation.confidence == "Low"

    def test_auto_match_requires_high_confidence(self):
        raw = valid_answer()
        raw["recommended_action"] = "Auto Match"
        raw["confidence"] = "Medium"

        recommendation = agent.validate_recommendation(raw, make_context())

        assert recommendation.recommended_action != "Auto Match"

    def test_review_only_scenario_is_never_auto_matched(self):
        """S11/S14 and friends: the engine's veto survives the model."""
        raw = valid_answer()
        raw["recommended_action"] = "Auto Match"
        raw["confidence"] = "High"

        context = make_context(scenario="Duplicate Review")
        recommendation = agent.validate_recommendation(raw, context)

        assert recommendation.recommended_action != "Auto Match"

    def test_negative_receipt_can_never_be_applied(self):
        """S19/S20/S21: a refund has no cash to apply."""
        raw = valid_answer()
        raw["proposed_amount"] = 5000.00

        context = make_context(net_cents=-50_000, gross_cents=-50_000)
        recommendation = agent.validate_recommendation(raw, context)

        assert recommendation.proposed_amount_cents == 0

    def test_negative_proposed_amount_is_reset(self):
        raw = valid_answer()
        raw["proposed_amount"] = -100.00

        recommendation = agent.validate_recommendation(raw, make_context())

        assert recommendation.proposed_amount_cents == 0

    def test_amount_above_the_combined_open_amount_is_capped(self):
        """The proposed amount cannot exceed what the invoices are worth."""
        raw = valid_answer()
        raw["proposed_invoices"] = ["INV-900175", "INV-900176"]
        raw["proposed_amount"] = 8123.75

        context = make_context(
            candidate_invoices=(("INV-900175", 837_500), ("INV-900176", 100_000)),
        )
        recommendation = agent.validate_recommendation(raw, context)

        assert recommendation.proposed_amount_cents <= 937_500

    def test_every_repair_is_logged(self):
        """A repair that is not logged cannot be audited."""
        raw = valid_answer()
        raw["proposed_invoices"] = ["INV-999999"]
        raw["proposed_amount"] = 999_999.99
        raw["confidence"] = "Very High"

        recommendation = agent.validate_recommendation(raw, make_context())

        assert len(recommendation.validation_notes) >= 3


class TestValidatorAcceptsGoodAnswers:
    """A well-formed answer must survive validation unchanged."""

    def test_valid_answer_is_preserved(self):
        recommendation = agent.validate_recommendation(valid_answer(), make_context())

        assert recommendation.exception_type == "Short Payment"
        assert recommendation.recommended_action == "Recommend Match"
        assert recommendation.confidence == "High"
        assert recommendation.proposed_invoices == ["INV-900175"]
        assert recommendation.proposed_amount_cents == 812_375
        assert recommendation.proposed_residual_cents == 25_125
        assert recommendation.validation_notes == []

    def test_missing_fields_fall_back_safely(self):
        recommendation = agent.validate_recommendation({}, make_context())

        assert recommendation.exception_type == "Unknown"
        assert recommendation.recommended_action == "Manual Review"
        assert recommendation.confidence == "Low"
        assert recommendation.proposed_amount_cents == 0


class TestRulesBackend:
    """The offline backend has to behave like the controlled one."""

    def test_every_recommendation_is_valid(self):
        context = make_context()

        raw = agent.rules_backend_analyse(context)
        recommendation = agent.validate_recommendation(raw, context)

        assert recommendation.recommended_action in agent.RECOMMENDED_ACTIONS
        assert recommendation.confidence in agent.CONFIDENCE_LEVELS
        assert recommendation.exception_type in agent.EXCEPTION_TYPES

    def test_rules_backend_never_auto_matches_a_review_only_scenario(self):
        context = make_context(scenario="Duplicate Review")

        raw = agent.rules_backend_analyse(context)
        recommendation = agent.validate_recommendation(raw, context)

        assert recommendation.recommended_action != "Auto Match"


class TestEndToEndRun:
    """The agent runs over the real unmatched receipts."""

    def test_every_unmatched_receipt_is_analysed(self, analysis, result_unmatched):
        review_df, _ = analysis

        assert len(review_df) == len(result_unmatched)
        assert set(review_df["Bank Transaction ID"]) == set(
            result_unmatched["Bank Transaction ID"]
        )

    def test_recommendations_use_the_controlled_vocabulary(self, analysis):
        review_df, _ = analysis

        assert set(review_df["Recommended Action"]).issubset(
            set(agent.RECOMMENDED_ACTIONS)
        )
        assert set(review_df["AI Confidence"]).issubset(set(agent.CONFIDENCE_LEVELS))
        assert set(review_df["AI Exception Type"]).issubset(set(agent.EXCEPTION_TYPES))

    def test_no_auto_match_without_high_confidence(self, analysis):
        """The approval gate depends on this rule holding."""
        review_df, _ = analysis

        auto = review_df[review_df["Recommended Action"] == "Auto Match"]
        assert (auto["AI Confidence"] == "High").all()

    def test_proposed_amounts_never_exceed_the_receipt(self, analysis, result_unmatched):
        """
        A proposed amount can never be more than the cash received.

        Negative receipts (refunds, chargebacks) are the interesting case:
        they carry no cash to apply, so the proposed amount must be zero.
        """
        review_df, _ = analysis

        gross_by_txn = dict(
            zip(
                result_unmatched["Bank Transaction ID"],
                result_unmatched["Gross Receipt Amount"],
            )
        )
        for _, row in review_df.iterrows():
            proposed = money_to_cents(row["Proposed Applied Amount"])
            gross = money_to_cents(gross_by_txn.get(row["Bank Transaction ID"], 0))

            if gross <= 0:
                # No cash to apply.
                assert proposed == 0
            else:
                assert proposed <= gross

    def test_review_sheet_has_the_approval_column(self, analysis):
        review_df, _ = analysis

        assert agent.REVIEW_APPROVAL_COL in review_df.columns
        assert agent.REVIEW_REVIEWER_NOTES_COL in review_df.columns

    def test_nothing_is_applied_without_an_approval(self, analysis):
        """A recommendation is not an application."""
        review_df, _ = analysis

        # The approval column is blank: no human has decided anything yet.
        approvals = review_df[agent.REVIEW_APPROVAL_COL].astype(object).fillna("").astype(str).str.strip()
        assert (approvals == "").all()

        # Every row is marked as not applied to AR.
        applied = review_df[agent.REVIEW_APPLIED_COL].astype(object).fillna("").astype(str).str.strip()
        assert set(applied.unique()) <= {"", "No"}
