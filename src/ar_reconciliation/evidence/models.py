"""
Data models for supporting documents and the evidence extracted from them.

Three records travel through the evidence pipeline:

  * :class:`Document` is one uploaded file: its identity (a content hash),
    where the untouched original is stored, and which bank receipts it
    supports.  One document may support several receipts, and one receipt
    may be supported by several documents.
  * :class:`TextSegment` is one addressable piece of a document's text, for
    example "page 2, line 7" of a PDF or "body line 4" of an email.  The
    location is what lets a reviewer find the evidence in the original.
  * :class:`EvidenceFact` is one financial fact read from a segment, with the
    verbatim quote it was read from.  A fact without a quote that can be
    found in the document is never accepted, so nothing is fabricated.

Money is held as integer cents, as everywhere else in the project.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "DOCUMENT_KINDS",
    "FACT_FIELDS",
    "Document",
    "TextSegment",
    "ParseResult",
    "EvidenceFact",
]

#: Accepted file extensions and the document kind each one is parsed as.
DOCUMENT_KINDS: dict[str, str] = {
    ".pdf": "pdf",
    ".eml": "email",
    ".txt": "text",
    ".xlsx": "spreadsheet",
    ".csv": "spreadsheet",
}

#: The controlled vocabulary of fact fields.  The LLM extractor's schema uses
#: the same list as an enum, so a model cannot invent a new kind of fact.
FACT_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "contract_number",
    "credit_memo",
    "payment_amount",
    "allocation",
    "payment_date",
    "payment_terms",
    "discount_terms",
    "installment",
    "deduction",
    "dispute",
    "payer_on_behalf_of",
    "customer_name",
)


@dataclass
class Document:
    """One uploaded supporting document."""

    doc_id: str
    filename: str
    kind: str
    size_bytes: int
    sha256: str
    uploaded_at: str
    stored_name: str
    linked_txns: list[str] = field(default_factory=list)
    uploaded_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Document":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class TextSegment:
    """One addressable line of document text."""

    doc_id: str
    location: str
    text: str


@dataclass
class ParseResult:
    """The text of one document, or the reason it could not be read."""

    doc_id: str
    status: str  # "Parsed", "No Text", "Unreadable"
    segments: list[TextSegment] = field(default_factory=list)
    error: str = ""


@dataclass
class EvidenceFact:
    """
    One financial fact read from a supporting document.

    ``value`` is the fact in text form (an invoice number, a date, "2/10 net
    30").  ``amount_cents`` and ``currency`` are set for monetary facts.
    ``details`` carries structured extras, for example the discount percentage
    in basis points and the discount window in days.
    """

    doc_id: str
    field: str
    value: str
    location: str
    quote: str
    method: str = "rules"
    amount_cents: int | None = None
    currency: str = ""
    invoice: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """A short, human-readable citation, e.g. "DOC-1A2B3C4D5E6F page 1, line 3"."""
        return f"{self.doc_id} {self.location}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceFact":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
