"""
Structured inputs (Customer Master, Contract Schedule, Remittance Advice)
feeding the deterministic engine before matching.

Each scenario builds a small bank file and AR ledger, runs the real engine
with and without the structured inputs, and checks the outcome and the
controls.  Decisions: G1 (deductions to review), G2 (only structured files
drive automatic matches), G3 (unique customer alias).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ar_reconciliation.core.primitives import money_to_cents
from ar_reconciliation.enrichment import load_inputs
from ar_reconciliation.services import run_reconciliation

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "sample" / "enriched"


def bank(txn, amount, memo="", customer_id="C1", name="Acme Corp", ref="", currency="USD", date="2026-06-10"):
    return {"Bank Transaction ID": txn, "Value Date": date, "Posting Date": date, "Customer Name": name,
            "Remitter Name": name, "ERP Customer ID": customer_id, "Currency": currency,
            "Gross Receipt Amount": amount, "Bank Fees": 0, "Net Amount": amount, "Status": "Unreconciled",
            "Memo / Remittance Detail": memo, "Bank Reference": ref}


def inv(number, amount, customer_id="C1", name="Acme Corp", status="Open", dispute="", currency="USD"):
    return {"ERP Customer ID": customer_id, "Parent Customer ID": "", "Customer Name": name, "Invoice Number": number,
            "Invoice Date": "2026-05-01", "Due Date": "2026-05-31", "Currency": currency, "Open Amount": amount,
            "AR Status": status, "Reconciliation Status": "Open", "Dispute Reason": dispute}


def remit(rid, invoice, paid, ref="", txn="", deduction="", code="", reason="", payer="Acme Corp", customer="C1"):
    return {"remittance_id": rid, "payment_reference": ref, "bank_transaction_id": txn, "payer_name": payer,
            "customer_id": customer, "payment_date": "2026-06-10", "currency": "USD", "invoice_number": invoice,
            "amount_paid": paid, "deduction_amount": deduction, "deduction_code": code, "deduction_reason": reason}


def run(tmp_path, bank_rows, ar_rows, remittances=None, contracts=None, customers=None, name="r"):
    bank_path, ar_path = tmp_path / f"{name}_bank.xlsx", tmp_path / f"{name}_ar.xlsx"
    pd.DataFrame(bank_rows).to_excel(bank_path, sheet_name="Bank Receipts", index=False)
    pd.DataFrame(ar_rows).to_excel(ar_path, sheet_name="Open AR", index=False)
    inputs = None
    if remittances is not None or contracts is not None or customers is not None:
        inputs = load_inputs(
            customer_master=pd.DataFrame(customers) if customers is not None else None,
            contracts=pd.DataFrame(contracts) if contracts is not None else None,
            remittances=pd.DataFrame(remittances) if remittances is not None else None,
        )
    out = tmp_path / f"{name}.xlsx"
    result = run_reconciliation(bank_path=bank_path, ar_path=ar_path, output_path=out, verbose=False,
                                enrichment_inputs=inputs)
    assert result.controls.balanced
    sheets = pd.ExcelFile(out)
    return {s: pd.read_excel(sheets, s) for s in sheets.sheet_names}


def receipt(sheets, txn) -> pd.Series:
    b = sheets["Processed Bank Receipts"]
    return b.loc[b["Bank Transaction ID"] == txn].iloc[0]


def applied(sheets, invoice) -> int:
    ar = sheets["Reconciled Open AR"]
    return money_to_cents(ar.loc[ar["Invoice Number"] == invoice, "Applied Amount"].iloc[0] or 0)


PO_MEMO = "Payment against PO-1; no invoice listed"


class TestRemittanceScenarios:
    def test_identical_invoice_amounts(self, tmp_path):
        ar = [inv("INV-1", 500), inv("INV-2", 500)]
        without = run(tmp_path, [bank("B1", 500, ref="R1")], ar, name="without")
        assert receipt(without, "B1")["Suggested Bank Status"] != "Reconciled"  # ambiguous (D1)
        with_ = run(tmp_path, [bank("B1", 500, ref="R1")], ar, remittances=[remit("RA1", "INV-2", "500.00", ref="R1")])
        assert receipt(with_, "B1")["Match Method"] == "Remittance Advice Allocation"
        assert applied(with_, "INV-2") == 50000 and applied(with_, "INV-1") == 0

    def test_multiple_invoice_payment_and_partial(self, tmp_path):
        ar = [inv("INV-1", 1000), inv("INV-2", 800)]
        sheets = run(tmp_path, [bank("B1", 1300, memo=PO_MEMO, ref="R9")], ar,
                     remittances=[remit("RA1", "INV-1", "1000.00", ref="R9"), remit("RA1", "INV-2", "300.00", ref="R9")])
        assert applied(sheets, "INV-1") == 100000 and applied(sheets, "INV-2") == 30000
        assert "Partial" in receipt(sheets, "B1")["Match Method"]

    def test_authorized_deduction_is_proposed_not_applied(self, tmp_path):
        ar = [inv("INV-1", 1000)]
        contracts = [{"contract_id": "K1", "customer_id": "C1", "invoice_number": "INV-1", "installment_amount": "1000.00",
                      "allowed_deduction_codes": "WHT", "max_deduction_pct": "3"}]
        sheets = run(tmp_path, [bank("B1", 970, ref="R1")], ar, contracts=contracts,
                     remittances=[remit("RA1", "INV-1", "970.00", ref="R1", deduction="30.00", code="WHT")])
        assert applied(sheets, "INV-1") == 97000
        deduction = sheets["Proposed Deductions"].iloc[0]
        assert deduction["Authorization"] == "Authorized" and deduction["Applied"] == "No"
        ar_row = sheets["Reconciled Open AR"].iloc[0]
        assert money_to_cents(ar_row["Residual Amount"]) == 3000  # the deduction stays open until approved
        assert receipt(sheets, "B1")["Suggested Bank Status"] == "Pending Review"

    def test_unauthorized_deduction(self, tmp_path):
        ar = [inv("INV-1", 1000)]
        contracts = [{"contract_id": "K1", "customer_id": "C1", "invoice_number": "INV-1", "installment_amount": "1000.00",
                      "allowed_deduction_codes": "WHT", "max_deduction_pct": "3"}]
        sheets = run(tmp_path, [bank("B1", 900, ref="R1")], ar, contracts=contracts,
                     remittances=[remit("RA1", "INV-1", "900.00", ref="R1", deduction="100.00", code="WHT")])
        deduction = sheets["Proposed Deductions"].iloc[0]
        assert deduction["Authorization"] == "Unauthorized" and "limit" in deduction["Authorization Rule"]

    def test_contract_installment(self, tmp_path):
        ar = [inv("INV-1", 3000)]
        contracts = [{"contract_id": "K1", "customer_id": "C1", "invoice_number": "INV-1", "installment_number": "1",
                      "installment_count": "3", "installment_amount": "1000.00"}]
        sheets = run(tmp_path, [bank("B1", 1000, memo=PO_MEMO)], ar, contracts=contracts)
        assert receipt(sheets, "B1")["Match Method"].startswith("Contract Installment Schedule")
        assert applied(sheets, "INV-1") == 100000

    def test_conflicting_remittances_block_automatic_matching(self, tmp_path):
        ar = [inv("INV-1", 600), inv("INV-2", 600)]
        sheets = run(tmp_path, [bank("B1", 600)], ar,
                     remittances=[remit("RA1", "INV-1", "600.00", txn="B1"), remit("RA2", "INV-2", "600.00", txn="B1")])
        row = receipt(sheets, "B1")
        assert money_to_cents(row["AR Applied Amount Total"] or 0) == 0
        assert "Conflicting remittance" in row["Structured Evidence"]

    def test_duplicate_evidence_applied_once(self, tmp_path):
        ar = [inv("INV-1", 500), inv("INV-2", 500)]
        sheets = run(tmp_path, [bank("B1", 500, ref="R1")], ar,
                     remittances=[remit("RA1", "INV-2", "500.00", ref="R1"), remit("RA9", "INV-2", "500.00", ref="R1")])
        assert applied(sheets, "INV-2") == 50000
        assert "duplicate" in receipt(sheets, "B1")["Structured Evidence"]

    def test_remittance_total_must_equal_receipt(self, tmp_path):
        sheets = run(tmp_path, [bank("B1", 500, ref="R1")], [inv("INV-1", 900)],
                     remittances=[remit("RA1", "INV-1", "450.00", ref="R1")])
        assert money_to_cents(receipt(sheets, "B1")["AR Applied Amount Total"] or 0) == 0

    def test_hold_scenario_is_not_overridden(self, tmp_path):
        sheets = run(tmp_path, [bank("B1", 700, memo="INV-1; collector hold")], [inv("INV-1", 700)],
                     remittances=[remit("RA1", "INV-1", "700.00", txn="B1")])
        assert money_to_cents(receipt(sheets, "B1")["AR Applied Amount Total"] or 0) == 0

    def test_third_party_invoice_goes_to_review(self, tmp_path):
        ar = [inv("INV-9", 400, customer_id="C9", name="Other Co")]
        sheets = run(tmp_path, [bank("B1", 400, memo=PO_MEMO)], ar,
                     remittances=[remit("RA1", "INV-9", "400.00", txn="B1")])
        assert money_to_cents(receipt(sheets, "B1")["AR Applied Amount Total"] or 0) == 0
        assert "third-party" in receipt(sheets, "B1")["Structured Evidence"]

    def test_missing_evidence_keeps_baseline_behavior(self, tmp_path):
        ar = [inv("INV-1", 500), inv("INV-2", 500)]
        base = run(tmp_path, [bank("B1", 500), bank("B2", 500, memo="INV-1")], ar, name="base")
        enriched = run(tmp_path, [bank("B1", 500), bank("B2", 500, memo="INV-1")], ar,
                       remittances=[remit("RA1", "INV-7", "1.00", txn="ZZ")], name="enriched")
        cols = ["Bank Transaction ID", "Suggested Bank Status", "AR Applied Amount Total"]
        pd.testing.assert_frame_equal(base["Processed Bank Receipts"][cols], enriched["Processed Bank Receipts"][cols])


class TestCustomerMaster:
    CUSTOMERS = [{"customer_id": "C1", "customer_name": "Acme Corp", "aliases": "Acme Holdings;ACME Intl", "currency": "USD"}]

    def test_unique_alias_resolves_a_missing_customer_id(self, tmp_path):
        sheets = run(tmp_path, [bank("B1", 250, memo="INV-1", customer_id="", name="ACME Intl")],
                     [inv("INV-1", 250)], customers=self.CUSTOMERS)
        row = receipt(sheets, "B1")
        assert money_to_cents(row["AR Applied Amount Total"]) == 25000
        assert "Customer Master" in row["Evidence Source"]

    def test_ambiguous_alias_is_not_used(self, tmp_path):
        customers = self.CUSTOMERS + [{"customer_id": "C2", "customer_name": "Beta", "aliases": "ACME Intl"}]
        sheets = run(tmp_path, [bank("B1", 250, memo="INV-1", customer_id="", name="ACME Intl")],
                     [inv("INV-1", 250)], customers=customers)
        assert "several customers" in receipt(sheets, "B1")["Structured Evidence"]

    def test_alias_in_another_currency_is_not_used(self, tmp_path):
        customers = [{"customer_id": "C1", "customer_name": "Acme Corp", "aliases": "ACME Intl", "currency": "CAD"}]
        sheets = run(tmp_path, [bank("B1", 250, memo="INV-1", customer_id="", name="ACME Intl")],
                     [inv("INV-1", 250)], customers=customers)
        assert "another currency" in receipt(sheets, "B1")["Structured Evidence"]


class TestLoaders:
    def test_missing_columns_and_bad_rows_are_reported(self):
        inputs = load_inputs(remittances=pd.DataFrame([
            {"remittance_id": "R1", "invoice_number": "INV-1", "amount_paid": "abc"},
            {"remittance_id": "R2", "invoice_number": "", "amount_paid": "10.00"},
            {"remittance_id": "R3", "invoice_number": "INV-3", "amount_paid": "10.00"},
        ]), customer_master=pd.DataFrame([{"name": "x"}]))
        assert len(inputs.remittances) == 1
        assert any("not a readable amount" in i["Issue"] for i in inputs.issues)
        assert any("Missing required columns" in i["Issue"] for i in inputs.issues)

    def test_csv_and_excel_are_both_read(self, tmp_path):
        frame = pd.DataFrame([{"customer_id": "C1", "customer_name": "Acme"}])
        frame.to_csv(tmp_path / "c.csv", index=False)
        frame.to_excel(tmp_path / "c.xlsx", index=False)
        assert len(load_inputs(customer_master=tmp_path / "c.csv").customers) == 1
        assert len(load_inputs(customer_master=tmp_path / "c.xlsx").customers) == 1


def test_demo_pack_outcomes_match_the_ground_truth(tmp_path):
    inputs = load_inputs(DEMO / "customer_master.csv", DEMO / "contract_schedule.csv", DEMO / "remittance_advice.csv")
    out = tmp_path / "demo.xlsx"
    result = run_reconciliation(output_path=out, verbose=False, enrichment_inputs=inputs)
    assert result.controls.balanced
    bank_df = pd.read_excel(out, sheet_name="Processed Bank Receipts").set_index("Bank Transaction ID")
    ar = pd.read_excel(out, sheet_name="Reconciled Open AR")
    expected = pd.read_csv(DEMO / "expected_matches.csv", keep_default_na=False)
    for _, row in expected.iterrows():
        cash = money_to_cents(bank_df.loc[row["bank_transaction_id"], "AR Applied Amount Total"] or 0)
        if row["expected_outcome"] == "Review":
            assert cash == 0, row["bank_transaction_id"]
        else:
            for part in row["expected_allocations"].split(";"):
                invoice, amount = part.split(":")
                applied_cents = money_to_cents(ar.loc[ar["Invoice Number"] == invoice, "Applied Amount"].iloc[0])
                assert applied_cents >= money_to_cents(amount), (row["bank_transaction_id"], invoice)


class TestDeductionDecisions:
    CONTRACT = [{"contract_id": "K1", "customer_id": "C1", "invoice_number": "INV-1", "installment_amount": "1000.00",
                 "allowed_deduction_codes": "WHT", "max_deduction_pct": "3"}]

    def _run(self, tmp_path, deduction="30.00"):
        bank_path, ar_path = tmp_path / "b.xlsx", tmp_path / "a.xlsx"
        pd.DataFrame([bank("B1", 1000 - float(deduction), ref="R1")]).to_excel(bank_path, sheet_name="Bank Receipts", index=False)
        pd.DataFrame([inv("INV-1", 1000)]).to_excel(ar_path, sheet_name="Open AR", index=False)
        inputs = load_inputs(contracts=pd.DataFrame(self.CONTRACT), remittances=pd.DataFrame(
            [remit("RA1", "INV-1", f"{1000 - float(deduction):.2f}", ref="R1", deduction=deduction, code="WHT")]))
        out = tmp_path / "r.xlsx"
        run_reconciliation(bank_path=bank_path, ar_path=ar_path, output_path=out, verbose=False, enrichment_inputs=inputs)
        return out

    def test_approved_authorized_deduction_closes_the_invoice_once(self, tmp_path):
        from ar_reconciliation.agent.deductions import record_deduction_decision
        from ar_reconciliation.agent.workflow import apply_decisions

        out = self._run(tmp_path)
        record_deduction_decision(out, "DED-B1-INV-1", "APPROVE", "A. Analyst")
        outcome = apply_decisions(out)
        assert outcome.controls.balanced
        ar = pd.read_excel(out, sheet_name="Reconciled Open AR").iloc[0]
        assert money_to_cents(ar["Approved Adjustment Amount"]) == 3000
        assert money_to_cents(ar["Residual Amount"]) == 0 and ar["Suggested Recon Status"] == "Reconciled"
        assert receipt({"Processed Bank Receipts": pd.read_excel(out, sheet_name="Processed Bank Receipts")},
                       "B1")["Suggested Bank Status"] == "Reconciled"
        again = apply_decisions(out)
        assert money_to_cents(pd.read_excel(out, sheet_name="Reconciled Open AR").iloc[0]["Approved Adjustment Amount"]) == 3000
        assert again.controls.balanced
        audit = pd.read_excel(out, sheet_name="Audit Trail")
        assert (audit["Event"] == "Deduction approved").sum() == 1

    def test_unauthorized_deduction_cannot_be_approved(self, tmp_path):
        from ar_reconciliation.agent.deductions import record_deduction_decision

        out = self._run(tmp_path, deduction="100.00")
        with pytest.raises(ValueError, match="unauthorized"):
            record_deduction_decision(out, "DED-B1-INV-1", "APPROVE", "A. Analyst")

    def test_rejected_deduction_leaves_the_balance_open(self, tmp_path):
        from ar_reconciliation.agent.deductions import record_deduction_decision
        from ar_reconciliation.agent.workflow import apply_decisions

        out = self._run(tmp_path)
        record_deduction_decision(out, "DED-B1-INV-1", "REJECT", "A. Analyst", "collect from customer")
        apply_decisions(out)
        ar = pd.read_excel(out, sheet_name="Reconciled Open AR").iloc[0]
        assert money_to_cents(ar["Residual Amount"]) == 3000


def test_enrichment_comparison_on_the_demo_pack(tmp_path):
    from ar_reconciliation.enrichment.comparison import COMPARISON_SHEET, run_with_comparison

    inputs = load_inputs(DEMO / "customer_master.csv", DEMO / "contract_schedule.csv", DEMO / "remittance_advice.csv")
    out = tmp_path / "enriched.xlsx"
    result, frame = run_with_comparison(None, None, out, inputs, DEMO / "expected_matches.csv")
    metrics = frame.set_index("Metric")
    before = int(metrics.loc["Receipts matched automatically", "Baseline (Bank + AR)"])
    after = int(metrics.loc["Receipts matched automatically", "Enriched"])
    assert after > before
    assert int(metrics.loc["Receipts needing AI investigation", "Enriched"]) < int(
        metrics.loc["Receipts needing AI investigation", "Baseline (Bank + AR)"])
    assert metrics.loc["Matching accuracy - correct automatic matches", "Enriched"].startswith("100.0%")
    assert metrics.loc["Financial controls", "Enriched"] == "Passed"
    assert COMPARISON_SHEET in pd.ExcelFile(out).sheet_names
    assert (tmp_path / "enriched_baseline.xlsx").exists()
