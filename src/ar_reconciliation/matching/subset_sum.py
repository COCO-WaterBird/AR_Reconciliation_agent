"""
Subset-sum matching over open AR invoices.

One receipt can pay several invoices.  Finding the combination whose total
equals the receipt is a subset-sum problem, solved here by dynamic
programming over the candidate amounts.
"""

from __future__ import annotations

from typing import Iterable

from ar_reconciliation.matching.models import CandidateInvoice

__all__ = ["find_subset_sum"]


def find_subset_sum(
    candidates: Iterable[CandidateInvoice],
    target_cents: int,
    tolerance_cents: int = 0,
) -> list[int] | None:
    """
    Find a set of candidates whose amounts sum to the target.

    Candidates are considered in the order given, and the first combination
    that reaches the target wins.  A single invoice within tolerance is
    preferred over a combination, because it is the more likely explanation
    and leaves the other invoices open for their own receipts.

    Args:
        candidates: The invoices available to match.
        target_cents: The amount the receipt has to settle.
        tolerance_cents: How far off the sum may be and still count.

    Returns:
        The AR indexes of the matching invoices, or None if there is no
        combination.
    """
    candidates = [
        c
        for c in candidates
        if c.amount_cents > 0 and c.amount_cents <= target_cents + tolerance_cents
    ]

    # A single invoice that matches exactly is preferred over a combination.
    for candidate in candidates:
        if abs(candidate.amount_cents - target_cents) <= tolerance_cents:
            return [candidate.ar_index]

    # Dynamic programming: reachable[sum] is the list of indexes that add
    # up to that sum.
    reachable: dict[int, list[int]] = {0: []}

    for candidate in candidates:
        for existing_sum, existing_indexes in list(reachable.items()):
            new_sum = existing_sum + candidate.amount_cents
            if new_sum > target_cents + tolerance_cents:
                continue
            if new_sum not in reachable:
                reachable[new_sum] = existing_indexes + [candidate.ar_index]
            if abs(new_sum - target_cents) <= tolerance_cents:
                return reachable[new_sum]

    return None
