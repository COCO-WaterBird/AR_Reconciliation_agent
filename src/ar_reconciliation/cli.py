"""
Console entry points.

These are thin wrappers so that the package can be installed and run with a
command, while the scripts in ``scripts/`` remain the documented way to run
the project from a checkout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ar_reconciliation.config import AR_FILE, BANK_FILE, RESULT_FILE

__all__ = ["reconcile_main", "agent_main"]


def reconcile_main(argv: list[str] | None = None) -> int:
    """Run the deterministic reconciliation engine."""
    parser = argparse.ArgumentParser(
        prog="ar-reconcile",
        description="Reconcile bank cash receipts against open AR.",
    )
    parser.add_argument("--bank", type=Path, default=BANK_FILE)
    parser.add_argument("--ar", type=Path, default=AR_FILE)
    parser.add_argument("--output", type=Path, default=RESULT_FILE)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from ar_reconciliation.services import run_reconciliation

    result = run_reconciliation(
        bank_path=args.bank,
        ar_path=args.ar,
        output_path=args.output,
        verbose=not args.quiet,
    )
    if not args.quiet:
        print()
        print(
            f"Reconciled AR applied amount  : "
            f"{result.reconciled_ar_total_cents / 100:,.2f}"
        )
        print(
            f"Reconciled bank applied amount: "
            f"{result.reconciled_bank_total_cents / 100:,.2f}"
        )
        print(f"Control difference            : {result.control_difference:,.2f}")
    return 0


def agent_main(argv: list[str] | None = None) -> int:
    """Run the AI Exception Resolution Agent."""
    from ar_reconciliation.agent import exception_agent

    return exception_agent.main(argv)


if __name__ == "__main__":
    sys.exit(reconcile_main())
