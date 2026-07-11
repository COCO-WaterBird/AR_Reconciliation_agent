"""
North America-style AR cash application / bank receipts reconciliation.

Input files in the same folder:
  1) Bank_Cash_Receipts.xlsx
  2) Open_AR_Detail.xlsx

Output:
  Open_AR_Detail_Reconciled_Result.xlsx

Install dependencies:
  python -m pip install pandas openpyxl

Run:
  python auto_reconcile_na.py
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent

BANK_FILE = BASE_DIR / "Bank_Cash_Receipts.xlsx"
AR_FILE = BASE_DIR / "Open_AR_Detail.xlsx"
OUTPUT_FILE = BASE_DIR / "Open_AR_Detail_Reconciled_Result.xlsx"

BANK_SHEET = "Bank Receipts"
AR_SHEET = "Open AR"

# ===== Bank file columns =====
BANK_TRANSACTION_ID_COL = "Bank Transaction ID"
BANK_VALUE_DATE_COL = "Value Date"
BANK_CUSTOMER_NAME_COL = "Customer Name"
BANK_CUSTOMER_ID_COL = "ERP Customer ID"
BANK_CURRENCY_COL = "Currency"
BANK_MATCH_AMOUNT_COL = "Net Amount"
BANK_STATUS_COL = "Status"

# ===== Open AR file columns =====
AR_CUSTOMER_ID_COL = "ERP Customer ID"
AR_CUSTOMER_NAME_COL = "Customer Name"
AR_INVOICE_COL = "Invoice Number"
AR_INVOICE_DATE_COL = "Invoice Date"
AR_DUE_DATE_COL = "Due Date"
AR_CURRENCY_COL = "Currency"
AR_OPEN_AMOUNT_COL = "Open Amount"
AR_STATUS_COL = "AR Status"
AR_RECON_STATUS_COL = "Reconciliation Status"

# ===== Output columns written back to Open AR =====
OUT_MATCHED_BANK_TXN_COL = "Matched Bank Transaction ID"
OUT_MATCHED_PAYMENT_DATE_COL = "Matched Payment Date"
OUT_MATCHED_AMOUNT_COL = "Matched Amount"
OUT_RECON_NOTES_COL = "Reconciliation Notes"

# Exact match by cents. Set this to 1 or 2 if you want a tiny rounding tolerance.
AMOUNT_TOLERANCE_CENTS = 0

# Safety limit. For very large customers, use remittance references or narrow candidates first.
MAX_CANDIDATES_PER_BANK_RECEIPT = 80


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_key(value: object) -> str:
    return normalize_text(value).upper().replace(" ", "")


def money_to_cents(value: object) -> int:
    """Convert Excel money values such as $1,234.56 or (1,234.56) to integer cents."""
    if pd.isna(value):
        return 0

    if isinstance(value, (int, float)):
        dec = Decimal(str(value))
    else:
        text = str(value).strip()
        if text == "":
            return 0

        negative = text.startswith("(") and text.endswith(")")
        text = (
            text.replace("$", "")
            .replace("USD", "")
            .replace("CAD", "")
            .replace(",", "")
            .replace("(", "")
            .replace(")", "")
            .strip()
        )
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Cannot parse money value: {value!r}") from exc
        if negative:
            dec = -dec

    return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class CandidateInvoice:
    ar_index: int
    amount_cents: int
    invoice_number: str


def find_subset_sum(candidates: Iterable[CandidateInvoice], target_cents: int, tolerance_cents: int = 0) -> list[int] | None:
    """
    Return AR dataframe indexes whose Open Amount total equals the bank amount.
    Dynamic programming is reliable for exact cents but can be expensive for huge candidate sets.
    """
    candidates = [c for c in candidates if c.amount_cents > 0 and c.amount_cents <= target_cents + tolerance_cents]

    # Prefer exact one-invoice matches first.
    for c in candidates:
        if abs(c.amount_cents - target_cents) <= tolerance_cents:
            return [c.ar_index]

    dp: dict[int, list[int]] = {0: []}

    for c in candidates:
        # Copy current states so each invoice is used at most once.
        for existing_sum, existing_indexes in list(dp.items()):
            new_sum = existing_sum + c.amount_cents
            if new_sum > target_cents + tolerance_cents:
                continue
            if new_sum not in dp:
                dp[new_sum] = existing_indexes + [c.ar_index]
            if abs(new_sum - target_cents) <= tolerance_cents:
                return dp[new_sum]

    return None


def require_columns(df: pd.DataFrame, required: list[str], file_label: str) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        available = "\n  - ".join(map(str, df.columns))
        raise ValueError(
            f"{file_label} is missing required columns: {missing}\n\nAvailable columns:\n  - {available}"
        )


def main() -> None:
    print("Reading input workbooks...")
    bank_df = pd.read_excel(BANK_FILE, sheet_name=BANK_SHEET)
    ar_df = pd.read_excel(AR_FILE, sheet_name=AR_SHEET)

    require_columns(
        bank_df,
        [
            BANK_TRANSACTION_ID_COL,
            BANK_VALUE_DATE_COL,
            BANK_CUSTOMER_NAME_COL,
            BANK_CUSTOMER_ID_COL,
            BANK_CURRENCY_COL,
            BANK_MATCH_AMOUNT_COL,
            BANK_STATUS_COL,
        ],
        "Bank_Cash_Receipts.xlsx",
    )
    require_columns(
        ar_df,
        [
            AR_CUSTOMER_ID_COL,
            AR_CUSTOMER_NAME_COL,
            AR_INVOICE_COL,
            AR_INVOICE_DATE_COL,
            AR_DUE_DATE_COL,
            AR_CURRENCY_COL,
            AR_OPEN_AMOUNT_COL,
            AR_STATUS_COL,
            AR_RECON_STATUS_COL,
        ],
        "Open_AR_Detail.xlsx",
    )

    for col in [OUT_MATCHED_BANK_TXN_COL, OUT_MATCHED_PAYMENT_DATE_COL, OUT_MATCHED_AMOUNT_COL, OUT_RECON_NOTES_COL]:
        if col not in ar_df.columns:
            ar_df[col] = ""

    # Keep output note/id columns string-compatible even when Excel reads blank columns as NaN/float.
    for col in [OUT_MATCHED_BANK_TXN_COL, OUT_MATCHED_PAYMENT_DATE_COL, OUT_MATCHED_AMOUNT_COL, OUT_RECON_NOTES_COL]:
        if ar_df[col].dtype.kind == "f":
            ar_df[col] = ar_df[col].astype("object")
    bank_df["Reconciliation Batch ID"] = bank_df.get("Reconciliation Batch ID", "").astype("object")
    bank_df["Processing Notes"] = ""

    bank_df["__bank_amount_cents"] = bank_df[BANK_MATCH_AMOUNT_COL].apply(money_to_cents)
    ar_df["__ar_open_amount_cents"] = ar_df[AR_OPEN_AMOUNT_COL].apply(money_to_cents)

    # Large companies usually reconcile by ERP customer ID. Fall back to normalized customer name if ID is blank.
    bank_df["__customer_key"] = bank_df[BANK_CUSTOMER_ID_COL].apply(normalize_key)
    bank_df.loc[bank_df["__customer_key"] == "", "__customer_key"] = bank_df[BANK_CUSTOMER_NAME_COL].apply(normalize_key)

    ar_df["__customer_key"] = ar_df[AR_CUSTOMER_ID_COL].apply(normalize_key)
    ar_df.loc[ar_df["__customer_key"] == "", "__customer_key"] = ar_df[AR_CUSTOMER_NAME_COL].apply(normalize_key)

    bank_df["__currency_key"] = bank_df[BANK_CURRENCY_COL].apply(lambda x: normalize_text(x).upper())
    ar_df["__currency_key"] = ar_df[AR_CURRENCY_COL].apply(lambda x: normalize_text(x).upper())

    unmatched_bank_rows: list[dict[str, object]] = []
    matched_receipt_count = 0
    matched_invoice_count = 0

    # Sort bank receipts by value date, then transaction ID.
    bank_df = bank_df.sort_values([BANK_VALUE_DATE_COL, BANK_TRANSACTION_ID_COL], kind="stable")

    print("Starting reconciliation...")
    for bank_index, bank_row in bank_df.iterrows():
        bank_status = normalize_text(bank_row[BANK_STATUS_COL]).lower()
        if bank_status in {"reconciled", "matched", "closed"}:
            continue

        target_cents = int(bank_row["__bank_amount_cents"])
        if target_cents <= 0:
            continue

        customer_key = bank_row["__customer_key"]
        currency_key = bank_row["__currency_key"]

        ar_mask = (
            (ar_df["__customer_key"] == customer_key)
            & (ar_df["__currency_key"] == currency_key)
            & (ar_df["__ar_open_amount_cents"] > 0)
            & (~ar_df[AR_RECON_STATUS_COL].fillna("").str.strip().str.lower().isin({"reconciled", "matched", "closed"}))
            & (ar_df[AR_STATUS_COL].fillna("").str.strip().str.lower().isin({"open", ""}))
        )

        candidates_df = ar_df.loc[ar_mask].copy()
        if candidates_df.empty:
            unmatched_bank_rows.append({
                "Bank Transaction ID": bank_row[BANK_TRANSACTION_ID_COL],
                "Value Date": bank_row[BANK_VALUE_DATE_COL],
                "Customer Name": bank_row[BANK_CUSTOMER_NAME_COL],
                "ERP Customer ID": bank_row[BANK_CUSTOMER_ID_COL],
                "Currency": bank_row[BANK_CURRENCY_COL],
                "Bank Amount": bank_row[BANK_MATCH_AMOUNT_COL],
                "Reason": "No open AR candidates found for the same ERP Customer ID and currency.",
            })
            bank_df.at[bank_index, "Processing Notes"] = "No open AR candidates found."
            continue

        # Sort by due date and invoice date to make matching deterministic.
        candidates_df = candidates_df.sort_values([AR_DUE_DATE_COL, AR_INVOICE_DATE_COL, AR_INVOICE_COL], kind="stable")
        if len(candidates_df) > MAX_CANDIDATES_PER_BANK_RECEIPT:
            candidates_df = candidates_df.head(MAX_CANDIDATES_PER_BANK_RECEIPT)

        candidates = [
            CandidateInvoice(
                ar_index=int(idx),
                amount_cents=int(row["__ar_open_amount_cents"]),
                invoice_number=normalize_text(row[AR_INVOICE_COL]),
            )
            for idx, row in candidates_df.iterrows()
        ]

        matched_ar_indexes = find_subset_sum(candidates, target_cents, AMOUNT_TOLERANCE_CENTS)

        if not matched_ar_indexes:
            unmatched_bank_rows.append({
                "Bank Transaction ID": bank_row[BANK_TRANSACTION_ID_COL],
                "Value Date": bank_row[BANK_VALUE_DATE_COL],
                "Customer Name": bank_row[BANK_CUSTOMER_NAME_COL],
                "ERP Customer ID": bank_row[BANK_CUSTOMER_ID_COL],
                "Currency": bank_row[BANK_CURRENCY_COL],
                "Bank Amount": bank_row[BANK_MATCH_AMOUNT_COL],
                "Candidate Invoice Count": len(candidates_df),
                "Candidate Open AR Total": candidates_df[AR_OPEN_AMOUNT_COL].sum(),
                "Reason": "No exact invoice combination equals the bank receipt amount.",
            })
            bank_df.at[bank_index, "Processing Notes"] = "No exact invoice combination found."
            continue

        batch_id = f"AUTO-{bank_row[BANK_TRANSACTION_ID_COL]}"
        invoice_numbers = ar_df.loc[matched_ar_indexes, AR_INVOICE_COL].astype(str).tolist()
        matched_amount = sum(int(ar_df.at[i, "__ar_open_amount_cents"]) for i in matched_ar_indexes) / 100

        ar_df.loc[matched_ar_indexes, AR_RECON_STATUS_COL] = "Reconciled"
        ar_df.loc[matched_ar_indexes, OUT_MATCHED_BANK_TXN_COL] = bank_row[BANK_TRANSACTION_ID_COL]
        ar_df.loc[matched_ar_indexes, OUT_MATCHED_PAYMENT_DATE_COL] = bank_row[BANK_VALUE_DATE_COL]
        ar_df.loc[matched_ar_indexes, OUT_MATCHED_AMOUNT_COL] = bank_row[BANK_MATCH_AMOUNT_COL]
        ar_df.loc[matched_ar_indexes, OUT_RECON_NOTES_COL] = (
            f"Auto-reconciled: bank receipt {bank_row[BANK_TRANSACTION_ID_COL]} "
            f"matched invoice combination {', '.join(invoice_numbers)}; total {matched_amount:,.2f} {currency_key}."
        )

        bank_df.at[bank_index, BANK_STATUS_COL] = "Reconciled"
        bank_df.at[bank_index, "Reconciliation Batch ID"] = batch_id
        bank_df.at[bank_index, "Processing Notes"] = f"Matched invoices: {', '.join(invoice_numbers)}"

        matched_receipt_count += 1
        matched_invoice_count += len(matched_ar_indexes)

    helper_cols = ["__bank_amount_cents", "__ar_open_amount_cents", "__customer_key", "__currency_key"]
    ar_output = ar_df.drop(columns=[c for c in helper_cols if c in ar_df.columns], errors="ignore")
    bank_output = bank_df.drop(columns=[c for c in helper_cols if c in bank_df.columns], errors="ignore")
    unmatched_df = pd.DataFrame(unmatched_bank_rows)

    summary_df = pd.DataFrame(
        [
            ["Matched bank receipts", matched_receipt_count],
            ["Matched AR invoice rows", matched_invoice_count],
            ["Unmatched bank receipts", len(unmatched_bank_rows)],
            ["Output file", OUTPUT_FILE.name],
        ],
        columns=["Metric", "Value"],
    )

    print(f"Matched bank receipts: {matched_receipt_count}")
    print(f"Matched AR invoice rows: {matched_invoice_count}")
    print(f"Unmatched bank receipts: {len(unmatched_bank_rows)}")
    print(f"Writing output workbook: {OUTPUT_FILE.name}")

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        ar_output.to_excel(writer, index=False, sheet_name="Reconciled Open AR")
        bank_output.to_excel(writer, index=False, sheet_name="Processed Bank Receipts")
        unmatched_df.to_excel(writer, index=False, sheet_name="Unmatched Bank Receipts")
        summary_df.to_excel(writer, index=False, sheet_name="Summary")

    print("Done.")


if __name__ == "__main__":
    main()
