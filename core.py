"""
Backward-compatible import for the shared cell primitives.

The primitives now live in
:mod:`ar_reconciliation.core.primitives`.  This stub keeps
``from core import money_to_cents`` working for any script that was written
against the previous layout.

New code should use::

    from ar_reconciliation.core.primitives import money_to_cents
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.core.primitives import *  # noqa: F401,F403,E402
from ar_reconciliation.core.primitives import (  # noqa: F401,E402
    MoneyParseError,
    cents_to_money,
    format_money,
    is_blank,
    money_to_cents,
    normalize_invoice,
    normalize_key,
    normalize_text,
)
