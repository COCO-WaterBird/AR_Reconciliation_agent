"""
The result types the matching layer produces.

These are the vocabulary between the matching algorithms and the service
that applies a match to the workbook.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CandidateInvoice", "MatchResult", "AmbiguousMatch"]


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

    The cash side of the receipt is stated explicitly, branch by branch, so
    the cash control can verify it rather than derive it:

        gross receipt == applied_cents + unapplied_cents + adjustment_cents

    ``unapplied_cents`` is cash received beyond the AR it settles (an
    overpayment), which stays on account.  ``adjustment_cents`` is the part
    of the gross amount that is neither applied nor unapplied cash: a bank
    fee deducted before the AR was cleared at net, or a signed penny rounding
    variance.
    """

    ar_indexes: list[int]
    applied_cents: int
    residual_cents: int
    method: str
    note: str
    unapplied_cents: int = 0
    adjustment_cents: int = 0
    #: Cash per invoice, aligned with ``ar_indexes``, when a structured source
    #: (remittance advice, contract schedule) states the split.  ``None`` for
    #: every engine-derived match, which keeps the existing allocation rule.
    allocations: tuple[int, ...] | None = None


@dataclass(frozen=True)
class AmbiguousMatch:
    """
    More than one set of open invoices explains the receipt equally well.

    The engine must not choose between them, so the receipt is routed to
    review with the alternatives it found.
    """

    alternatives: list[list[str]]
