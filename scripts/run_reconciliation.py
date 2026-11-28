#!/usr/bin/env python
"""
Run the deterministic reconciliation engine.

Reads the two source workbooks from ``data/input/`` and writes the result
workbook to ``data/output/``.  The source workbooks are never modified.

    python scripts/run_reconciliation.py
    python scripts/run_reconciliation.py --output /tmp/scratch.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow the script to be run from a checkout without installing the package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.config import AR_FILE, BANK_FILE, RESULT_FILE  # noqa: E402
from ar_reconciliation.services import run_reconciliation  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile bank cash receipts against open AR.",
    )
    parser.add_argument(
        "--bank",
        type=Path,
        default=BANK_FILE,
        help="Bank receipts workbook (default: %(default)s)",
    )
    parser.add_argument(
        "--ar",
        type=Path,
        default=AR_FILE,
        help="Open AR detail workbook (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=RESULT_FILE,
        help="Where to write the reconciled workbook (default: %(default)s)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print the run summary.",
    )
    args = parser.parse_args(argv)

    result = run_reconciliation(
        bank_path=args.bank,
        ar_path=args.ar,
        output_path=args.output,
        verbose=not args.quiet,
    )

    if not args.quiet:
        from ar_reconciliation.cli import print_currency_totals

        print_currency_totals(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
