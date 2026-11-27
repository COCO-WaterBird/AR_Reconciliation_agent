"""
The supporting-document store.

Uploaded documents are evidence in a financial control, so the store treats
them the way an auditor would expect:

  * **Originals are preserved.**  Each upload is copied byte for byte into
    ``originals/`` under its SHA-256 hash and made read-only.  The hash is
    recorded, so :meth:`DocumentStore.verify` can prove later that the file
    a reviewer relied on has not changed.
  * **Uploads are validated.**  Only the accepted file types are stored, the
    size is capped, and the file's content must agree with its extension (a
    ``.pdf`` must start with ``%PDF``), so a renamed executable is refused.
  * **Duplicates are not stored twice.**  Uploading the same bytes again links
    the existing document to the new receipts instead of creating a copy.
  * **Links are many-to-many.**  One remittance can cover several receipts,
    and one receipt can be explained by several documents.

The manifest (``manifest.json``) records every document's ID, file name,
upload time, size, hash and linked bank transaction IDs.  The store never
interprets a document's content; that is the parser's job.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

from ar_reconciliation.core.primitives import normalize_key, normalize_text
from ar_reconciliation.evidence.models import DOCUMENT_KINDS, Document, EvidenceFact

__all__ = [
    "MAX_DOCUMENT_BYTES",
    "DocumentRejected",
    "DocumentStore",
]

#: The largest file the store accepts.  Remittance advice, contracts and
#: emails are small; a much larger file is almost certainly the wrong file.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

MANIFEST_NAME = "manifest.json"


class DocumentRejected(ValueError):
    """An upload failed validation and was not stored."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _check_content(extension: str, data: bytes, filename: str) -> None:
    """Refuse a file whose content does not agree with its extension."""
    if not data:
        raise DocumentRejected(f"{filename} is empty.")
    if extension == ".pdf" and not data.lstrip()[:5] == b"%PDF-":
        raise DocumentRejected(f"{filename} does not look like a PDF file.")
    if extension == ".xlsx" and not data.startswith(b"PK\x03\x04"):
        raise DocumentRejected(f"{filename} does not look like an Excel workbook.")
    if extension in {".eml", ".txt", ".csv"}:
        if b"\x00" in data[:8192]:
            raise DocumentRejected(f"{filename} contains binary data, not text.")


class DocumentStore:
    """A folder of preserved supporting documents with a JSON manifest."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.originals = self.root / "originals"
        self.facts_dir = self.root / "facts"
        self._manifest_path = self.root / MANIFEST_NAME
        self._documents: dict[str, Document] = {}
        if self._manifest_path.exists():
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            for item in data.get("documents", []):
                document = Document.from_dict(item)
                self._documents[document.doc_id] = document

    # -- persistence ------------------------------------------------------

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"documents": [d.to_dict() for d in self._documents.values()]}
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._manifest_path)

    # -- queries ----------------------------------------------------------

    def documents(self) -> list[Document]:
        return list(self._documents.values())

    def get(self, doc_id: str) -> Document:
        try:
            return self._documents[doc_id]
        except KeyError:
            raise KeyError(f"No document with ID {doc_id}") from None

    def path_of(self, doc_id: str) -> Path:
        return self.originals / self.get(doc_id).stored_name

    def documents_for(self, txn_id: str) -> list[Document]:
        """The documents linked to one bank receipt, in upload order."""
        key = normalize_key(txn_id)
        return [
            d for d in self._documents.values()
            if key in {normalize_key(t) for t in d.linked_txns}
        ]

    def linked_txns(self) -> set[str]:
        return {t for d in self._documents.values() for t in d.linked_txns}

    # -- changes ----------------------------------------------------------

    def add(
        self,
        source: Path,
        txn_ids: list[str] | tuple[str, ...] = (),
        uploaded_by: str = "",
        filename: str | None = None,
    ) -> tuple[Document, bool]:
        """
        Validate and store one file.

        Returns the document and ``True`` when it was newly stored, or the
        existing document and ``False`` when the same bytes were uploaded
        before (the new receipt links are added to it).
        """
        source = Path(source)
        return self.add_bytes(
            source.read_bytes(), filename or source.name, txn_ids, uploaded_by
        )

    def add_bytes(
        self,
        data: bytes,
        filename: str,
        txn_ids: list[str] | tuple[str, ...] = (),
        uploaded_by: str = "",
    ) -> tuple[Document, bool]:
        """Validate and store an upload given as bytes (used by the UI)."""
        filename = Path(filename).name
        extension = Path(filename).suffix.lower()
        if extension not in DOCUMENT_KINDS:
            accepted = ", ".join(sorted(DOCUMENT_KINDS))
            raise DocumentRejected(
                f"{filename}: file type '{extension or '(none)'}' is not accepted. "
                f"Accepted types: {accepted}."
            )
        if len(data) > MAX_DOCUMENT_BYTES:
            raise DocumentRejected(
                f"{filename} is {len(data):,} bytes; the limit is {MAX_DOCUMENT_BYTES:,}."
            )
        _check_content(extension, data, filename)

        digest = _sha256(data)
        doc_id = f"DOC-{digest[:12].upper()}"
        clean_txns = [normalize_text(t) for t in txn_ids if normalize_text(t)]

        existing = self._documents.get(doc_id)
        if existing is not None:
            self._link(existing, clean_txns)
            self._save()
            return existing, False

        self.originals.mkdir(parents=True, exist_ok=True)
        stored_name = f"{digest}{extension}"
        target = self.originals / stored_name
        if not target.exists():
            target.write_bytes(data)
            # Read-only: the preserved original must not be edited in place.
            target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

        document = Document(
            doc_id=doc_id,
            filename=filename,
            kind=DOCUMENT_KINDS[extension],
            size_bytes=len(data),
            sha256=digest,
            uploaded_at=_now(),
            stored_name=stored_name,
            linked_txns=[],
            uploaded_by=normalize_text(uploaded_by),
        )
        self._link(document, clean_txns)
        self._documents[doc_id] = document
        self._save()
        return document, True

    @staticmethod
    def _link(document: Document, txn_ids: list[str]) -> None:
        known = {normalize_key(t) for t in document.linked_txns}
        for txn in txn_ids:
            if normalize_key(txn) not in known:
                document.linked_txns.append(txn)
                known.add(normalize_key(txn))

    def link(self, doc_id: str, txn_ids: list[str]) -> Document:
        document = self.get(doc_id)
        self._link(document, [normalize_text(t) for t in txn_ids if normalize_text(t)])
        self._save()
        return document

    def unlink(self, doc_id: str, txn_id: str) -> Document:
        document = self.get(doc_id)
        key = normalize_key(txn_id)
        document.linked_txns = [t for t in document.linked_txns if normalize_key(t) != key]
        self._save()
        return document

    # -- integrity --------------------------------------------------------

    def verify(self, doc_id: str) -> bool:
        """True when the preserved original still has its recorded hash."""
        path = self.path_of(doc_id)
        return path.exists() and _sha256(path.read_bytes()) == self.get(doc_id).sha256

    # -- extracted facts cache --------------------------------------------

    def save_facts(
        self,
        doc_id: str,
        facts: list[EvidenceFact],
        status: str,
        error: str = "",
        lines: list[tuple[str, str]] | None = None,
    ) -> None:
        """Keep the facts (and the text lines they were read from) next to the document."""
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "doc_id": doc_id,
            "status": status,
            "error": error,
            "extracted_at": _now(),
            "facts": [f.to_dict() for f in facts],
            "lines": [list(item) for item in (lines or [])],
        }
        (self.facts_dir / f"{doc_id}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def load_facts(
        self, doc_id: str
    ) -> tuple[list[EvidenceFact], str, str, list[tuple[str, str]]] | None:
        path = self.facts_dir / f"{doc_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        facts = [EvidenceFact.from_dict(item) for item in data.get("facts", [])]
        lines = [(str(a), str(b)) for a, b in data.get("lines", [])]
        return facts, data.get("status", ""), data.get("error", ""), lines

    def clear(self) -> None:
        """Remove the whole store (used by tests and the demo reset)."""
        if self.root.exists():
            for path in self.originals.glob("*"):
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            shutil.rmtree(self.root)
        self._documents = {}
