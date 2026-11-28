#!/usr/bin/env python
"""
Regenerate the synthetic enriched demo pack in data/sample/enriched/.

Customer Master, Contract Schedule, Remittance Advice, the ground-truth file
and a synthetic mailbox - all invented, all referring to the synthetic sample
workbooks in data/input/.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.config import AR_FILE, BANK_FILE, SAMPLE_DIR  # noqa: E402
from ar_reconciliation.enrichment.demo import write_enriched_demo  # noqa: E402

if __name__ == "__main__":
    for path in write_enriched_demo(SAMPLE_DIR / "enriched", AR_FILE, BANK_FILE):
        print(path.relative_to(SAMPLE_DIR.parents[1]))
