"""
Subset-sum matching over open AR invoices.

One receipt can pay several invoices.  Finding the combination whose total
equals the receipt is a subset-sum problem, solved here by dynamic
programming over the candidate amounts.
"""

from __future__ import annotations

from typing import Iterable

from ar_reconciliation.matching.models import CandidateInvoice

__all__ = ["find_subset_sum", "find_subset_sums"]


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


def find_subset_sums(
    candidates: Iterable[CandidateInvoice],
    target_cents: int,
    tolerance_cents: int = 0,
    limit: int = 2,
) -> list[list[int]]:
    """
    Find up to ``limit`` distinct sets of candidates that sum to the target.

    Used to tell a unique explanation of a receipt apart from an ambiguous
    one: when two different sets of invoices both add up to the receipt, the
    engine has no basis for choosing and must route the receipt to review.

    Each subset is generated exactly once (0/1 knapsack), and at most
    ``limit`` partial sets are kept per running sum.  Capping per sum is
    safe: the number of solutions found at the target is then exactly
    ``min(limit, true number of solutions)``.

    Returns:
        The AR indexes of each solution found, in candidate order.  An empty
        list means no combination exists.
    """
    candidates = [
        c
        for c in candidates
        if c.amount_cents > 0 and c.amount_cents <= target_cents + tolerance_cents
    ]
    ceiling = target_cents + tolerance_cents

    reachable: dict[int, list[list[int]]] = {0: [[]]}
    for candidate in candidates:
        # Snapshot both the sums and their paths, so a path extended with this
        # candidate is never extended with it a second time.
        snapshot = [(total, list(paths)) for total, paths in reachable.items()]
        for total, paths in snapshot:
            new_total = total + candidate.amount_cents
            if new_total > ceiling:
                continue
            bucket = reachable.setdefault(new_total, [])
            for path in paths:
                if len(bucket) >= limit:
                    break
                bucket.append(path + [candidate.ar_index])

    solutions: list[list[int]] = []
    for total in range(target_cents - tolerance_cents, ceiling + 1):
        for path in reachable.get(total, []):
            if path and len(solutions) < limit:
                solutions.append(path)
    return solutions
