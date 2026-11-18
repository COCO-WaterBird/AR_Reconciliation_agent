"""
Shared primitives for the reconciliation engine and the AI exception agent.

Both the deterministic engine and the AI agent need to turn spreadsheet cells
into comparable values: text for fuzzy matching, integer cents for money.
Those conversions used to live in both files as separately written copies of
the same four function names, and the copies had drifted apart:

  * ``normalize_key`` stripped punctuation in the agent but not in the engine.
  * ``money_to_cents`` understood ``(1,234.56)`` and ``USD 500`` in the engine
    but silently returned ``0`` in the agent.

That second difference is the dangerous one.  A silent ``0`` looks like a
legitimate "this invoice is worth nothing" to every downstream check,
including the AI recommendation validator, so a malformed amount would be
confirmed rather than reported.

This module is therefore the single definition of those conversions, with two
rules that hold everywhere:

  1. **Money parsing never guesses.**  A value that cannot be parsed raises
     :class:`MoneyParseError` rather than collapsing to zero.
  2. **Text keys are canonical.**  Every component folds text to the same
     normalized key, so ``"ACME-CORP"``, ``"ACME Corp."`` and ``"acme corp"``
     all compare equal.

Money is handled as integer cents throughout the project.  No float
arithmetic is allowed to touch a monetary value.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import pandas as pd

__all__ = [
    "MoneyParseError",
    "normalize_text",
    "normalize_key",
    "normalize_invoice",
    "money_to_cents",
    "cents_to_money",
    "format_money",
    "is_blank",
]


class MoneyParseError(ValueError):
    """Raised when a cell value cannot be read as a monetary amount."""


# Currency prefixes/suffixes that may appear in a spreadsheet cell.  They are
# stripped before parsing so that "USD 500" and "500 USD" both mean 500.00.
_CURRENCY_TOKENS = ("USD", "CAD", "EUR", "GBP", "MXN")

_WHITESPACE_RE = re.compile(r"\s+")
_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")


def is_blank(value: object) -> bool:
    """True when a cell holds no meaningful value (None, NaN or empty text)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def normalize_text(value: object) -> str:
    """Collapse whitespace and strip.  Never returns None, never raises."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        # pd.isna raises on some exotic types (lists, arrays).  Those are
        # meaningful values for our purposes, so fall through to str().
        pass
    return _WHITESPACE_RE.sub(" ", str(value)).strip()


def normalize_key(value: object) -> str:
    """
    Canonical comparison key for text.

    Lower-cased, whitespace collapsed and every non-alphanumeric character
    removed.  Two strings that differ only in punctuation, spacing or case
    produce the same key, which is what the customer-name and invoice
    fallbacks need.
    """
    return _KEY_STRIP_RE.sub("", normalize_text(value).lower())


def normalize_invoice(value: object) -> str:
    """Canonical comparison key for an invoice number."""
    return normalize_text(value).upper()


def money_to_cents(value: object) -> int:
    """
    Convert a spreadsheet money value to integer cents.

    Accepts the forms that actually appear in an ERP export:

      * a number: ``1234.56``, ``1234``
      * a formatted string: ``"$1,234.56"``, ``"1,234.56"``
      * a currency-tagged string: ``"USD 1,234.56"``, ``"1,234.56 CAD"``
      * an accounting-negative string: ``"(1,234.56)"``

    Rounding is half-up, matching the accounting convention the rest of the
    project uses.

    Raises:
        MoneyParseError: if the value is not blank and cannot be parsed as a
            monetary amount.  A malformed amount must never become a silent
            zero, because every downstream control total would then treat it
            as a real value.
    """
    if is_blank(value):
        return 0

    if isinstance(value, bool):
        # bool is an int subclass; True meaning $1.00 would be a silent bug.
        raise MoneyParseError(f"Cannot parse money value from boolean: {value!r}")

    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        dec = Decimal(str(value))
    else:
        text = normalize_text(value)
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")

        upper = text.upper()
        for token in _CURRENCY_TOKENS:
            upper = upper.replace(token, " ")
        # The currency symbol and the thousands separator are removed, not
        # spaced out: "1,234.56" must become "1234.56", not "1 234.56".
        text = upper.replace("$", "").replace(",", "").strip()

        # The original cell was not blank, so neither is the text left after
        # stripping the currency decoration.  A value like "$" or "USD" on its
        # own is malformed, not zero, and must not pass as a real amount.
        if not text:
            raise MoneyParseError(f"Cannot parse money value: {value!r}")

        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise MoneyParseError(
                f"Cannot parse money value: {value!r}"
            ) from exc

        if negative:
            dec = -dec

    return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_money(cents: int) -> float:
    """Convert integer cents back to a float amount."""
    return float(Decimal(int(cents)) / Decimal("100"))


def format_money(cents: int) -> str:
    """Format integer cents as a human-readable string, e.g. ``"1,234.56"``."""
    return f"{cents_to_money(cents):,.2f}"
