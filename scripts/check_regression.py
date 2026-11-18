"""
Compare the current result workbook against a stored baseline snapshot.

This is a lightweight guard used while refactoring: it answers "did the
numbers move?" in one command, without needing the full pytest suite.

Usage:
    python scripts/check_regression.py baseline.json
    python scripts/check_regression.py baseline.json --write     # store a new baseline
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow the script to be run from a checkout without installing the package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd  # noqa: E402

from ar_reconciliation.config import BASELINE_FILE, RESULT_FILE  # noqa: E402

AR_SHEET = "Reconciled Open AR"
BANK_SHEET = "Processed Bank Receipts"
UNMATCHED_SHEET = "Unmatched Bank Receipts"


def snapshot(result_path: Path = RESULT_FILE) -> dict:
    """Collect the metrics that a reconciliation change must not move."""
    excel = pd.ExcelFile(result_path)

    ar = pd.read_excel(excel, AR_SHEET)
    bank = pd.read_excel(excel, BANK_SHEET)
    unmatched = pd.read_excel(excel, UNMATCHED_SHEET)

    return {
        "row_counts": {
            AR_SHEET: len(ar),
            BANK_SHEET: len(bank),
            UNMATCHED_SHEET: len(unmatched),
        },
        "ar_applied_total": round(float(ar["Applied Amount"].fillna(0).sum()), 2),
        "bank_applied_total": round(
            float(bank["AR Applied Amount Total"].fillna(0).sum()), 2
        ),
        "ar_status": ar["Suggested Recon Status"].value_counts().to_dict(),
        "bank_status": bank["Suggested Bank Status"].value_counts().to_dict(),
        "match_methods": {
            k: int(v) for k, v in ar["Match Method"].fillna("").value_counts().items()
        },
    }


def compare(expected: dict, actual: dict) -> list[str]:
    """Return a list of human-readable differences."""
    problems: list[str] = []

    for key in ("row_counts", "ar_status", "bank_status", "match_methods"):
        exp = expected.get(key, {})
        act = actual.get(key, {})
        if exp != act:
            problems.append(f"{key} changed:\n    expected {exp}\n    actual   {act}")

    for key in ("ar_applied_total", "bank_applied_total"):
        exp = expected.get(key)
        act = actual.get(key)
        if exp is not None and abs(float(exp) - float(act)) > 0.005:
            problems.append(f"{key} changed: expected {exp}, actual {act}")

    return problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    write = "--write" in argv
    argv = [a for a in argv if not a.startswith("--")]

    baseline_path = Path(argv[0]) if argv else BASELINE_FILE
    result_path = Path(argv[1]) if len(argv) > 1 else RESULT_FILE
    actual = snapshot(result_path)

    if write:
        baseline_path.write_text(json.dumps(actual, indent=2, default=str))
        print(f"Wrote baseline to {baseline_path.name}")
        return 0

    if not baseline_path.exists():
        print(f"No baseline found at {baseline_path}. Run with --write to create one.")
        return 1

    expected = json.loads(baseline_path.read_text())
    problems = compare(expected, actual)

    if problems:
        print("REGRESSION DETECTED\n")
        for problem in problems:
            print(f"  * {problem}")
        return 1

    print("No regression: all baseline metrics match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
