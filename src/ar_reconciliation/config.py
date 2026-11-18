"""
Project-wide paths and settings.

Every path in the project is derived from the repository root, so the code
works the same whether it is run from the repository root, from ``scripts/``,
or from an installed package.

The layout is::

    data/input/    source workbooks read by the engine (never written to)
    data/output/   workbooks produced by a run
    data/sample/   the sample dataset that ships with the repository

Source workbooks are read-only by contract.  The engine never writes to
``data/input``.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "PROJECT_ROOT",
    "SRC_DIR",
    "DATA_DIR",
    "INPUT_DIR",
    "OUTPUT_DIR",
    "SAMPLE_DIR",
    "DOCS_DIR",
    "BASELINE_FILE",
    "BANK_FILE",
    "AR_FILE",
    "RESULT_FILE",
    "APPROVAL_LEDGER_FILE",
    "BANK_SHEET",
    "AR_SHEET",
    "REVIEW_SHEET",
    "REVIEW_LOG_SHEET",
    "resolve_output_path",
]

# The repository root is the parent of ``src/``.  This file lives at
# ``src/ar_reconciliation/config.py``, so the root is two levels up.
# When the package is installed into site-packages this fallback keeps the
# import working, but the data directories then have to be supplied
# explicitly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

SRC_DIR = PROJECT_ROOT / "src"
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output"
SAMPLE_DIR = DATA_DIR / "sample"
DOCS_DIR = PROJECT_ROOT / "docs"

BASELINE_FILE = DATA_DIR / "baseline.json"

# --- Workbook names -------------------------------------------------------

BANK_WORKBOOK_NAME = "Bank_Cash_Receipts.xlsx"
AR_WORKBOOK_NAME = "Open_AR_Detail.xlsx"
RESULT_WORKBOOK_NAME = "Open_AR_Detail_Reconciled_Result.xlsx"
APPROVAL_LEDGER_NAME = "AI_Exception_Approvals.xlsx"

# --- Sheet names ----------------------------------------------------------

BANK_SHEET = "Bank Receipts"
AR_SHEET = "Open AR"
REVIEW_SHEET = "AI Exception Review"
REVIEW_LOG_SHEET = "AI Exception Log"


def _resolve(*candidates: Path) -> Path:
    """
    Return the first candidate that exists, else the first one.

    The sample dataset is the fallback so that a fresh clone runs out of the
    box, while a user who drops their own workbooks into ``data/input/``
    gets those instead.
    """
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


#: Bank receipts.  Read-only source data.
BANK_FILE = _resolve(INPUT_DIR / BANK_WORKBOOK_NAME, SAMPLE_DIR / BANK_WORKBOOK_NAME)

#: Open AR detail.  Read-only source data.
AR_FILE = _resolve(INPUT_DIR / AR_WORKBOOK_NAME, SAMPLE_DIR / AR_WORKBOOK_NAME)

#: Where a run writes the reconciled workbook.
RESULT_FILE = OUTPUT_DIR / RESULT_WORKBOOK_NAME

#: The durable review / approval ledger.
APPROVAL_LEDGER_FILE = OUTPUT_DIR / APPROVAL_LEDGER_NAME


def resolve_output_path(filename: str = RESULT_WORKBOOK_NAME) -> Path:
    """
    Resolve an output workbook path inside ``data/output/``.

    Output files are never written next to the source workbooks, so a run
    can never overwrite the data it was given.
    """
    return OUTPUT_DIR / filename
