"""
Tests for the enterprise reporting layer.

Unit tests run the analytics on small synthetic frames; workbook tests use
the session's reconciled sample workbook (tests/conftest.py).
"""

from __future__ import annotations

import time
import zipfile
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from openpyxl import load_workbook

from ar_reconciliation.core.primitives import money_to_cents
from ar_reconciliation.reporting.aging import UNKNOWN_BUCKET, aging_detail, aging_summary, customer_risk
from ar_reconciliation.reporting.builder import build_report_model, resolve_as_of
from ar_reconciliation.reporting.config import FxRate, load_config, load_fx_rates
from ar_reconciliation.reporting.controls_matrix import control_matrix, overall_control_status
from ar_reconciliation.reporting.excel_exporter import (
    DASHBOARD,
    REPORT_SHEETS,
    neutralize_formulas,
    write_enterprise_report,
)
from ar_reconciliation.reporting.exceptions import QUEUE_COLUMNS
from ar_reconciliation.reporting.metrics import compute_metrics, consolidate, rate
from ar_reconciliation.reporting.statuses import MATCH_STATUSES, receipt_statuses
from ar_reconciliation.services.controls import ControlResult

HEADER = 3


def bank_row(txn, currency="USD", gross=100.0, applied=0.0, unapplied=0.0, held=None, status="Unreconciled",
             bank_status="Pending Review", method="", ai=0.0, adjustment=0.0):
    held = (gross if applied == 0 and status not in {"Reconciled"} else 0.0) if held is None else held
    return {
        "Bank Transaction ID": txn, "Value Date": "2026-06-01", "Posting Date": "2026-06-01",
        "Customer Name": "Acme", "ERP Customer ID": "C1", "Currency": currency,
        "Gross Receipt Amount": gross, "Bank Fees": 0, "Status": status,
        "AR Applied Amount Total": applied, "Unapplied Amount": unapplied,
        "Fee / Rounding Adjustment": adjustment, "Held For Review Amount": held,
        "Suggested Bank Status": bank_status, "Match Method": method, "AI-Resolved Applied Amount": ai,
        "Memo / Remittance Detail": "", "Memo Scenario": "", "Extracted Invoice Numbers": "",
    }


def ar_row(invoice, open_amount, applied=0.0, due="2026-06-01", currency="USD", customer="Acme", **extra):
    row = {"ERP Customer ID": "C1", "Customer Name": customer, "Invoice Number": invoice, "Currency": currency,
           "Open Amount": open_amount, "Applied Amount": applied, "Due Date": due, "AR Status": "Open",
           "Reconciliation Status": "Open", "Dispute Reason": "", "Matched Bank Transaction ID": ""}
    row.update(extra)
    return row


@pytest.fixture()
def config():
    return load_config(as_of="2026-06-30")


# ---------------------------------------------------------------------------
# Financial accuracy
# ---------------------------------------------------------------------------


class TestStatusesAndMetrics:
    def test_match_status_is_mutually_exclusive_and_complete(self, config):
        bank = pd.DataFrame([
            bank_row("F1", applied=100, bank_status="Reconciled", method="Memo Invoice Match"),
            bank_row("P1", gross=120, applied=100, unapplied=20, bank_status="Pending Review"),
            bank_row("U1"),
            bank_row("R1", gross=-50),
            bank_row("S1", status="Reconciled", held=0, bank_status="Reconciled"),
        ])
        statuses = receipt_statuses(bank)
        assert list(statuses["Match Status"]) == [
            "Fully Matched", "Partially Matched", "Unmatched", "Refund / Reversal", "Previously Reconciled"]
        metrics = compute_metrics(statuses, pd.DataFrame(), config)
        assert sum(metrics.match_counts.values()) == metrics.total_receipts == 5
        assert set(metrics.match_counts) == set(MATCH_STATUSES)

    def test_review_status_is_independent_of_match_status(self):
        bank = pd.DataFrame([bank_row("F1", applied=100, bank_status="Pending Review", method="Memo Invoice Match - Bank Fee")])
        statuses = receipt_statuses(bank)
        assert statuses.loc[0, "Match Status"] == "Fully Matched"
        assert statuses.loc[0, "Review Status"] == "Pending Review"
        assert statuses.loc[0, "Posting Status"].startswith("Not Posted")

    def test_review_status_comes_only_from_recorded_decisions(self):
        bank = pd.DataFrame([bank_row("A1"), bank_row("A2"), bank_row("A3"), bank_row("A4")])
        review = pd.DataFrame([
            {"Bank Transaction ID": "A1", "Approval": "APPROVE", "Applied To AR": "Yes"},
            {"Bank Transaction ID": "A2", "Approval": "APPROVE", "Applied To AR": "No"},
            {"Bank Transaction ID": "A3", "Approval": "REJECT", "Applied To AR": "No"},
            {"Bank Transaction ID": "A4", "Approval": "", "Applied To AR": "No"},
        ])
        statuses = receipt_statuses(bank, review)
        assert list(statuses["Review Status"]) == [
            "Approved", "Approved - Not Yet Applied", "Rejected", "Pending Review"]

    def test_denominators_exclude_previously_reconciled_and_handle_zero(self, config):
        bank = pd.DataFrame([
            bank_row("F1", applied=100, bank_status="Reconciled", method="Memo Invoice Match"),
            bank_row("U1"),
            bank_row("S1", status="Reconciled", held=0, bank_status="Reconciled"),
        ])
        metrics = compute_metrics(receipt_statuses(bank), pd.DataFrame(), config)
        assert metrics.in_scope_receipts == 2
        assert metrics.auto_match_rate == pytest.approx(0.5)
        assert metrics.exception_rate == pytest.approx(0.5)
        assert rate(5, 0) is None

    def test_ai_applied_receipts_are_not_auto_matched(self, config):
        bank = pd.DataFrame([bank_row("A1", applied=100, bank_status="Reconciled",
                                      method="AI Approved Resolution", ai=100)])
        metrics = compute_metrics(receipt_statuses(bank), pd.DataFrame(), config)
        assert metrics.auto_matched == 0 and metrics.matched_receipts == 1

    def test_invoice_paid_by_two_receipts_counts_once(self, config):
        ar = pd.DataFrame([ar_row("INV-1", 100, applied=100), ar_row("INV-2", 50)])
        metrics = compute_metrics(receipt_statuses(pd.DataFrame([bank_row("X")])), ar, config,
                                  invoice_applications=2)
        assert metrics.invoices_with_cash == 1 and metrics.invoice_applications == 2

    def test_cash_figures_stay_per_currency_and_tie(self, config):
        bank = pd.DataFrame([
            bank_row("U1", currency="USD", gross=120, applied=100, unapplied=15, adjustment=5,
                     bank_status="Pending Review"),
            bank_row("U2", currency="USD", gross=40),
            bank_row("U3", currency="USD", gross=-30),
            bank_row("C1", currency="CAD", gross=200, applied=200, bank_status="Reconciled"),
        ])
        metrics = compute_metrics(receipt_statuses(bank), pd.DataFrame(), config)
        cash = metrics.cash_by_currency.set_index("Currency")
        assert set(cash.index) == {"USD", "CAD"}
        usd = cash.loc["USD"]
        assert usd["Gross Receipts"] == 16000  # positive receipts only, in cents
        assert usd["Gross Receipts"] == (usd["Applied to AR"] + usd["Unapplied Cash"]
                                         + usd["Fee / Rounding Adjustment"] + usd["Held for Review"]
                                         + usd["Previously Reconciled"])
        assert usd["Refunds / Reversals"] == -3000
        assert usd["Unapplied Cash"] == 1500
        assert usd["Cash Application Rate"] == pytest.approx(100 / 160)
        assert cash.loc["CAD", "Cash Application Rate"] == pytest.approx(1.0)


class TestCurrencyConsolidation:
    def test_no_reporting_currency_means_no_total(self, config):
        total, reason = consolidate({"USD": 100, "CAD": 200}, config)
        assert total is None and "original currency" in reason

    def test_missing_rate_blocks_the_total(self, config):
        config.reporting_currency = "USD"
        config.fx_rates = {"CAD": FxRate("CAD", Decimal("0.73"), "2026-06-30", "Test")}
        total, reason = consolidate({"USD": 100, "CAD": 200, "EUR": 50}, config)
        assert total is None and "EUR" in reason

    def test_rates_convert_with_half_up_rounding(self, config):
        config.reporting_currency = "USD"
        config.fx_rates = {"CAD": FxRate("CAD", Decimal("0.7315"), "2026-06-30", "Test")}
        total, _ = consolidate({"USD": 10000, "CAD": 333}, config)
        assert total == 10000 + 244  # 333 x 0.7315 = 243.59 -> 244

    def test_rate_file_requires_rate_date_and_source(self, tmp_path):
        path = tmp_path / "fx.csv"
        path.write_text("currency,rate_to_reporting,rate_date,source\nCAD,0.73,2026-06-30,Bank\nEUR,1.08,,\n")
        rates, status = load_fx_rates(path)
        assert set(rates) == {"CAD"} and "loaded" in status
        assert load_fx_rates(None) == ({}, "No FX rates supplied")

    def test_no_rate_is_ever_assumed(self):
        config = load_config()
        assert config.fx_rates == {} and config.reporting_currency is None


class TestControlMatrix:
    def test_differences_do_not_net_across_currencies(self):
        usd = ControlResult(receipts_gross_cents=100, applied_cents=100, ar_applied_cents=100,
                            cash_difference_cents=5, cash_breaks=["U1"])
        cad = ControlResult(receipts_gross_cents=100, applied_cents=100, ar_applied_cents=100,
                            cash_difference_cents=-5, cash_breaks=["C1"])
        whole = ControlResult(cash_difference_cents=0, by_currency={"USD": usd, "CAD": cad})
        matrix = control_matrix(whole, pd.DataFrame())
        cash = matrix[matrix["Control"] == "Cash Control"].set_index("Currency")["Status"]
        assert cash["USD"] == "FAIL" and cash["CAD"] == "FAIL"
        assert overall_control_status(matrix) == "Failed"

    def test_data_quality_issues_are_a_warning(self):
        matrix = control_matrix(ControlResult(), pd.DataFrame([{"Issue": "Bank Transaction ID B1 appears 2 times"}]))
        status = matrix.set_index("Control")["Status"]
        assert status["Duplicate Transaction Check"] == "WARNING"
        assert status["Data Quality Control"] == "WARNING"
        assert overall_control_status(matrix) == "Passed with Warnings"

    def test_sample_controls_tie_per_currency(self, reconciled):
        model = build_report_model(reconciled, load_config())
        amounts = model.controls[model.controls["_unit"] == "amount"]
        assert set(amounts["Currency"]) == {"USD", "CAD", "EUR"}
        assert (amounts["Variance"] == 0).all() and (amounts["Status"] == "PASS").all()


# ---------------------------------------------------------------------------
# AR aging
# ---------------------------------------------------------------------------


class TestAging:
    AS_OF = date(2026, 6, 30)

    def _detail(self, rows, config):
        return aging_detail(pd.DataFrame(rows), self.AS_OF, config)

    @pytest.mark.parametrize("due, bucket", [
        ("2026-07-15", "Current / Not Due"),
        ("2026-06-30", "Current / Not Due"),
        ("2026-06-29", "1-30 Days Past Due"),
        ("2026-05-31", "1-30 Days Past Due"),
        ("2026-05-30", "31-60 Days Past Due"),
        ("2026-05-01", "31-60 Days Past Due"),
        ("2026-04-30", "61-90 Days Past Due"),
        ("2026-04-01", "61-90 Days Past Due"),
        ("2026-03-31", "91-120 Days Past Due"),
        ("2026-03-02", "91-120 Days Past Due"),
        ("2026-03-01", "Over 120 Days Past Due"),
    ])
    def test_bucket_boundaries(self, config, due, bucket):
        detail = self._detail([ar_row("I", 100, due=due)], config)
        assert detail.loc[0, "Aging Bucket"] == bucket

    def test_fully_paid_excluded_and_partial_ages_remainder(self, config):
        detail = self._detail([ar_row("PAID", 100, applied=100), ar_row("PART", 100, applied=40)], config)
        assert list(detail["Invoice Number"]) == ["PART"]
        assert detail.loc[0, "remaining"] == 6000

    def test_approved_adjustment_reduces_the_balance(self, config):
        detail = self._detail([ar_row("ADJ", 100, applied=90, **{"Approved Adjustment Amount": 10})], config)
        assert detail.empty

    def test_missing_due_date_is_unknown_not_assumed(self, config):
        detail = self._detail([ar_row("NODUE", 100, due=None)], config)
        assert detail.loc[0, "Aging Bucket"] == UNKNOWN_BUCKET
        summary = aging_summary(detail, config)
        assert summary.loc[0, UNKNOWN_BUCKET] == 10000 and summary.loc[0, "Total Overdue AR"] == 0

    def test_currencies_are_aged_separately(self, config):
        detail = self._detail([ar_row("U", 100, due="2026-01-01"), ar_row("C", 300, currency="CAD")], config)
        summary = aging_summary(detail, config).set_index("Currency")
        assert summary.loc["USD", "AR Over 90 Days"] == 10000
        assert summary.loc["CAD", "Total Outstanding AR"] == 30000
        assert summary.loc["CAD", "AR Over 90 Days"] == 0

    def test_as_of_date_is_explicit(self):
        bank = pd.DataFrame([bank_row("A"), {**bank_row("B"), "Posting Date": "2026-06-26"}])
        as_of, basis = resolve_as_of(bank, load_config())
        assert as_of == date(2026, 6, 26) and "Posting Date" in basis
        as_of, basis = resolve_as_of(bank, load_config(as_of="2026-06-30"))
        assert as_of == date(2026, 6, 30) and basis.startswith("Configured")

    def test_customer_risk_rules_are_explainable(self, config):
        detail = self._detail([
            ar_row("OLD", 6000, due="2026-01-01", customer="Old Co"),
            ar_row("NEW", 100, due="2026-07-30", customer="New Co"),
        ], config)
        risk = customer_risk(detail, pd.DataFrame(), config).set_index("Customer Name")
        assert risk.loc["Old Co", "Risk Level"] == "High" and "90 days" in risk.loc["Old Co", "Risk Rule"]
        assert risk.loc["New Co", "Risk Level"] == "Low"


# ---------------------------------------------------------------------------
# Workbook
# ---------------------------------------------------------------------------


class TestWorkbook:
    def test_required_sheets_and_order(self, result_path):
        workbook = load_workbook(result_path)
        assert workbook.sheetnames[0] == DASHBOARD
        assert set(REPORT_SHEETS) <= set(workbook.sheetnames)
        assert all(len(name) <= 31 for name in workbook.sheetnames)

    def test_required_columns(self, result_path):
        queue = pd.read_excel(result_path, sheet_name="Action & Review Queue", header=HEADER)
        assert list(queue.columns) == QUEUE_COLUMNS
        priorities = queue["Priority"].map({"High": 0, "Medium": 1, "Low": 2})
        assert priorities.is_monotonic_increasing

    def test_kpis_match_the_model_and_are_numbers(self, result_path, reconciled):
        model = build_report_model(reconciled, load_config())
        ws = load_workbook(result_path)[DASHBOARD]
        cards = {ws.cell(row=r, column=c).value: ws.cell(row=r + 1, column=c)
                 for r in (11, 15) for c in (2, 4, 6, 8)}
        assert cards["Total Bank Receipts"].value == model.metrics.total_receipts
        assert cards["Matched Bank Receipts"].value == model.metrics.matched_receipts
        assert cards["Pending Review Receipts"].value == model.metrics.pending_review
        assert cards["Auto-Match Rate"].value == pytest.approx(model.metrics.auto_match_rate)
        assert cards["Total Bank Receipts"].number_format == "#,##0"
        assert cards["Auto-Match Rate"].number_format == "0.0%"
        # Multi-currency amounts are listed per currency, never totalled.
        applied = cards["Cash Applied to AR"].value
        assert "USD" in applied and "CAD" in applied and "\n" in applied

    def test_charts_reference_report_cells(self, result_path):
        workbook = load_workbook(result_path)
        assert len(workbook[DASHBOARD]._charts) >= 2
        assert len(workbook["Exception Analysis"]._charts) >= 1
        assert len(workbook["AR Aging & Customer Risk"]._charts) >= 1

    def test_formats_and_layout(self, result_path):
        workbook = load_workbook(result_path)
        dashboard = workbook[DASHBOARD]
        assert dashboard.sheet_view.showGridLines is False
        for ws in workbook.worksheets:
            for row in ws.iter_rows(max_row=min(ws.max_row, 60)):
                for cell in row:
                    assert "$" not in (cell.number_format or ""), (ws.title, cell.coordinate)
        queue = workbook["Action & Review Queue"]
        assert queue.freeze_panes and queue.auto_filter.ref

    def test_workbook_reopens_cleanly(self, result_path):
        with zipfile.ZipFile(result_path) as archive:
            assert archive.testzip() is None
        excel = pd.ExcelFile(result_path)
        for name in excel.sheet_names:
            pd.read_excel(excel, name)

    def test_empty_dataset(self, tmp_path):
        path = tmp_path / "empty.xlsx"
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(columns=list(bank_row("X").keys())).to_excel(
                writer, sheet_name="Processed Bank Receipts", index=False)
            pd.DataFrame(columns=list(ar_row("I", 1).keys())).to_excel(
                writer, sheet_name="Reconciled Open AR", index=False)
        model = write_enterprise_report(path)
        assert model.metrics.total_receipts == 0 and model.processing_status == "No Data"
        workbook = load_workbook(path)
        assert workbook.sheetnames[0] == DASHBOARD

    def test_missing_fields_are_handled(self, config):
        bank = pd.DataFrame([{"Bank Transaction ID": "X", "Currency": "USD", "Gross Receipt Amount": 10}])
        ar = pd.DataFrame([{"Invoice Number": "I", "Currency": "USD", "Open Amount": 10}])
        model = build_report_model({"Processed Bank Receipts": bank, "Reconciled Open AR": ar}, config)
        assert model.metrics.match_counts["Unmatched"] == 1
        assert model.aging_detail.loc[0, "Aging Bucket"] == UNKNOWN_BUCKET

    def test_large_dataset(self, config):
        rows = [bank_row(f"B{i}", currency=("USD", "CAD", "EUR")[i % 3], gross=100 + i % 50,
                         applied=(100 + i % 50) if i % 4 else 0, bank_status="Reconciled" if i % 4 else "Pending Review")
                for i in range(20000)]
        ar = pd.DataFrame([ar_row(f"I{i}", 500, applied=250 if i % 2 else 0, due="2026-03-01")
                           for i in range(5000)])
        start = time.perf_counter()
        model = build_report_model({"Processed Bank Receipts": pd.DataFrame(rows), "Reconciled Open AR": ar}, config)
        assert time.perf_counter() - start < 120
        assert model.metrics.total_receipts == 20000
        assert sum(model.metrics.match_counts.values()) == 20000

    def test_formula_injection_is_neutralized(self, result_path, tmp_path):
        import shutil

        path = tmp_path / "inject.xlsx"
        shutil.copy(result_path, path)
        bank = pd.read_excel(path, sheet_name="Processed Bank Receipts")
        bank.loc[0, "Memo / Remittance Detail"] = '=HYPERLINK("http://example.invalid","click")'
        with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            bank.to_excel(writer, sheet_name="Processed Bank Receipts", index=False)
        write_enterprise_report(path)
        workbook = load_workbook(path)
        for ws in workbook.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    assert cell.data_type != "f", (ws.title, cell.coordinate)
        memo_col = [c.value for c in workbook["Processed Bank Receipts"][1]].index("Memo / Remittance Detail") + 1
        assert workbook["Processed Bank Receipts"].cell(row=2, column=memo_col).value.startswith("=HYPERLINK")

    def test_neutralize_counts_cells(self):
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active["A1"] = "=1+1"
        assert neutralize_formulas(workbook) == 1
        assert workbook.active["A1"].data_type == "s"


def test_report_amounts_never_mix_currencies(reconciled):
    """Every amount column in the report tables sits next to a currency."""
    model = build_report_model(reconciled, load_config())
    for frame in (model.metrics.cash_by_currency, model.exception_summary, model.aging_summary,
                  model.customer_risk, model.attention, model.queue, model.cash_results):
        if not frame.empty:
            assert "Currency" in frame.columns


def test_management_attention_is_traceable(reconciled):
    model = build_report_model(reconciled, load_config())
    assert not model.attention.empty
    bank_ids = set(reconciled["Processed Bank Receipts"]["Bank Transaction ID"].astype(str))
    invoices = set(reconciled["Reconciled Open AR"]["Invoice Number"].astype(str))
    for refs in model.attention["References"]:
        for ref in str(refs).split(","):
            token = ref.strip().split(" ")[0]
            if token.startswith(("BTRX-", "INV-")):
                assert token in bank_ids | invoices
