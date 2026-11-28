"""
Payment-email sync: local demo mailbox and a mocked read-only Gmail client.

Covers incremental synchronization (only new message IDs are fetched),
content deduplication, extraction caching, structured email payment
records and the strict linking rules.  Email facts never drive automatic
matching (decision G2) - they are linked as evidence only.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd

from ar_reconciliation.enrichment import load_inputs
from ar_reconciliation.evidence import DocumentStore
from ar_reconciliation.evidence import service as evidence_service
from ar_reconciliation.evidence.mail import (
    GMAIL_SCOPE,
    GmailSource,
    LocalMailboxSource,
    email_payment_records,
    link_email_records,
    sync_mailbox,
)
from ar_reconciliation.evidence.samples import make_email

DEMO = Path(__file__).resolve().parents[1] / "data" / "sample" / "enriched"


def test_local_mailbox_sync_is_incremental_and_deduplicated(tmp_path):
    store = DocumentStore(tmp_path / "ev")
    source = LocalMailboxSource(DEMO / "mailbox")
    first = sync_mailbox(source, store)
    assert (first.fetched, first.new_documents, first.duplicates) == (4, 3, 1)
    second = sync_mailbox(source, store)
    assert second.fetched == 0 and second.new_documents == 0
    assert len(store.documents()) == 3


def test_unchanged_documents_are_not_parsed_again(tmp_path, monkeypatch):
    store = DocumentStore(tmp_path / "ev")
    calls = []
    real = evidence_service.parse_document

    def counting(*args, **kwargs):
        calls.append(args[1])
        return real(*args, **kwargs)

    monkeypatch.setattr(evidence_service, "parse_document", counting)
    sync_mailbox(LocalMailboxSource(DEMO / "mailbox"), store)
    parsed = len(calls)
    email_payment_records(store)
    email_payment_records(store)
    assert parsed == 3 and len(calls) == 3  # each unique document parsed exactly once


def test_email_payment_records_and_linking(tmp_path):
    store = DocumentStore(tmp_path / "ev")
    sync_mailbox(LocalMailboxSource(DEMO / "mailbox"), store)
    customers = load_inputs(customer_master=DEMO / "customer_master.csv").customers
    records = email_payment_records(store, customers).set_index("Customer ID")
    horizon = records.loc["CUST-1012"]
    assert horizon["Payment Reference"] == "REF90861208"
    assert horizon["Invoices"] == "INV-900172"
    assert horizon["Payment Amount"] == 8850.0 and horizon["Deduction Amount"] == 1000.0
    assert "damaged equipment" in horizon["Deduction Reason"]

    bank = pd.DataFrame([
        {"Bank Transaction ID": "BTRX-000155", "Bank Reference": "REF90861208", "ERP Customer ID": "CUST-1012",
         "Gross Receipt Amount": 8850.0, "Value Date": "2026-06-18"},
        {"Bank Transaction ID": "BTRX-000141", "Bank Reference": "", "ERP Customer ID": "CUST-1014",
         "Gross Receipt Amount": 2400.0, "Value Date": "2026-06-22"},
        {"Bank Transaction ID": "BTRX-000999", "Bank Reference": "", "ERP Customer ID": "CUST-1014",
         "Gross Receipt Amount": 2400.0, "Value Date": "2026-06-23"},
    ])
    linked = link_email_records(store, email_payment_records(store, customers), bank).set_index("Customer ID")
    assert linked.loc["CUST-1012", "Link Basis"] == "Exact payment reference"
    # Two Pinnacle receipts fit amount and date: no link (never guessed).
    assert linked.loc["CUST-1014", "Linked Receipt"] in ("", None) or pd.isna(linked.loc["CUST-1014", "Linked Receipt"])
    assert [d.doc_id for d in store.documents_for("BTRX-000155")]


class FakeGmail:
    """Minimal stand-in for googleapiclient's Gmail service."""

    def __init__(self, messages: dict[str, bytes]):
        self.inbox = messages
        self.fetched: list[str] = []

    def users(self):
        return self

    def messages(self):  # noqa: D401 - mirrors the API
        return self

    def list(self, userId, q, pageToken=None):
        ids = sorted(self._store)
        self._result = {"messages": [{"id": i} for i in ids]}
        return self

    def get(self, userId, id, format):
        self.fetched.append(id)
        self._result = {"raw": base64.urlsafe_b64encode(self._store[id]).decode().rstrip("=")}
        return self

    def execute(self):
        return self._result

    @property
    def _store(self):
        return self.inbox


def test_gmail_source_fetches_only_new_messages(tmp_path):
    email_a = make_email("ap@acme.example", "ar@us.example", "Payment INV-1", "Paid INV-1 1,000.00.")
    email_b = make_email("ap@acme.example", "ar@us.example", "Payment INV-2", "Paid INV-2 500.00.")
    service = FakeGmail({"m1": email_a})
    source = GmailSource(service=service)
    store = DocumentStore(tmp_path / "ev")
    assert sync_mailbox(source, store).new_documents == 1
    service.inbox["m2"] = email_b
    second = sync_mailbox(source, store)
    assert second.fetched == 1 and service.fetched == ["m1", "m2"]
    assert len(store.documents()) == 2
    assert GMAIL_SCOPE.endswith("gmail.readonly")
