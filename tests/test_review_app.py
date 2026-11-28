"""
Headless smoke tests of the multipage review UI (app/review_app.py).

Streamlit is an optional extra (decision E4), so the tests are skipped when it
is not installed.  They drive the real pages through Streamlit's AppTest:

* the two-input workflow on the synthetic sample data - run, then render
  every page without an exception;
* the enriched demo - run with the structured inputs, see the before/after
  comparison, then approve and apply the authorized deduction (G1) and
  confirm the unauthorized one cannot be approved.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "review_app.py"
PAGES = ["overview", "enrichment", "data_import", "reconciliation", "workbench", "investigation", "evidence",
         "controls", "reports", "audit", "settings"]


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def start(monkeypatch, source: str) -> AppTest:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    at = AppTest.from_file(str(APP), default_timeout=240)
    at.run()
    assert not at.exception
    at.session_state["reviewer"] = "A. Analyst"
    at.switch_page("views/data_import.py").run()
    at.radio(key="data_source").set_value(source).run()
    assert not at.exception
    button(at, "Run Reconciliation").click().run()
    assert not at.exception, at.exception
    return at


def test_two_input_workflow_renders_every_page(monkeypatch):
    at = start(monkeypatch, "Use Synthetic Sample Data")
    workspace = Path(at.session_state["workspace"])
    sheets = pd.ExcelFile(next(workspace.glob("*.xlsx"))).sheet_names
    assert "Enrichment Comparison" not in sheets and "Proposed Deductions" not in sheets
    for page in PAGES:
        at.switch_page(f"views/{page}.py").run()
        assert not at.exception, (page, at.exception)


def test_enriched_demo_comparison_and_deduction_review(monkeypatch):
    at = start(monkeypatch, "Use Synthetic Enriched Demo")
    at.switch_page("views/enrichment.py").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "100.0% (11/11)" in text and "45.5% (5/11)" in text

    at.session_state["selected_txn"] = "BTRX-000194"
    at.switch_page("views/investigation.py").run()
    assert not at.exception
    assert next(b for b in at.button if b.key == "ded_ok_DED-BTRX-000194-INV-900194").disabled  # Unauthorized

    at.session_state["selected_txn"] = "BTRX-000158"
    at.switch_page("views/investigation.py").run()
    next(b for b in at.button if b.key == "ded_ok_DED-BTRX-000158-INV-900175").click().run()
    assert not at.exception
    next(b for b in at.button if b.key == "ded_apply_DED-BTRX-000158-INV-900175").click().run()
    assert not at.exception

    result = next(p for p in Path(at.session_state["workspace"]).glob("*.xlsx") if "baseline" not in p.name)
    deductions = pd.read_excel(result, sheet_name="Proposed Deductions")
    row = deductions.set_index("Deduction ID").loc["DED-BTRX-000158-INV-900175"]
    assert row["Decision"] == "APPROVE" and row["Applied"] == "Yes"
    ar = pd.read_excel(result, sheet_name="Reconciled Open AR").set_index("Invoice Number")
    assert ar.loc["INV-900175", "Approved Adjustment Amount"] == pytest.approx(251.25)
    controls = pd.read_excel(result, sheet_name="Summary")
    assert "FAILED" not in controls.astype(str).to_string()
