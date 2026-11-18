"""
Backward-compatible entry point for the AI exception agent.

The agent now lives in
:mod:`ar_reconciliation.agent.exception_agent`.  This stub keeps
``python ai_exception_agent.py`` working for anyone who still uses the old
command, and it keeps ``import ai_exception_agent`` working for any script
that was written against the previous layout.

New code should use::

    python scripts/run_agent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.agent.exception_agent import *  # noqa: F401,F403,E402
from ar_reconciliation.agent.exception_agent import main  # noqa: F401,E402


if __name__ == "__main__":
    sys.exit(main())
