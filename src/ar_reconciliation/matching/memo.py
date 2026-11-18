"""
Reading the intent out of a bank memo.

The memo on a bank receipt is the strongest evidence of what the customer
intended to pay.  This module turns that free text into two things: the
invoice numbers it names, and a scenario that describes what kind of
payment it is.

The scenario names are part of the business vocabulary.  They are referenced
by the policy sets in :mod:`ar_reconciliation.rules.policies`, so they must
stay exactly as they are.
"""

from __future__ import annotations

import re

from ar_reconciliation.core.primitives import normalize_text

__all__ = ["extract_invoice_numbers", "classify_memo"]

#: An invoice reference in free text, e.g. "INV-900175".
_INVOICE_PATTERN = re.compile(r"\bINV-\d+\b", flags=re.IGNORECASE)


def extract_invoice_numbers(value: object) -> list[str]:
    """
    Pull the invoice numbers out of a memo.

    Duplicates are removed but the order is kept, because the order in
    which the customer listed the invoices is the order in which they
    probably intended them to be paid.
    """
    text = normalize_text(value).upper()
    if not text:
        return []
    invoice_numbers = re.findall(r"\bINV-\d+\b", text)
    return list(dict.fromkeys(invoice_numbers))


def classify_memo(value: object) -> str:
    """
    Name the scenario a memo describes.

    The order of the checks matters: the first match wins, so the more
    specific phrases are tested before the more general ones.
    """
    text = normalize_text(value).lower()
    if not text:
        return "No Memo"
    if "could match" in text or "ambiguous" in text:
        return "Ambiguous Invoice Reference"
    if re.search(r"\bbank fee\b", text) or "fee deducted" in text:
        return "Bank Fee"
    if "penny rounding" in text or "rounding variance" in text:
        return "Rounding Variance"
    if "short paid" in text or "short-pay" in text or "discount claim" in text:
        return "Short Payment"
    if "extra amount" in text or "overpayment" in text:
        return "Overpayment"
    if "partial payment" in text:
        return "Partial Payment"
    if "installment" in text:
        return "Installment"
    if "duplicate" in text:
        return "Duplicate Review"
    if "currency mismatch" in text:
        return "Currency Mismatch"
    if "customer id missing" in text:
        return "Missing ERP Customer ID"
    if "disputed" in text:
        return "Dispute Payment"
    if "credit memo" in text:
        return "Credit Memo"
    if "chargeback" in text or "reversal" in text:
        return "Reversal / Chargeback"
    if "refund" in text:
        return "Refund"
    if "parent pays child" in text:
        return "Parent Child Payment"
    if "wrong customer" in text or "wrong customer/legal entity" in text:
        return "Wrong Customer or Legal Entity"
    if "tax withheld" in text:
        return "Tax Withholding"
    if "sales tax" in text or "tax adjustment" in text:
        return "Sales Tax Adjustment"
    if "fx" in text or "international wire" in text:
        return "FX Exception"
    if "future-dated" in text:
        return "Future-Dated Receipt"
    if "unexpected bank account" in text:
        return "Unexpected Bank Account"
    if "collector hold" in text:
        return "Collector Hold"
    if "small residual" in text or "write-off" in text or "write off" in text:
        return "Small Residual Write-Off"
    if "no invoice" in text or "no invoice specified" in text or "on-account" in text:
        return "On-account / Unapplied"
    if "dba payment" in text:
        return "DBA Payment"
    if "lockbox batch" in text:
        return "Lockbox Batch"
    if "remit" in text and _INVOICE_PATTERN.search(text):
        return "Multi Invoice Payment"
    if _INVOICE_PATTERN.search(text):
        return "Invoice Reference"
    return "Unclassified"
