"""
Masking personal and banking data before text leaves the machine.

Business decision E3: when the external LLM backend is used, only extracted
facts and short quotes are sent, and the quotes are masked first.  Invoice
numbers, amounts, dates and company names are kept, because they are the
evidence; email addresses, phone numbers and bank account identifiers are
not needed to reason about a payment and are replaced with a placeholder.

The report and the stored originals are never masked: masking applies only
to what is sent to a third-party model.
"""

from __future__ import annotations

import re

__all__ = ["MAX_QUOTE_CHARS", "mask_sensitive", "short_quote"]

#: The longest quote sent to a model for one fact.
MAX_QUOTE_CHARS = 300

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(
    r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w-])"
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_LABELLED_ACCOUNT = re.compile(
    r"(?i)\b(account|acct|routing|iban|swift|bic|aba|sort code)"
    r"(\s*(?:no\.?|number|#)?\s*[:#]?\s*)([A-Z0-9][A-Z0-9 -]{3,}[A-Z0-9])"
)
_LONG_DIGITS = re.compile(r"(?<![\w-])\d{8,}(?![\w-])")


def mask_sensitive(text: str) -> str:
    """Replace email addresses, phone numbers and account identifiers."""
    text = _EMAIL.sub("[email]", text)
    text = _LABELLED_ACCOUNT.sub(lambda m: f"{m.group(1)}{m.group(2)}[account]", text)
    text = _IBAN.sub("[account]", text)
    text = _PHONE.sub("[phone]", text)
    text = _LONG_DIGITS.sub("[number]", text)
    return text


def short_quote(text: str, limit: int = MAX_QUOTE_CHARS) -> str:
    """A masked quote no longer than ``limit`` characters."""
    masked = mask_sensitive(" ".join(text.split()))
    return masked if len(masked) <= limit else masked[: limit - 1] + "…"
