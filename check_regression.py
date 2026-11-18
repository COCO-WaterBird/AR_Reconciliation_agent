"""
Backward-compatible entry point for the regression check.

The check now lives in ``scripts/check_regression.py``.  This stub keeps
``python check_regression.py baseline.json`` working for anyone who still
uses the old command.

New code should use::

    python scripts/check_regression.py data/baseline.json
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script(name: str):
    """Load ``scripts/<name>.py`` under its own module name.

    The script cannot simply be imported by name: this file is called
    ``check_regression.py`` too, so it would shadow it.
    """
    path = Path(__file__).resolve().parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_scripts_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_module = _load_script("check_regression")

globals().update(
    {k: v for k, v in vars(_module).items() if not k.startswith("__")}
)

main = _module.main

if __name__ == "__main__":
    sys.exit(main())
