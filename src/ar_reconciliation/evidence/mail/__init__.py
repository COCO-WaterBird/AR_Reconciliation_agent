"""Payment emails: read-only Gmail or a local mailbox, synced incrementally into the evidence store."""

from ar_reconciliation.evidence.mail.sources import GMAIL_SCOPE, GmailSource, LocalMailboxSource, MailMessage
from ar_reconciliation.evidence.mail.sync import (
    RECORD_COLUMNS,
    SyncResult,
    email_payment_records,
    link_email_records,
    sync_mailbox,
)

__all__ = ["GMAIL_SCOPE", "GmailSource", "LocalMailboxSource", "MailMessage", "RECORD_COLUMNS", "SyncResult",
           "email_payment_records", "link_email_records", "sync_mailbox"]
