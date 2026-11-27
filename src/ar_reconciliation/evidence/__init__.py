"""
Supporting-document evidence for receipts the engine could not settle.

The pipeline is: :mod:`store` (preserve and link uploads) ->
:mod:`parsers` (text lines with locations) -> :mod:`extraction` (facts with
verbatim quotes) -> :mod:`service` (the evidence for one receipt).

Document content is untrusted data.  It is never executed or obeyed; it is
only read for facts, and every fact keeps the quote and location it came
from.
"""

from ar_reconciliation.evidence.models import (
    DOCUMENT_KINDS,
    FACT_FIELDS,
    Document,
    EvidenceFact,
    ParseResult,
    TextSegment,
)
from ar_reconciliation.evidence.store import MAX_DOCUMENT_BYTES, DocumentRejected, DocumentStore
from ar_reconciliation.evidence.parsers import parse_bytes, parse_document
from ar_reconciliation.evidence.extraction import (
    LLMExtractor,
    amounts_in,
    extract_facts,
    find_conflicts,
    verify_fact,
)
from ar_reconciliation.evidence.service import (
    ReceiptEvidence,
    evidence_for_receipt,
    extract_document,
    suggest_links,
)

__all__ = [
    "DOCUMENT_KINDS",
    "FACT_FIELDS",
    "MAX_DOCUMENT_BYTES",
    "Document",
    "DocumentRejected",
    "DocumentStore",
    "EvidenceFact",
    "LLMExtractor",
    "ParseResult",
    "ReceiptEvidence",
    "TextSegment",
    "amounts_in",
    "evidence_for_receipt",
    "extract_document",
    "extract_facts",
    "find_conflicts",
    "parse_bytes",
    "parse_document",
    "suggest_links",
    "verify_fact",
]
