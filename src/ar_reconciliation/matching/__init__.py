"""The matching algorithms and the controls that decide what may be matched."""

from __future__ import annotations

from ar_reconciliation.matching.algorithms import (
    find_amount_combination_match,
    find_structured_allocation_match,
    find_invoice_reference_match,
    find_partial_combination_match,
)
from ar_reconciliation.matching.application import (
    apply_match,
    build_payment_signature,
)
from ar_reconciliation.matching.candidates import (
    build_candidate_mask,
    build_name_fallback_keys,
    is_open_ar_row,
)
from ar_reconciliation.matching.memo import classify_memo, extract_invoice_numbers
from ar_reconciliation.matching.models import AmbiguousMatch, CandidateInvoice, MatchResult
from ar_reconciliation.matching.subset_sum import find_subset_sum, find_subset_sums

__all__ = [
    "AmbiguousMatch",
    "CandidateInvoice",
    "MatchResult",
    "apply_match",
    "build_candidate_mask",
    "build_name_fallback_keys",
    "build_payment_signature",
    "classify_memo",
    "extract_invoice_numbers",
    "find_amount_combination_match",
    "find_structured_allocation_match",
    "find_invoice_reference_match",
    "find_partial_combination_match",
    "find_subset_sum",
    "find_subset_sums",
    "is_open_ar_row",
]
