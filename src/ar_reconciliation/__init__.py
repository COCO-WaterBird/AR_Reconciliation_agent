"""
AR cash application and bank receipt reconciliation.

The package is layered:

``core``      shared primitives - cell parsing, money, the column vocabulary
``rules``     business policies - thresholds, tolerances, taxonomies
``matching``  the matching algorithms
``agent``     the AI exception agent
``llm``       LLM backends
``services``  the workflows that tie the layers together
``io``        reading and writing workbooks

The layers only depend downwards: a service may use the matching layer, the
matching layer may use the rules and the core, and nothing reaches back up.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
