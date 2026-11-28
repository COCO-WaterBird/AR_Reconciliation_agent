"""
The report sheets at the front of the result workbook.

Originally written for the D6 five-sheet layout; updated for the enterprise
layout that replaced it (docs/reporting_redesign_plan.md, section 5).  The
invariants are unchanged: the report opens first, every receipt is in
exactly one match status, and the report amounts tie to the detail sheets to
the cent.
"""

from __future__ import annotations

import shutil

import pandas as pd
from openpyxl import load_workbook

from ar_reconciliation.core.primitives import money_to_cents
from ar_reconciliation.reporting.excel_exporter import DASHBOARD, LEGACY_VIEW_SHEETS, REPORT_SHEETS

HEADER = 3  # detail report sheets: title, subtitle, navigation, then the header row


def _cents(series: pd.Series) -> int:
    return int(series.astype(object).fillna("").apply(money_to_cents).sum())


def test_report_sheets_come_first_and_dashboard_opens(result_path):
    workbook = load_workbook(result_path)
    assert workbook.sheetnames[0] == DASHBOARD
    assert workbook.active.title == DASHBOARD
    assert set(REPORT_SHEETS) <= set(workbook.sheetnames)
    assert not set(LEGACY_VIEW_SHEETS) & set(workbook.sheetnames)
    # The system-of-record sheets are still there, with their names.
    for name in ("Reconciled Open AR", "Processed Bank Receipts", "Unmatched Bank Receipts", "Summary"):
        assert name in workbook.sheetnames


def test_every_receipt_has_exactly_one_match_status(result_path, reconciled):
    bank = reconciled["Processed Bank Receipts"]
    results = pd.read_excel(result_path, sheet_name="Cash Application Results", header=HEADER)
    assert sorted(results["Bank Receipt ID"]) == sorted(bank["Bank Transaction ID"])
    assert results["Match Status"].notna().all()


def test_report_amounts_agree_with_the_detail_sheets(result_path, reconciled):
    bank = reconciled["Processed Bank Receipts"]
    results = pd.read_excel(result_path, sheet_name="Cash Application Results", header=HEADER)
    assert _cents(results["Applied to AR"]) == _cents(bank["AR Applied Amount Total"])
    assert _cents(results["Held for Review"]) == _cents(bank["Held For Review Amount"])


def test_every_open_item_carries_a_recommended_action(result_path):
    queue = pd.read_excel(result_path, sheet_name="Action & Review Queue", header=HEADER)
    assert len(queue) > 0
    assert queue["Recommended Action"].astype(object).fillna("").str.len().gt(0).all()


def test_report_after_analysis_and_approval(result_path, tmp_path):
    from ar_reconciliation.agent.workflow import apply_decisions, record_decision, run_analysis
    from ar_reconciliation.evidence import DocumentStore
    from ar_reconciliation.evidence.samples import SAMPLE_DOCUMENTS

    workbook = tmp_path / "result.xlsx"
    shutil.copy(result_path, workbook)
    store = DocumentStore(tmp_path / "ev")
    for name, (data, txns, _scenario) in SAMPLE_DOCUMENTS.items():
        store.add_bytes(data, name, txns)
    run_analysis(workbook, evidence_dir=tmp_path / "ev")

    queue = pd.read_excel(workbook, sheet_name="Action & Review Queue", header=HEADER)
    assert "BTRX-000155" in set(queue["Bank Receipt ID"])

    record_decision(workbook, "BTRX-000155", "APPROVE", "A. Analyst")
    apply_decisions(workbook, evidence_dir=tmp_path / "ev")

    excel = pd.ExcelFile(workbook)
    assert excel.sheet_names[0] == DASHBOARD
    results = pd.read_excel(excel, "Cash Application Results", header=HEADER).set_index("Bank Receipt ID")
    assert results.loc["BTRX-000155", "Settled By"] == "AI resolution approved by reviewer"
    assert results.loc["BTRX-000155", "Review Status"] == "Approved"
    queue = pd.read_excel(excel, "Action & Review Queue", header=HEADER)
    assert "BTRX-000155" not in set(queue["Bank Receipt ID"])
    evidence = pd.read_excel(excel, "AI Decision & Evidence", header=HEADER)
    own = evidence.loc[evidence["Bank Receipt ID"] == "BTRX-000155", "Evidence Type"]
    assert {"Matching Rule", "AI Recommendation", "Document Fact", "Reviewer Decision"} <= set(own)
