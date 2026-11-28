"""
Incremental mailbox synchronization and email payment records.

``sync_mailbox`` fetches only messages it has not processed before (the IDs
are kept in ``mail_state.json`` next to the document store), stores each raw
message through the existing :class:`DocumentStore` (identical content is
stored once, whatever its message ID) and extracts it through the existing
per-document facts cache - an unchanged document is never parsed twice.

``email_payment_records`` turns the extracted facts into one structured
record per email: customer (sender domain via the Customer Master, or a
stated customer), payment reference, payment date, invoices, amounts,
deductions and reasons.  Records are linked to bank receipts only on an
exact payment-reference match or a unique customer + amount + date.  They
are evidence for review and for the AI agent; they never drive an automatic
match (decision G2).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import pandas as pd

from ar_reconciliation.core.primitives import format_money, normalize_key, normalize_text
from ar_reconciliation.evidence.mail.sources import MailSource, iter_new
from ar_reconciliation.evidence.service import extract_document
from ar_reconciliation.evidence.store import DocumentRejected, DocumentStore

__all__ = ["SyncResult", "sync_mailbox", "email_payment_records", "link_email_records", "RECORD_COLUMNS"]

STATE_FILE = "mail_state.json"
RECORDS_FILE = "email_payments.csv"
_REFERENCE = re.compile(r"\b(?:payment\s+reference|reference|ref(?:erence)?\s*(?:no\.?|#)?)\s*[:#]?\s*([A-Z]{2,5}\d{5,})\b",
                        re.IGNORECASE)
_REF_TOKEN = re.compile(r"\bREF\d{6,}\b")
RECORD_COLUMNS = ["Document ID", "Message ID", "Source", "From", "Customer ID", "Customer", "Payment Reference",
                  "Payment Date", "Invoices", "Payment Amount", "Deduction Amount", "Deduction Reason",
                  "Linked Receipt", "Link Basis"]


@dataclass
class SyncResult:
    source: str
    listed: int = 0
    fetched: int = 0
    new_documents: int = 0
    duplicates: int = 0
    rejected: list[str] = field(default_factory=list)
    synced_at: str = ""


def _state(store: DocumentStore) -> dict:
    path = store.root / STATE_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"processed": {}, "last_sync": ""}


def _save_state(store: DocumentStore, state: dict) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def sync_mailbox(source: MailSource, store: DocumentStore) -> SyncResult:
    """Fetch new messages only, store them (deduplicated) and extract them (cached)."""
    state = _state(store)
    processed = state.setdefault("processed", {}).setdefault(source.name, {})
    result = SyncResult(source=source.name)
    ids = source.list_ids()
    result.listed = len(ids)
    for message_id in iter_new(source, processed):
        message = source.fetch(message_id)
        result.fetched += 1
        try:
            document, created = store.add_bytes(message.raw, f"{Path(message_id).stem}.eml", [],
                                                uploaded_by=f"{source.name} sync")
        except DocumentRejected as exc:
            result.rejected.append(f"{message_id}: {exc}")
            processed[message_id] = ""
            continue
        extract_document(store, document.doc_id)  # cached by document; never re-parsed
        processed[message_id] = document.doc_id
        if created:
            result.new_documents += 1
        else:
            result.duplicates += 1
    result.synced_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    state["last_sync"] = result.synced_at
    _save_state(store, state)
    return result


def _header(lines: list[tuple[str, str]], name: str) -> str:
    for location, text in lines:
        if location.endswith(f"header {name}"):
            return text.split(":", 1)[1].strip() if ":" in text else text
    return ""


def email_payment_records(store: DocumentStore, customers: pd.DataFrame | None = None) -> pd.DataFrame:
    """One structured payment record per stored email (from the facts cache)."""
    domains: dict[str, tuple[str, str]] = {}
    if customers is not None and not customers.empty:
        for _, row in customers.iterrows():
            for domain in str(row.get("email_domains", "")).split(";"):
                if domain.strip():
                    domains[domain.strip().lower()] = (row["customer_id"], row["customer_name"])
    state = _state(store)
    message_of = {doc: mid for src in state.get("processed", {}).values() for mid, doc in src.items() if doc}
    rows = []
    for document in store.documents():
        if document.kind != "email":
            continue
        extracted = extract_document(store, document.doc_id)
        facts, lines = extracted.facts, extracted.lines
        sender = parseaddr(_header(lines, "From"))[1].lower()
        customer_id, customer = domains.get(sender.split("@")[-1], ("", ""))
        stated = [f.value for f in facts if f.field in {"customer_name", "payer_on_behalf_of"}]
        text = " ".join(t for _, t in lines)
        reference = ""
        match = _REFERENCE.search(text) or _REF_TOKEN.search(text)
        if match:
            reference = (match.group(1) if match.groups() else match.group(0)).upper()
        dates = [f.value for f in facts if f.field == "payment_date"]
        if not dates and _header(lines, "Date"):
            try:
                dates = [parsedate_to_datetime(_header(lines, "Date")).date().isoformat()]
            except (TypeError, ValueError):
                dates = []
        amounts = [f.amount_cents for f in facts if f.field in {"allocation", "payment_amount"} and f.amount_cents]
        deductions = [f for f in facts if f.field == "deduction"]
        rows.append({
            "Document ID": document.doc_id,
            "Message ID": message_of.get(document.doc_id, ""),
            "Source": document.uploaded_by or "Upload",
            "From": sender,
            "Customer ID": customer_id,
            "Customer": customer or (stated[0] if stated else ""),
            "Payment Reference": reference,
            "Payment Date": dates[0] if dates else "",
            "Invoices": ", ".join(dict.fromkeys(f.invoice for f in facts if f.invoice)),
            "Payment Amount": (max(amounts) / 100) if amounts else None,
            "Deduction Amount": (sum(d.amount_cents or 0 for d in deductions) / 100) if deductions else None,
            "Deduction Reason": "; ".join(d.quote for d in deductions),
            "Linked Receipt": ", ".join(document.linked_txns),
            "Link Basis": "",
        })
    frame = pd.DataFrame(rows, columns=RECORD_COLUMNS)
    if not frame.empty:
        frame.to_csv(store.root / RECORDS_FILE, index=False)
    return frame


def link_email_records(store: DocumentStore, records: pd.DataFrame, bank: pd.DataFrame) -> pd.DataFrame:
    """
    Link email records to receipts: exact payment reference, else a unique
    customer + amount + date (within 3 days).  Never anything looser.
    """
    if records.empty or bank.empty:
        return records
    records = records.copy().astype(object)
    refs = {normalize_key(r): t for r, t in zip(bank.get("Bank Reference", pd.Series(dtype=str)).astype(str),
                                                bank["Bank Transaction ID"].astype(str)) if normalize_key(r)}
    for index, row in records.iterrows():
        txn, basis = "", ""
        ref = normalize_key(row["Payment Reference"])
        if ref and ref in refs:
            txn, basis = refs[ref], "Exact payment reference"
        elif row["Customer ID"] and row["Payment Amount"] is not None and not pd.isna(row["Payment Amount"]):
            candidates = bank[
                (bank["ERP Customer ID"].astype(str).map(normalize_key) == normalize_key(row["Customer ID"]))
                & (pd.to_numeric(bank["Gross Receipt Amount"], errors="coerce").round(2) == round(float(row["Payment Amount"]), 2))
            ]
            if row["Payment Date"]:
                when = pd.to_datetime(row["Payment Date"], errors="coerce")
                if not pd.isna(when):
                    dates = pd.to_datetime(candidates["Value Date"], errors="coerce")
                    candidates = candidates[(dates - when).abs() <= pd.Timedelta(days=3)]
            if len(candidates) == 1:
                txn, basis = str(candidates.iloc[0]["Bank Transaction ID"]), "Unique customer, amount and date"
        if txn:
            store.link(row["Document ID"], [txn])
            records.at[index, "Linked Receipt"] = txn
            records.at[index, "Link Basis"] = basis
    records.to_csv(store.root / RECORDS_FILE, index=False)
    return records
