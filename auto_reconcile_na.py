"""
Backward-compatible entry point for the reconciliation engine.

The engine now lives in the :mod:`ar_reconciliation.services` package.  This
stub keeps ``python auto_reconcile_na.py`` working for anyone who still uses
the old command, and it keeps ``import auto_reconcile_na`` working for any
script that was written against the previous layout.

New code should use::

    python scripts/run_reconciliation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.core.schema import *  # noqa: F401,F403,E402
from ar_reconciliation.matching import *  # noqa: F401,F403,E402
from ar_reconciliation.rules.policies import (  # noqa: F401,E402
    AMOUNT_TOLERANCE_CENTS,
    ENGINE_REVIEW_ONLY_SCENARIOS as REVIEW_ONLY_SCENARIOS,
    MAX_CANDIDATES_PER_BANK_RECEIPT,
    MAX_PARTIAL_RESIDUAL_RATIO,
    MIN_PARTIAL_RESIDUAL_CENTS,
    NAME_FALLBACK_SCENARIOS,
    PARTIAL_ALLOWANCE_SCENARIOS,
    ROUNDING_TOLERANCE_CENTS,
    SMALL_RESIDUAL_WRITE_OFF_CENTS,
    UNRESTRICTED_PARTIAL_SCENARIOS,
)
from ar_reconciliation.services.reconciliation import (  # noqa: F401,E402
    ReconciliationService,
    run_reconciliation,
)


def main(
    bank_path: Path | None = None,
    ar_path: Path | None = None,
    output_path: Path | None = None,
) -> int:
    """Run the reconciliation engine with the default paths."""
    run_reconciliation(
        bank_path=bank_path,
        ar_path=ar_path,
        output_path=output_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
