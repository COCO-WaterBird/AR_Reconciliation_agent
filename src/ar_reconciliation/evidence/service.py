"""
Evidence for one bank receipt.

This is the single entry point the agent, the CLI and the UI use to turn the
documents linked to a receipt into facts:

  * :func:`extract_document` parses one stored document, extracts its facts
    (deterministically, plus the optional LLM extractor) and caches the
    result next to the original;
  * :func:`evidence_for_receipt` collects the facts of every document linked
    to a receipt, reports documents that could not be read, and lists the
    conflicts between them;
  * :func:`suggest_links` proposes which unlinked documents may belong to
    which receipt.  Suggestions are shown to a person; nothing is linked
    automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from ar_reconciliation.core.primitives import money_to_cents, normalize_key, normalize_text
from ar_reconciliation.evidence.extraction import (
    extract_facts,
    find_conflicts,
    find_injection_attempts,
)
from ar_reconciliation.evidence.models import Document, EvidenceFact, TextSegment
from ar_reconciliation.evidence.parsers import parse_document
from ar_reconciliation.evidence.store import DocumentStore

__all__ = [
    "ExtractedDocument",
    "ReceiptEvidence",
    "extract_document",
    "evidence_for_receipt",
    "suggest_links",
]


@dataclass
class ReceiptEvidence:
    """The documents and facts supporting one bank receipt."""

    documents: list[Document] = field(default_factory=list)
    facts: list[EvidenceFact] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: The text lines of each readable document, as (location, text).  Used
    #: only to verify that a cited quote really is in the document.
    lines: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    #: Lines addressed to an AI system (possible prompt injection).
    warnings: list[str] = field(default_factory=list)

    @property
    def has_documents(self) -> bool:
        return bool(self.documents)


@dataclass
class ExtractedDocument:
    """The outcome of reading one document."""

    facts: list[EvidenceFact]
    status: str
    error: str
    notes: list[str]
    lines: list[tuple[str, str]]


def extract_document(
    store: DocumentStore,
    doc_id: str,
    llm_extractor: Any | None = None,
    refresh: bool = False,
) -> ExtractedDocument:
    """
    Parse and extract one document, using the cached facts when present.

    Returns the facts, the parse status and error, notes about facts the LLM
    extractor proposed but that failed verification, and the text lines.
    """
    if not refresh and llm_extractor is None:
        cached = store.load_facts(doc_id)
        if cached is not None:
            facts, status, error, lines = cached
            return ExtractedDocument(facts, status, error, [], lines)

    document = store.get(doc_id)
    if not store.verify(doc_id):
        error = "The stored original no longer matches its recorded hash."
        store.save_facts(doc_id, [], "Unreadable", error)
        return ExtractedDocument([], "Unreadable", error, [], [])

    parsed = parse_document(store.path_of(doc_id), doc_id, document.filename)
    facts: list[EvidenceFact] = []
    notes: list[str] = []
    if parsed.status == "Parsed":
        facts = extract_facts(parsed.segments)
        if llm_extractor is not None:
            try:
                llm_facts, dropped = llm_extractor.extract(parsed.segments)
                notes.extend(dropped)
                seen = {(f.field, f.value.upper(), f.amount_cents, f.location) for f in facts}
                for fact in llm_facts:
                    key = (fact.field, fact.value.upper(), fact.amount_cents, fact.location)
                    if key not in seen:
                        facts.append(fact)
                        seen.add(key)
            except Exception as exc:  # the model is optional; the rules facts stand
                notes.append(
                    f"{doc_id}: LLM extraction failed ({type(exc).__name__}); "
                    f"the deterministic facts were kept."
                )
    lines = [(segment.location, segment.text) for segment in parsed.segments]
    store.save_facts(doc_id, facts, parsed.status, parsed.error, lines)
    return ExtractedDocument(facts, parsed.status, parsed.error, notes, lines)


def evidence_for_receipt(
    store: DocumentStore | None,
    txn_id: str,
    receipt_currency: str = "",
    llm_extractor: Any | None = None,
) -> ReceiptEvidence:
    """Collect the evidence linked to one receipt."""
    evidence = ReceiptEvidence()
    if store is None:
        return evidence
    for document in store.documents_for(txn_id):
        evidence.documents.append(document)
        extracted = extract_document(store, document.doc_id, llm_extractor)
        evidence.notes.extend(extracted.notes)
        if extracted.status != "Parsed":
            evidence.unreadable.append(
                f"{document.doc_id} ({document.filename}): {extracted.error or extracted.status}"
            )
            continue
        evidence.facts.extend(extracted.facts)
        evidence.lines[document.doc_id] = extracted.lines
        evidence.warnings.extend(
            find_injection_attempts(
                TextSegment(document.doc_id, location, text) for location, text in extracted.lines
            )
        )
    evidence.conflicts = find_conflicts(evidence.facts, receipt_currency)
    return evidence


def suggest_links(
    store: DocumentStore,
    unmatched_df: pd.DataFrame,
) -> list[dict[str, str]]:
    """
    Suggest which receipts a document may support.

    A suggestion needs a concrete overlap: an invoice number the receipt's
    memo names, or a stated payment amount equal to the receipt's gross or
    net amount.  The reason is returned so a person can judge it.
    """
    suggestions: list[dict[str, str]] = []
    for document in store.documents():
        extracted = extract_document(store, document.doc_id)
        if extracted.status != "Parsed":
            continue
        facts = extracted.facts
        invoices = {f.invoice.upper() for f in facts if f.invoice}
        amounts = {f.amount_cents for f in facts if f.amount_cents is not None}
        linked = {normalize_key(t) for t in document.linked_txns}
        for _, row in unmatched_df.iterrows():
            txn = normalize_text(row.get("Bank Transaction ID", ""))
            if not txn or normalize_key(txn) in linked:
                continue
            memo_invoices = {
                v.strip().upper()
                for v in normalize_text(row.get("Extracted Invoice Numbers", "")).split(",")
                if v.strip()
            }
            reasons: list[str] = []
            shared = sorted(invoices & memo_invoices)
            if shared:
                reasons.append(f"names {', '.join(shared)} like the receipt memo")
            receipt_amounts = set()
            for column in ("Gross Receipt Amount", "Net Amount"):
                try:
                    receipt_amounts.add(money_to_cents(row.get(column, "")))
                except Exception:
                    continue
            if amounts & (receipt_amounts - {0}):
                reasons.append("states an amount equal to the receipt")
            if reasons:
                suggestions.append(
                    {
                        "Document ID": document.doc_id,
                        "File Name": document.filename,
                        "Bank Transaction ID": txn,
                        "Reason": "; ".join(reasons),
                    }
                )
    return suggestions


def facts_by_doc(facts: Iterable[EvidenceFact]) -> dict[str, list[EvidenceFact]]:
    grouped: dict[str, list[EvidenceFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.doc_id, []).append(fact)
    return grouped
