"""
A headless smoke test of the review UI (app/review_app.py).

Streamlit is an optional extra (decision E4), so the test is skipped when it
is not installed.  It drives the whole acceptance path on the synthetic
sample data: reconcile, pick an exception, attach the sample documents,
analyse, approve, apply, and check the controls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "review_app.py"


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def test_review_app_end_to_end(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception

    button(at, "Run reconciliation").click().run()
    assert not at.exception
    assert any("in Needs Review" in s.value for s in at.success)

    at.selectbox(key="txn").set_value("BTRX-000155").run()
    button(at, "Load the synthetic sample documents").click().run()
    button(at, "Analyse exceptions").click().run()
    assert not at.exception
    markdown = " ".join(m.value for m in at.markdown)
    assert "INV-900172: 8,850.00" in markdown
    assert "Customer Deduction 1,000.00" in markdown

    at.text_input(key="reviewer").set_value("A. Analyst").run()
    button(at, "Record decision").click().run()
    assert not at.exception

    button(at, "Apply approved resolutions").click().run()
    assert not at.exception
    assert any("Applied 1" in s.value for s in at.success)
    workspace = at.session_state["workspace"]
    assert (Path(workspace) / "Reconciliation_Result.xlsx").exists()
