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
    enrich = parser.add_argument_group("optional structured inputs (CSV or Excel)")
    enrich.add_argument("--customer-master", type=Path, help="Customer Master")
    enrich.add_argument("--contracts", type=Path, help="Contract and Payment Schedule")
    enrich.add_argument("--remittance", type=Path, help="Remittance Advice")
    enrich.add_argument("--enriched-demo", action="store_true",
                        help="Use the synthetic enriched demo pack in data/sample/enriched/")
    enrich.add_argument("--compare", action="store_true",
                        help="Also run the baseline (bank + AR only) and write the Enrichment Comparison")
    enrich.add_argument("--expected", type=Path, help="Ground truth for matching accuracy (expected_matches.csv)")
    args = parser.parse_args(argv)

    inputs = None
    if args.enriched_demo:
        from ar_reconciliation.config import SAMPLE_DIR

        demo = SAMPLE_DIR / "enriched"
        args.customer_master = args.customer_master or demo / "customer_master.csv"
        args.contracts = args.contracts or demo / "contract_schedule.csv"
        args.remittance = args.remittance or demo / "remittance_advice.csv"
        args.expected = args.expected or demo / "expected_matches.csv"
    if args.customer_master or args.contracts or args.remittance:
        from ar_reconciliation.enrichment import load_inputs

        inputs = load_inputs(args.customer_master, args.contracts, args.remittance)
        for issue in inputs.issues:
            print(f"Data quality: {issue['Source']} {issue['File']} row {issue['Row']}: {issue['Issue']}")

    if inputs is not None and args.compare:
        from ar_reconciliation.enrichment.comparison import run_with_comparison

        result, comparison = run_with_comparison(args.bank, args.ar, args.output, inputs, args.expected,
                                                 verbose=not args.quiet)
        if not args.quiet:
            print("\nEnrichment comparison (baseline vs enriched):")
            for _, row in comparison.iterrows():
                print(f"  {row['Metric']:<50} {row['Baseline (Bank + AR)']!s:>16}  ->  {row['Enriched']!s}")
    else:
        result = run_reconciliation(
            bank_path=args.bank,
            ar_path=args.ar,
            output_path=args.output,
            verbose=not args.quiet,
            enrichment_inputs=inputs,
        )

    if not args.quiet:
        from ar_reconciliation.cli import print_currency_totals

        print_currency_totals(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
