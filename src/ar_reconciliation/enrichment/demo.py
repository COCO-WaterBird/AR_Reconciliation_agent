"""
The synthetic enriched demo pack (``data/sample/enriched/``).

Every file is invented and refers to receipts and invoices in the synthetic
sample workbooks, so the demo changes real outcomes in the sample run:

==============  ===================================================  =========================
Receipt         Scenario                                             Expected outcome
==============  ===================================================  =========================
BTRX-000122     Identical invoice amounts; remittance names one      Auto-matched
BTRX-000165     One payment, two invoices (second partially paid)    Auto-matched
BTRX-000172     Partial payment                                      Auto-matched, rest open
BTRX-000158     Withholding-tax deduction allowed by contract        Cash applied; deduction
                                                                     Authorized, pending
BTRX-000194     Promotional deduction not allowed by contract        Cash applied; deduction
                                                                     Unauthorized
BTRX-000171     Contract instalment 1 of 2                           Auto-matched
BTRX-000123     Two remittances with conflicting instructions        Review
BTRX-000125     No evidence (identical amounts)                      Review
BTRX-000191     Third-party payer                                    Review
BTRX-000168     Remittance for an invoice on collector hold          Review
BTRX-000188     Missing customer ID resolved by Customer Master      Review (no amount tie)
==============  ===================================================  =========================

``expected_matches.csv`` is the ground truth used to measure matching
accuracy.  The mailbox holds payment emails (one duplicated, one with a PDF
attachment); email facts feed review and the AI only (decision G2).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from ar_reconciliation.core.primitives import normalize_text
from ar_reconciliation.evidence.samples import make_email, minimal_pdf

__all__ = ["write_enriched_demo", "EXPECTED"]

ALIASES = {
    "CUST-1028": "Westlake Labs;Westlake Laboratory",
    "CUST-1013": "Evergreen Hospitality",
    "CUST-1020": "Maple Leaf Packaging;Maple Leaf Pkg",
    "CUST-1031": "Cascade West",
}
PARENTS = {"CUST-1031": "CUST-1030"}

REMITTANCES = [
    # remittance_id, payment_reference, bank_transaction_id, payer_name, customer_id, payment_date, currency,
    # invoice_number, amount_paid, deduction_amount, deduction_code, deduction_reason
    ("RA-1001", "REF31516757", "", "Maple Leaf Packaging CA", "CUST-1020", "2026-06-04", "USD", "INV-900134", "5000.00", "", "", ""),
    ("RA-1002", "REF99903592", "", "Meridian Office Supply", "CUST-1022", "2026-06-24", "USD", "INV-900137", "5000.00", "", "", ""),
    ("RA-1002", "REF99903592", "", "Meridian Office Supply", "CUST-1022", "2026-06-24", "USD", "INV-900138", "3650.00", "", "", ""),
    ("RA-1003", "REF28176954", "", "Horizon Telecom Services", "CUST-1012", "2026-06-02", "USD", "INV-900172", "1675.00", "", "", ""),
    ("RA-1004", "REF94457912", "", "BrightPath Education Inc.", "CUST-1015", "2026-06-04", "USD", "INV-900175", "8123.75", "251.25", "WHT", "Withholding tax 3%"),
    ("RA-1005", "REF29585415", "", "Northstar Retail Group Inc.", "CUST-1002", "2026-06-14", "USD", "INV-900194", "5550.00", "1575.00", "PROMO", "Promotional allowance"),
    ("RA-1006", "", "BTRX-000123", "Union Ridge Mining Co.", "CUST-1021", "2026-06-08", "USD", "INV-900136", "6200.00", "", "", ""),
    ("RA-1007", "", "BTRX-000123", "Union Ridge Mining Co.", "CUST-1021", "2026-06-08", "USD", "INV-900135", "6200.00", "", "", ""),
    # Duplicate of RA-1001 received again (another export): ignored, never double applied.
    ("RA-1008", "REF31516757", "", "Maple Leaf Packaging CA", "CUST-1020", "2026-06-04", "USD", "INV-900134", "5000.00", "", "", ""),
    ("RA-1009", "", "BTRX-000191", "Cascade Industrial Supply - West", "CUST-1031", "2026-06-08", "USD", "INV-900190", "2375.00", "", "", ""),
    ("RA-1010", "", "BTRX-000168", "Northeast Robotics Inc.", "CUST-1025", "2026-06-01", "USD", "INV-900186", "7125.00", "", "", ""),
]

CONTRACTS = [
    # contract_id, customer_id, invoice_number, installment_number, installment_count, due_date,
    # installment_amount, currency, allowed_deduction_codes, max_deduction_pct
    ("SC-2026-011", "CUST-1011", "INV-900120", "1", "2", "2026-06-14", "1700.00", "USD", "", ""),
    ("SC-2026-011", "CUST-1011", "INV-900120", "2", "2", "2026-07-14", "1000.00", "USD", "", ""),
    ("SC-2026-015", "CUST-1015", "INV-900175", "1", "1", "2026-06-06", "8375.00", "USD", "WHT", "3"),
    ("SC-2026-002", "CUST-1002", "INV-900194", "1", "1", "2026-04-15", "7125.00", "USD", "WHT;FREIGHT", "2"),
]

#: Ground truth: receipt -> (outcome, "invoice:amount;...").
EXPECTED = {
    "BTRX-000122": ("Auto", "INV-900134:5000.00"),
    "BTRX-000165": ("Auto", "INV-900137:5000.00;INV-900138:3650.00"),
    "BTRX-000172": ("Auto", "INV-900172:1675.00"),
    "BTRX-000158": ("Auto", "INV-900175:8123.75"),
    "BTRX-000194": ("Auto", "INV-900194:5550.00"),
    "BTRX-000171": ("Auto", "INV-900120:1700.00"),
    "BTRX-000123": ("Review", ""),
    "BTRX-000125": ("Review", ""),
    "BTRX-000191": ("Review", ""),
    "BTRX-000168": ("Review", ""),
    "BTRX-000188": ("Review", ""),
}


def _write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _mailbox(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    messages = {
        "001_horizon_deduction.eml": make_email(
            "ap@horizon-telecom.example", "ar@example-company.example", "Payment for INV-900172",
            "Hello AR team,\n\nWe paid USD 8,850.00 against INV-900172 today.\n"
            "We deducted 1,000.00 for damaged equipment received on delivery 4471.\n"
            "Payment reference REF90861208.\n", date="Thu, 18 Jun 2026 10:05:00 -0400"),
        "002_pinnacle_po_letter.eml": make_email(
            "payables@pinnacle-aerospace.example", "ar@example-company.example", "Remittance - PO-550000",
            "Please find our remittance advice attached.\n", date="Mon, 22 Jun 2026 09:00:00 -0400",
            attachments=[("remittance_PO-550000.pdf", minimal_pdf([[
                "Pinnacle Aerospace LLC - Remittance Advice", "Purchase order PO-550000",
                "This payment settles invoice INV-900156.", "Amount paid: USD 2,400.00",
                "Payment date: 2026-06-22"]]), "application", "pdf")]),
        "003_cascade_third_party.eml": make_email(
            "finance@cascade-west.example", "ar@example-company.example", "Payment for INV-900190",
            "We are remitting USD 2,375.00 on behalf of Cascade Industrial Supply for invoice INV-900190.\n",
            date="Mon, 08 Jun 2026 11:30:00 -0400"),
    }
    written = []
    for name, data in messages.items():
        path = folder / name
        path.write_bytes(data)
        written.append(path)
    # The same Horizon email forwarded again: deduplicated by content.
    duplicate = folder / "004_horizon_deduction_copy.eml"
    duplicate.write_bytes(messages["001_horizon_deduction.eml"])
    written.append(duplicate)
    return written


def write_enriched_demo(folder: Path, ar_file: Path, bank_file: Path) -> list[Path]:
    """Write the demo pack built on the synthetic sample workbooks."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    ar = pd.read_excel(ar_file, sheet_name="Open AR")
    bank = pd.read_excel(bank_file, sheet_name="Bank Receipts")
    customers: dict[str, tuple[str, str]] = {}
    for _, row in ar.iterrows():
        customers.setdefault(normalize_text(row["ERP Customer ID"]),
                             (normalize_text(row["Customer Name"]), normalize_text(row["Currency"])))
    for _, row in bank.iterrows():
        cid = normalize_text(row.get("ERP Customer ID", ""))
        if cid and cid not in customers:
            customers[cid] = (normalize_text(row["Customer Name"]), normalize_text(row["Currency"]))
    rows = []
    for cid, (name, currency) in sorted(customers.items()):
        domain = "".join(ch for ch in name.lower() if ch.isalnum())[:18] + ".example"
        rows.append((cid, name, ALIASES.get(cid, ""), PARENTS.get(cid, ""), currency, domain, "Net 30"))
    paths = [folder / "customer_master.csv", folder / "contract_schedule.csv",
             folder / "remittance_advice.csv", folder / "expected_matches.csv"]
    _write_csv(paths[0], ["customer_id", "customer_name", "aliases", "parent_customer_id", "currency",
                          "email_domains", "payment_terms"], rows)
    _write_csv(paths[1], ["contract_id", "customer_id", "invoice_number", "installment_number", "installment_count",
                          "due_date", "installment_amount", "currency", "allowed_deduction_codes",
                          "max_deduction_pct"], CONTRACTS)
    _write_csv(paths[2], ["remittance_id", "payment_reference", "bank_transaction_id", "payer_name", "customer_id",
                          "payment_date", "currency", "invoice_number", "amount_paid", "deduction_amount",
                          "deduction_code", "deduction_reason"], REMITTANCES)
    _write_csv(paths[3], ["bank_transaction_id", "expected_outcome", "expected_allocations"],
               [(k, v[0], v[1]) for k, v in EXPECTED.items()])
    return paths + _mailbox(folder / "mailbox")
