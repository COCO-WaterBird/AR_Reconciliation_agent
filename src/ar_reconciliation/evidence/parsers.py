"""
Turning supporting documents into addressable lines of text.

Every parser returns a list of :class:`TextSegment` records, one per line of
text, each with a location a reviewer can find in the original ("page 2,
line 7", "body line 4", "Remittance!row 5").  The extraction step cites
these locations, so every fact can be traced back to where it was read.

The parsers are deliberately plain:

  * PDF text is read with ``pypdf``.  A scanned PDF has no text layer; it is
    reported as such, because OCR is out of scope for this MVP.
  * Email is parsed with the standard library ``email`` package.  The
    headers, the plain-text body (or the HTML body with tags stripped) and
    any supported attachments are read.
  * Text files are read line by line; CSV and Excel row by row.

A file that cannot be read is reported with status ``Unreadable`` and the
error, never raised: one bad document must not stop a reconciliation.
"""

from __future__ import annotations

import csv
import html
import io
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path

from ar_reconciliation.evidence.models import DOCUMENT_KINDS, ParseResult, TextSegment

__all__ = ["parse_document", "parse_bytes"]

#: An attachment inside an email is parsed only to this depth, so a crafted
#: email that attaches itself cannot recurse for ever.
_MAX_ATTACHMENT_DEPTH = 2


def _lines(doc_id: str, prefix: str, text: str) -> list[TextSegment]:
    segments: list[TextSegment] = []
    for number, line in enumerate(text.splitlines(), start=1):
        cleaned = " ".join(line.split())
        if cleaned:
            segments.append(TextSegment(doc_id, f"{prefix}line {number}", cleaned))
    return segments


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _parse_pdf(doc_id: str, data: bytes, prefix: str) -> list[TextSegment]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "Reading PDF files needs the 'pypdf' package. "
            "Install it with: python -m pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    segments: list[TextSegment] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        segments.extend(_lines(doc_id, f"{prefix}page {page_number}, ", text))
    return segments


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", markup)
    markup = re.sub(r"<[^>]+>", " ", markup)
    return html.unescape(markup)


def _parse_email(doc_id: str, data: bytes, prefix: str, depth: int) -> list[TextSegment]:
    message = BytesParser(policy=policy.default).parsebytes(data)
    segments: list[TextSegment] = []
    for header in ("From", "To", "Date", "Subject"):
        value = message.get(header)
        if value:
            segments.append(
                TextSegment(doc_id, f"{prefix}header {header}", f"{header}: {' '.join(str(value).split())}")
            )

    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        content = body.get_content()
        if body.get_content_type() == "text/html":
            content = _strip_html(content)
        segments.extend(_lines(doc_id, f"{prefix}body ", content))

    if depth < _MAX_ATTACHMENT_DEPTH:
        for attachment in message.iter_attachments():
            name = attachment.get_filename() or "attachment"
            extension = Path(name).suffix.lower()
            if extension not in DOCUMENT_KINDS:
                continue
            payload = attachment.get_payload(decode=True) or b""
            try:
                segments.extend(
                    _parse_kind(
                        doc_id,
                        DOCUMENT_KINDS[extension],
                        payload,
                        f"{prefix}attachment '{name}' > ",
                        depth + 1,
                        extension,
                    )
                )
            except Exception as exc:  # one bad attachment must not hide the email
                segments.append(
                    TextSegment(
                        doc_id,
                        f"{prefix}attachment '{name}'",
                        f"[attachment could not be read: {type(exc).__name__}]",
                    )
                )
    return segments


def _parse_spreadsheet(doc_id: str, data: bytes, prefix: str, extension: str) -> list[TextSegment]:
    segments: list[TextSegment] = []
    if extension == ".csv":
        reader = csv.reader(io.StringIO(_decode_text(data)))
        for number, row in enumerate(reader, start=1):
            cells = [" ".join(c.split()) for c in row if c and c.strip()]
            if cells:
                segments.append(TextSegment(doc_id, f"{prefix}row {number}", " | ".join(cells)))
        return segments

    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = []
                for value in row:
                    if value is None:
                        continue
                    if isinstance(value, float):
                        text = f"{value:,.2f}"
                    else:
                        text = " ".join(str(value).split())
                    if text:
                        cells.append(text)
                if cells:
                    segments.append(
                        TextSegment(doc_id, f"{prefix}{sheet.title}!row {number}", " | ".join(cells))
                    )
    finally:
        workbook.close()
    return segments


def _parse_kind(
    doc_id: str, kind: str, data: bytes, prefix: str, depth: int, extension: str
) -> list[TextSegment]:
    if kind == "pdf":
        return _parse_pdf(doc_id, data, prefix)
    if kind == "email":
        return _parse_email(doc_id, data, prefix, depth)
    if kind == "spreadsheet":
        return _parse_spreadsheet(doc_id, data, prefix, extension)
    return _lines(doc_id, prefix, _decode_text(data))


def parse_bytes(doc_id: str, filename: str, data: bytes) -> ParseResult:
    """Parse a document given its bytes and its original file name."""
    extension = Path(filename).suffix.lower()
    kind = DOCUMENT_KINDS.get(extension)
    if kind is None:
        return ParseResult(doc_id, "Unreadable", error=f"Unsupported file type '{extension}'.")
    try:
        segments = _parse_kind(doc_id, kind, data, "", 0, extension)
    except Exception as exc:
        return ParseResult(doc_id, "Unreadable", error=f"{type(exc).__name__}: {exc}")
    if not segments:
        reason = (
            "No text layer; the PDF is probably a scan. OCR is out of scope."
            if kind == "pdf"
            else "The document contains no text."
        )
        return ParseResult(doc_id, "No Text", error=reason)
    return ParseResult(doc_id, "Parsed", segments=segments)


def parse_document(path: Path, doc_id: str, filename: str | None = None) -> ParseResult:
    """Parse a stored document from disk."""
    path = Path(path)
    return parse_bytes(doc_id, filename or path.name, path.read_bytes())
