#!/usr/bin/env python
"""
Manage supporting documents for receipts in Needs Review.

    python scripts/manage_evidence.py load-samples           # synthetic demo set
    python scripts/manage_evidence.py add remittance.pdf --txn BTRX-000178
    python scripts/manage_evidence.py list
    python scripts/manage_evidence.py extract
    python scripts/manage_evidence.py decide BTRX-000178 APPROVE --reviewer "A. Analyst"

Documents are stored, unchanged and read-only, in data/evidence/ (gitignored).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.cli import evidence_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(evidence_main())
