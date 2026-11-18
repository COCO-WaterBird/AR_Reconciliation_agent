"""
Shared pytest fixtures.

The tests run against the sample data that ships with the repository, so they
exercise the real matching rules rather than a simplified model of them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
_SRC = BASE_DIR / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.config import AR_FILE, BANK_FILE, RESULT_FILE  # noqa: E402


@pytest.fixture(scope="session")
def bank_df() -> pd.DataFrame:
    """The raw bank receipts, as the engine reads them."""
    return pd.read_excel(BANK_FILE, sheet_name="Bank Receipts")


@pytest.fixture(scope="session")
def ar_df() -> pd.DataFrame:
    """The raw open AR, as the engine reads it."""
    return pd.read_excel(AR_FILE, sheet_name="Open AR")


@pytest.fixture(scope="session")
def result_path(tmp_path_factory) -> Path:
    """
    A freshly reconciled result workbook in a temporary directory.

    The engine is run against the sample data once per test session.  The
    output goes to a temporary path so the tests never touch the workbook in
    the repository.
    """
    from ar_reconciliation.services import run_reconciliation

    out_dir = tmp_path_factory.mktemp("reconciliation")
    out_file = out_dir / "result.xlsx"

    run_reconciliation(
        bank_path=BANK_FILE,
        ar_path=AR_FILE,
        output_path=out_file,
        verbose=False,
    )
    return out_file


@pytest.fixture(scope="session")
def reconciled(result_path: Path) -> dict[str, pd.DataFrame]:
    """Every sheet of the reconciled workbook, keyed by sheet name."""
    excel = pd.ExcelFile(result_path)
    return {name: pd.read_excel(excel, name) for name in excel.sheet_names}


@pytest.fixture(scope="session")
def result_ar(reconciled) -> pd.DataFrame:
    return reconciled["Reconciled Open AR"]


@pytest.fixture(scope="session")
def result_bank(reconciled) -> pd.DataFrame:
    return reconciled["Processed Bank Receipts"]


@pytest.fixture(scope="session")
def result_unmatched(reconciled) -> pd.DataFrame:
    return reconciled["Unmatched Bank Receipts"]


@pytest.fixture(scope="session")
def analysis(result_path, tmp_path_factory):
    """
    The AI agent's analysis of the unmatched receipts.

    Runs the deterministic ``rules`` backend over the real unmatched
    receipts and returns the review and log frames.  A copy of the workbook
    is used so the repository's own result file is never touched.
    """
    import shutil

    from ar_reconciliation.agent import exception_agent as agent

    out_dir = tmp_path_factory.mktemp("agent")
    out_file = out_dir / "result.xlsx"
    shutil.copy(result_path, out_file)

    _ar, _result, review_df, log_df, _unmatched, _backend = agent.analyse_exceptions(
        result_path=out_file, backend_name="rules"
    )
    return review_df, log_df
