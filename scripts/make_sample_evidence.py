#!/usr/bin/env python
"""
Regenerate the synthetic supporting documents in data/sample/evidence/.

Every document is invented; the invoice numbers and amounts refer to the
synthetic sample workbooks so the demo resolves real sample exceptions.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.config import SAMPLE_EVIDENCE_DIR  # noqa: E402
from ar_reconciliation.evidence.samples import write_sample_documents  # noqa: E402

if __name__ == "__main__":
    for path in write_sample_documents(SAMPLE_EVIDENCE_DIR):
        print(path.relative_to(SAMPLE_EVIDENCE_DIR.parents[2]))
