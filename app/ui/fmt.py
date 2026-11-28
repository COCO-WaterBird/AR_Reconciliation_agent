"""
Display formatting for financial data.

Formatting only: no value is computed here.  Amounts arrive as integer cents
or decimals from the backend and are shown with their ISO currency code;
missing values are labeled, never shown as zero.  Every string that reaches
``unsafe_allow_html`` passes through :func:`esc` first, because memo text,
email bodies and contract excerpts are untrusted input.
"""

from __future__ import annotations

import html
from datetime import date, datetime
from typing import Any

import pandas as pd

NOT_AVAILABLE = "Not Available"


def esc(value: Any) -> str:
    """HTML-escape any value for safe use inside markup."""
    return html.escape("" if value is None else str(value), quote=True)


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def money(value: Any, currency: str | None = None, cents: bool = False, missing: str = NOT_AVAILABLE) -> str:
    """'CAD 6,000.00', '(1,250.00)'; never a bare float."""
    if is_missing(value) or value == "":
        return missing
    amount = float(value) / 100 if cents else float(value)
    text = f"{abs(amount):,.2f}"
    text = f"({text})" if amount < 0 else text
    return f"{currency} {text}" if currency else text


def count(value: Any, missing: str = NOT_AVAILABLE) -> str:
    if is_missing(value):
        return missing
    return f"{int(value):,}"


def pct(value: Any, missing: str = "Not Applicable") -> str:
    if is_missing(value):
        return missing
    return f"{float(value):.1%}"


def day(value: Any, missing: str = NOT_AVAILABLE) -> str:
    """ISO date without a time component."""
    if is_missing(value) or value == "":
        return missing
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(value)


def text(value: Any, missing: str = NOT_AVAILABLE) -> str:
    if is_missing(value) or str(value).strip() == "":
        return missing
    return str(value)


def amounts_by_currency(amounts: dict[str, int]) -> list[str]:
    """One line per currency from integer cents, sorted by currency code."""
    return [money(v, c, cents=True) for c, v in sorted(amounts.items())]
