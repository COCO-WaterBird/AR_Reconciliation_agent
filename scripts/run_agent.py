#!/usr/bin/env python
"""
Run the AI Exception Resolution Agent.

Reads the reconciled workbook, analyses the receipts the deterministic
engine could not reconcile, and writes the recommendations into the
``AI Exception Review`` sheet.

    python scripts/run_agent.py                       # offline rules backend
    python scripts/run_agent.py --limit 5             # smoke test
    python scripts/run_agent.py --backend llm --model gpt-4o-mini
    python scripts/run_agent.py --apply-approved      # apply human approvals
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow the script to be run from a checkout without installing the package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.agent import exception_agent as A  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    return A.main(argv)


if __name__ == "__main__":
    sys.exit(main())
