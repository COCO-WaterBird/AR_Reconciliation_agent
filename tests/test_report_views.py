"""
The five report sheets at the front of the result workbook (decision D6).

They are views over the detail sheets, so the tests check that they come
first, that every receipt is in exactly one of Matched / Unmatched, and that
their amounts agree with the detail sheets to the cent.
"""

from __future__ import annotations

import shutil

import pandas as pd
from openpyxl import load_workbook

from ar_reconciliation.core.primitives import money_to_cents
from ar_reconciliation.io.report_views import APPENDIX_ORDER, REPORT_SHEETS


def _cents(series: pd.Series) -> int:
    return int(series.astype(object).fillna("").apply(money_to_cents).sum())


def test_report_sheets_come_first_and_summary_opens(result_path):
    workbook = load_workbook(result_path, read_only=False)
    assert workbook.sheetnames[: len(REPORT_SHEETS)] == list(REPORT_SHEETS)
    appendix = [n for n in workbook.sheetnames if n in APPENDIX_ORDER]
    assert appendix == [n for n in APPENDIX_ORDER if n in workbook.sheetnames]
    assert workbook.active.title == "Summary"


def test_every_receipt_is_matched_or_unmatched_once(reconciled):
    bank = reconciled["Processed Bank Receipts"]
    matched = reconciled["Matched"]
    unmatched = reconciled["Unmatched"]
    ids = list(matched["Bank Transaction ID"]) + list(unmatched["Bank Transaction ID"])
    assert sorted(ids) == sorted(bank["Bank Transaction ID"])


def test_view_amounts_agree_with_the_detail_sheets(reconciled):
    bank = reconciled["Processed Bank Receipts"]
    assert _cents(reconciled["Matched"]["Applied to AR"]) == _cents(bank["AR Applied Amount Total"])
    assert _cents(reconciled["Unmatched"]["Held For Review Amount"]) == _cents(bank["Held For Review Amount"])


def test_unmatched_carries_the_engine_reason(reconciled):
    unmatched = reconciled["Unmatched"]
    assert unmatched["Reason"].astype(object).fillna("").str.len().gt(0).all()


def test_ai_views_after_analysis_and_approval(result_path, tmp_path):
    from ar_reconciliation.agent.workflow import apply_decisions, record_decision, run_analysis
    from ar_reconciliation.evidence import DocumentStore
    from ar_reconciliation.evidence.samples import SAMPLE_DOCUMENTS

    workbook = tmp_path / "result.xlsx"
    shutil.copy(result_path, workbook)
    store = DocumentStore(tmp_path / "ev")
    for name, (data, txns, _scenario) in SAMPLE_DOCUMENTS.items():
        store.add_bytes(data, name, txns)
    run_analysis(workbook, evidence_dir=tmp_path / "ev")

    suggested = pd.read_excel(workbook, sheet_name="AI Suggested")
    assert "BTRX-000155" in set(suggested["Bank Transaction ID"])
    assert (suggested["Record decisions in"] == "AI Exception Review").all()

    record_decision(workbook, "BTRX-000155", "APPROVE", "A. Analyst")
    apply_decisions(workbook, evidence_dir=tmp_path / "ev")

    excel = pd.ExcelFile(workbook)
    assert excel.sheet_names[:5] == list(REPORT_SHEETS)
    matched = pd.read_excel(excel, "Matched").set_index("Bank Transaction ID")
    assert matched.loc["BTRX-000155", "Settled By"] == "AI resolution approved by reviewer"
    assert "BTRX-000155" not in set(pd.read_excel(excel, "Unmatched")["Bank Transaction ID"])
    assert "BTRX-000155" not in set(pd.read_excel(excel, "AI Suggested")["Bank Transaction ID"])
    evidence = pd.read_excel(excel, "Evidence")
    own = evidence.loc[evidence["Bank Transaction ID"] == "BTRX-000155", "Evidence Type"]
    assert {"Matching rule", "Document fact", "Reviewer decision"} <= set(own)
