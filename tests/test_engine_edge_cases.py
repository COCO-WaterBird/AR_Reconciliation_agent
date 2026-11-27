"""
Edge-case tests for the deterministic engine, built on small synthetic frames.

The rest of the engine suite runs against the one sample dataset, which
never exercises these paths.  Each case here builds a handful of bank and AR
rows in memory, runs ``prepare_frames`` + ``reconcile`` and asserts on the
result.  Nothing is read from or written to disk.

The second half pins the fixes for the defects found in the codebase review
(P0-1 to P0-4, P1-1, P1-3, P1-4) and the product-owner decisions D1-D4.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ar_reconciliation.services.controls import compute_controls
from ar_reconciliation.core.primitives import money_to_cents
from ar_reconciliation.services.reconciliation import ReconciliationService

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

_BANK_DEFAULTS = {
    "Bank Transaction ID": "",
    "Value Date": "2026-06-01",
    "Posting Date": "2026-06-01",
    "Customer Name": "Acme Corp",
    "Remitter Name": "Acme Corp",
    "ERP Customer ID": "C1",
    "Currency": "USD",
    "Gross Receipt Amount": 0,
    "Bank Fees": 0,
    "Net Amount": 0,
    "Status": "Unreconciled",
    "Memo / Remittance Detail": "",
}

_AR_DEFAULTS = {
    "ERP Customer ID": "C1",
    "Parent Customer ID": "",
    "Customer Name": "Acme Corp",
    "Invoice Number": "",
    "Invoice Date": "2026-05-01",
    "Due Date": "2026-05-31",
    "Currency": "USD",
    "Open Amount": 0,
    "AR Status": "Open",
    "Reconciliation Status": "Open",
    "Dispute Reason": "",
}


def receipt(txn_id: str, amount, memo: str = "", **overrides) -> dict:
    """One bank receipt with no fee: gross == net == amount."""
    row = {
        "Bank Transaction ID": txn_id,
        "Gross Receipt Amount": amount,
        "Net Amount": amount,
        "Memo / Remittance Detail": memo,
    }
    row.update(overrides)
    return row


def invoice(number: str, amount, **overrides) -> dict:
    row = {"Invoice Number": number, "Open Amount": amount}
    row.update(overrides)
    return row


def run(bank_rows: list[dict], ar_rows: list[dict]) -> dict:
    """Run the matching loop on synthetic rows and return the outcome."""
    bank_df = pd.DataFrame([{**_BANK_DEFAULTS, **r} for r in bank_rows])
    ar_df = pd.DataFrame([{**_AR_DEFAULTS, **r} for r in ar_rows])
    bank_df, ar_df = ReconciliationService.prepare_frames(bank_df, ar_df)
    return ReconciliationService().reconcile(ar_df=ar_df, bank_df=bank_df)


def bank_row(outcome: dict, txn_id: str) -> pd.Series:
    bank = outcome["bank_df"]
    rows = bank[bank["Bank Transaction ID"] == txn_id]
    assert len(rows) == 1, f"expected one bank row for {txn_id}, found {len(rows)}"
    return rows.iloc[0]


def ar_row(outcome: dict, invoice_number: str) -> pd.Series:
    ar = outcome["ar_df"]
    rows = ar[ar["Invoice Number"] == invoice_number]
    assert len(rows) == 1, f"expected one AR row for {invoice_number}, found {len(rows)}"
    return rows.iloc[0]


def cents(value) -> int:
    return money_to_cents(value)


def unmatched_ids(outcome: dict) -> set[str]:
    return {str(r["Bank Transaction ID"]) for r in outcome["unmatched_bank_rows"]}


def data_quality_text(outcome: dict) -> str:
    return " ".join(str(v) for row in outcome["data_quality_rows"] for v in row.values())


# ---------------------------------------------------------------------------
# Behaviour that is correct today and must stay correct
# ---------------------------------------------------------------------------


def test_clean_one_to_one_memo_match_is_reconciled():
    outcome = run([receipt("B1", 1000, "Payment INV-1")], [invoice("INV-1", 1000)])

    bank = bank_row(outcome, "B1")
    assert bank["Suggested Bank Status"] == "Reconciled"
    assert cents(bank["AR Applied Amount Total"]) == 100000
    assert ar_row(outcome, "INV-1")["Suggested Recon Status"] == "Reconciled"


def test_one_receipt_can_settle_several_memo_invoices():
    outcome = run(
        [receipt("B1", 1000, "Remit INV-1 INV-2")],
        [invoice("INV-1", 400), invoice("INV-2", 600)],
    )

    assert bank_row(outcome, "B1")["Suggested Bank Status"] == "Reconciled"
    assert cents(ar_row(outcome, "INV-1")["Applied Amount"]) == 40000
    assert cents(ar_row(outcome, "INV-2")["Applied Amount"]) == 60000


def test_memo_invoice_of_another_customer_is_not_applied():
    outcome = run(
        [receipt("B1", 1000, "Payment INV-9")],
        [invoice("INV-9", 1000, **{"ERP Customer ID": "C2", "Customer Name": "Other"})],
    )

    assert "B1" in unmatched_ids(outcome)
    assert cents(ar_row(outcome, "INV-9")["Applied Amount"]) == 0


def test_memo_invoice_in_another_currency_is_not_applied():
    outcome = run(
        [receipt("B1", 1000, "Payment INV-1", Currency="CAD")],
        [invoice("INV-1", 1000)],
    )

    assert "B1" in unmatched_ids(outcome)
    assert cents(ar_row(outcome, "INV-1")["Applied Amount"]) == 0


def test_negative_receipt_is_routed_to_review():
    outcome = run([receipt("B1", -250, "Chargeback INV-1")], [invoice("INV-1", 250)])

    assert "B1" in unmatched_ids(outcome)
    assert cents(ar_row(outcome, "INV-1")["Applied Amount"]) == 0


def test_identical_receipt_with_same_invoice_reference_is_held_as_duplicate():
    outcome = run(
        [
            receipt("B1", 1000, "Payment INV-1"),
            receipt("B2", 1000, "Payment INV-1", **{"Value Date": "2026-06-02"}),
        ],
        [invoice("INV-1", 1000)],
    )

    assert bank_row(outcome, "B1")["Suggested Bank Status"] == "Reconciled"
    assert cents(bank_row(outcome, "B2")["AR Applied Amount Total"]) == 0
    assert "B2" in unmatched_ids(outcome)


def test_an_invoice_is_never_applied_beyond_its_open_amount():
    outcome = run(
        [
            receipt("B1", 1000, "payment"),
            receipt("B2", 1000, "payment", **{"Value Date": "2026-06-02"}),
        ],
        [invoice("INV-1", 1000)],
    )

    assert cents(ar_row(outcome, "INV-1")["Applied Amount"]) == 100000
    assert "B2" in unmatched_ids(outcome)


# ---------------------------------------------------------------------------
# Fixed defects from the codebase review
# ---------------------------------------------------------------------------


def test_partial_payment_naming_two_invoices_does_not_crash():
    outcome = run(
        [receipt("B1", 500, "Partial payment INV-1 INV-2")],
        [invoice("INV-1", 400), invoice("INV-2", 600)],
    )

    assert bank_row(outcome, "B1")["Suggested Bank Status"] != "Reconciled"


def test_rounding_variance_naming_two_invoices_does_not_crash():
    outcome = run(
        [receipt("B1", 1000.01, "penny rounding INV-1 INV-2")],
        [invoice("INV-1", 400), invoice("INV-2", 600)],
    )

    assert bank_row(outcome, "B1")["Suggested Bank Status"] != "Reconciled"


def test_overpayment_excess_is_recorded_as_unapplied_cash():
    outcome = run([receipt("B1", 1050, "overpayment INV-1")], [invoice("INV-1", 1000)])

    bank = bank_row(outcome, "B1")
    applied = cents(bank["AR Applied Amount Total"])
    unapplied = cents(bank.get("Unapplied Amount", 0))
    assert applied == 100000
    assert applied + unapplied == 105000


def test_ambiguous_amount_combination_is_not_auto_reconciled():
    outcome = run(
        [receipt("B1", 300, "payment")],
        [
            invoice("INV-1", 100),
            invoice("INV-2", 200),
            invoice("INV-3", 300),
            invoice("INV-4", 300),
        ],
    )

    assert bank_row(outcome, "B1")["Suggested Bank Status"] != "Reconciled"
    assert "B1" in unmatched_ids(outcome)


def test_receipt_matching_two_equal_invoices_is_not_auto_reconciled():
    outcome = run(
        [receipt("B1", 100, "inv 1")],
        [invoice("INV-1", 100), invoice("INV-2", 100)],
    )

    assert bank_row(outcome, "B1")["Suggested Bank Status"] != "Reconciled"


def test_repeated_bank_transaction_id_is_applied_at_most_once():
    outcome = run(
        [receipt("B1", 100, "payment"), receipt("B1", 100, "payment")],
        [invoice("INV-1", 100, **{"Due Date": "2026-05-01"}), invoice("INV-2", 100)],
    )

    bank = outcome["bank_df"]
    applied_rows = bank[bank["AR Applied Amount Total"].apply(cents) > 0]
    assert len(applied_rows) <= 1
    assert "B1" in data_quality_text(outcome)


def test_malformed_amount_is_reported_not_raised():
    outcome = run(
        [receipt("B1", "12O.00", "Payment INV-1", **{"Net Amount": 120})],
        [invoice("INV-1", 120)],
    )

    assert "B1" in data_quality_text(outcome)
    assert cents(ar_row(outcome, "INV-1")["Applied Amount"]) == 0


def test_equal_instalments_on_one_invoice_are_both_applied():
    outcome = run(
        [
            receipt("B1", 500, "installment INV-1"),
            receipt("B2", 500, "installment INV-1", **{"Value Date": "2026-07-01"}),
        ],
        [invoice("INV-1", 1000)],
    )

    assert cents(ar_row(outcome, "INV-1")["Applied Amount"]) == 100000
    assert cents(bank_row(outcome, "B2")["AR Applied Amount Total"]) == 50000


def test_duplicate_invoice_number_is_not_auto_reconciled():
    outcome = run(
        [receipt("B1", 200, "Payment INV-1")],
        [invoice("INV-1", 100), invoice("INV-1", 100)],
    )

    assert bank_row(outcome, "B1")["Suggested Bank Status"] != "Reconciled"
    assert "INV-1" in data_quality_text(outcome)


# ---------------------------------------------------------------------------
# Controls (D4): they must tie on a clean run and must be able to fail
# ---------------------------------------------------------------------------


def _mixed_run() -> dict:
    """A run that exercises every cash bucket."""
    return run(
        [
            receipt("B1", 1000, "Payment INV-1"),  # applied
            receipt("B2", 1050, "overpayment INV-2"),  # applied + unapplied
            receipt("B3", 990, "bank fee INV-3", **{"Bank Fees": 10, "Net Amount": 980}),
            receipt("B4", 700, "payment"),  # no candidate -> held
            receipt("B5", 300, "Payment INV-9", Status="Reconciled"),  # skipped
        ],
        [
            invoice("INV-1", 1000),
            invoice("INV-2", 1000),
            invoice("INV-3", 980),
        ],
    )


def test_controls_tie_on_a_clean_run():
    outcome = _mixed_run()
    controls = compute_controls(outcome["ar_df"], outcome["bank_df"])

    assert controls.balanced, controls.describe_breaks()
    assert controls.receipts_gross_cents == 404000
    assert controls.applied_cents == 298000
    assert controls.unapplied_cents == 5000
    assert controls.adjustment_cents == 1000
    assert controls.held_cents == 70000
    assert controls.skipped_cents == 30000
    assert controls.ar_remaining_cents == 0


def test_cash_control_fails_when_a_receipt_is_not_fully_accounted_for():
    outcome = _mixed_run()
    bank = outcome["bank_df"]
    # Simulate the original defect: the overpayment excess goes nowhere.
    bank.loc[bank["Bank Transaction ID"] == "B2", "Unapplied Amount"] = 0.0

    controls = compute_controls(outcome["ar_df"], bank)

    assert not controls.balanced
    assert controls.cash_difference_cents == 5000
    assert controls.cash_breaks == ["B2"]


def test_rollforward_fails_when_an_invoice_is_over_applied():
    outcome = _mixed_run()
    ar = outcome["ar_df"]
    bank = outcome["bank_df"]
    # Over-apply INV-1 on both sides so the bank/AR agreement still holds;
    # only the roll-forward can catch it.
    ar.loc[ar["Invoice Number"] == "INV-1", "Applied Amount"] = 1100.0
    bank.loc[bank["Bank Transaction ID"] == "B1", "AR Applied Amount Total"] = 1100.0
    bank.loc[bank["Bank Transaction ID"] == "B1", "Gross Receipt Amount"] = 1100
    bank.loc[bank["Bank Transaction ID"] == "B1", "__gross_amount_cents"] = 110000

    controls = compute_controls(ar, bank)

    assert controls.cash_difference_cents == 0
    assert controls.applied_difference_cents == 0
    assert not controls.balanced
    assert controls.rollforward_breaks == ["INV-1"]


def test_ambiguous_receipt_is_held_with_its_alternatives():
    outcome = run(
        [receipt("B1", 300, "payment")],
        [invoice("INV-3", 300), invoice("INV-4", 300)],
    )

    reason = next(r["Reason"] for r in outcome["unmatched_bank_rows"])
    assert "Ambiguous amount match" in reason
    assert "INV-3" in reason and "INV-4" in reason
    assert cents(bank_row(outcome, "B1")["Held For Review Amount"]) == 30000
