"""
The result types the matching layer produces.

These are the vocabulary between the matching algorithms and the service
that applies a match to the workbook.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CandidateInvoice", "MatchResult"]


@dataclass(frozen=True)
class CandidateInvoice:
    """One open AR invoice considered as a possible match."""

    ar_index: int
    amount_cents: int
    invoice_number: str


@dataclass(frozen=True)
class MatchResult:
    """
    A proposed match between one receipt and one or more invoices.

    ``applied_cents`` is what is to be applied to AR.  ``residual_cents`` is
    what is left open, and is signed: negative means the customer paid more
    than the referenced AR.
    """

    ar_indexes: list[int]
    applied_cents: int
    residual_cents: int
    method: str
    note: str
