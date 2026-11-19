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

import os
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
    "ENV_FILE",
    "load_env",
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


# --- Environment ----------------------------------------------------------

#: The local environment file.  It holds credentials and local overrides
#: and is never committed - ``.gitignore`` excludes it.
ENV_FILE = PROJECT_ROOT / ".env"


def load_env(env_file: Path | None = None) -> dict[str, str]:
    """
    Load ``.env`` into the process environment.

    The file is optional: the project runs without it, and the rules backend
    never needs it.  A key already present in the environment wins, so
    ``OPENAI_API_KEY=... python scripts/run_agent.py`` still overrides the
    file.

    ``python-dotenv`` is used when it is installed.  When it is not, a small
    built-in parser handles the common ``KEY=value`` form, so the project
    does not force a dependency on a machine that only runs the
    deterministic engine.

    Returns the values that were loaded.
    """
    target = env_file if env_file is not None else ENV_FILE
    if not target.exists():
        return {}

    try:
        from dotenv import dotenv_values

        values = {k: v for k, v in dotenv_values(target).items() if v is not None}
    except ImportError:
        values = _parse_env_file(target)

    loaded: dict[str, str] = {}
    for key, value in values.items():
        if key in os.environ:
            # The real environment wins: an explicit export is a deliberate
            # override of the file.
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Read a ``KEY=value`` file without python-dotenv.

    Supports ``#`` comments, blank lines, an optional ``export`` prefix and
    values wrapped in matching single or double quotes.
    """
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values
