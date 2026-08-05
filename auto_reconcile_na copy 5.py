"""
North America-style AR cash application / bank receipts reconciliation.
FIXED VERSION: Original source data columns are never modified.
All results are written to new output columns only.

Input files in the same folder:
  1) Bank_Cash_Receipts.xlsx
  2) Open_AR_Detail.xlsx

Output:
  Open_AR_Detail_Reconciled_Result.xlsx

Install dependencies:
  python -m pip install pandas openpyxl

Run:
  python auto_reconcile_na_fixed.py

Key fix:
  - AR_OPEN_AMOUNT_COL, AR_STATUS_COL, AR_RECON_STATUS_COL are NEVER overwritten.
  - All reconciliation results go to new output columns only.
  - Added "Suggested Recon Status" and "Suggested Bank Status" columns.
  - build_candidate_mask uses OUT_MATCHED_BANK_TXN_COL to detect already-matched
    rows within the same run, instead of relying on mutated status columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable
import re

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent

BANK_FILE = BASE_DIR / "Bank_Cash_Receipts.xlsx"
AR_FILE   = BASE_DIR / "Open_AR_Detail.xlsx"
OUTPUT_FILE = BASE_DIR / "Open_AR_Detail_Reconciled_Result.xlsx"

BANK_SHEET = "Bank Receipts"
AR_SHEET   = "Open AR"

# ===== Bank file columns (read-only) =====
BANK_TRANSACTION_ID_COL  = "Bank Transaction ID"
BANK_ACCOUNT_COL         = "Bank Account"
BANK_VALUE_DATE_COL      = "Value Date"
BANK_POSTING_DATE_COL    = "Posting Date"
BANK_CUSTOMER_NAME_COL   = "Customer Name"
BANK_REMITTER_NAME_COL   = "Remitter Name"
BANK_CUSTOMER_ID_COL     = "ERP Customer ID"
BANK_CURRENCY_COL        = "Currency"
BANK_GROSS_AMOUNT_COL    = "Gross Receipt Amount"
BANK_FEES_COL            = "Bank Fees"
BANK_NET_AMOUNT_COL      = "Net Amount"
BANK_STATUS_COL          = "Status"          # read-only source column
BANK_EXCEPTION_TYPE_COL  = "Exception Type"
BANK_MEMO_COL            = "Memo / Remittance Detail"

# ===== Open AR file columns (read-only) =====
AR_CUSTOMER_ID_COL        = "ERP Customer ID"
AR_PARENT_CUSTOMER_ID_COL = "Parent Customer ID"
AR_CUSTOMER_NAME_COL      = "Customer Name"
AR_INVOICE_COL            = "Invoice Number"
AR_INVOICE_DATE_COL       = "Invoice Date"
AR_DUE_DATE_COL           = "Due Date"
AR_CURRENCY_COL           = "Currency"
AR_OPEN_AMOUNT_COL        = "Open Amount"     # read-only source column
AR_STATUS_COL             = "AR Status"       # read-only source column
AR_RECON_STATUS_COL       = "Reconciliation Status"  # read-only source column
AR_DISPUTE_REASON_COL     = "Dispute Reason"

# ===== NEW output columns written to Open AR (never overwrites source cols) =====
OUT_MATCHED_BANK_TXN_COL   = "Matched Bank Transaction ID"
OUT_MATCHED_PAYMENT_DATE_COL = "Matched Payment Date"
OUT_MATCHED_AMOUNT_COL     = "Matched Amount"
OUT_APPLIED_AMOUNT_COL     = "Applied Amount"
OUT_RESIDUAL_AMOUNT_COL    = "Residual Amount"
OUT_MATCH_METHOD_COL       = "Match Method"
OUT_MATCH_CONFIDENCE_COL   = "Match Confidence"
OUT_REVIEW_FLAG_COL        = "Review Flag"
OUT_RECON_NOTES_COL        = "Reconciliation Notes"
OUT_SUGGESTED_RECON_STATUS_COL = "Suggested Recon Status"   # NEW — replaces mutation of AR_RECON_STATUS_COL

# ===== NEW output columns written to bank receipt =====
BANK_RECON_BATCH_ID_COL      = "Reconciliation Batch ID"
BANK_PROCESSING_NOTES_COL    = "Processing Notes"
OUT_EXTRACTED_INVOICES_COL   = "Extracted Invoice Numbers"
OUT_MEMO_SCENARIO_COL        = "Memo Scenario"
OUT_MATCH_METHOD_BANK_COL    = "Match Method"
OUT_MATCH_CONFIDENCE_BANK_COL = "Match Confidence"
OUT_SUGGESTED_BANK_STATUS_COL = "Suggested Bank Status"     # NEW — replaces mutation of BANK_STATUS_COL

# ===== Matching thresholds =====
AMOUNT_TOLERANCE_CENTS           = 0
ROUNDING_TOLERANCE_CENTS         = 2
SMALL_RESIDUAL_WRITE_OFF_CENTS   = 500
MAX_CANDIDATES_PER_BANK_RECEIPT  = 80

REVIEW_ONLY_SCENARIOS = {
    "Duplicate Review",
    "Currency Mismatch",
    "Wrong Customer or Legal Entity",
    "Reversal / Chargeback",
    "Refund",
    "FX Exception",
    "Future-Dated Receipt",
    "Unexpected Bank Account",
    "Collector Hold",
    "Dispute Payment",
    "Credit Memo",
    "Tax Withholding",
    "On-account / Unapplied",
    "Missing ERP Customer ID",
    "Ambiguous Invoice Reference",
}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_key(value: object) -> str:
    return normalize_text(value).upper().replace(" ", "")


def normalize_invoice(value: object) -> str:
    return normalize_text(value).upper()


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


def cents_to_money(cents: int) -> float:
    return float(Decimal(cents) / Decimal("100"))


def extract_invoice_numbers(value: object) -> list[str]:
    text = normalize_text(value).upper()
    if not text:
        return []
    invoice_numbers = re.findall(r"\bINV-\d+\b", text)
    return list(dict.fromkeys(invoice_numbers))


def classify_memo(value: object) -> str:
    text = normalize_text(value).lower()
    if not text:
        return "No Memo"
    if "could match" in text or "ambiguous" in text:
        return "Ambiguous Invoice Reference"
    if "bank fee" in text or "fee deducted" in text:
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
    if "remit" in text and re.search(r"\binv-\d+\b", text, flags=re.IGNORECASE):
        return "Multi Invoice Payment"
    if re.search(r"\binv-\d+\b", text, flags=re.IGNORECASE):
        return "Invoice Reference"
    return "Unclassified"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateInvoice:
    ar_index: int
    amount_cents: int
    invoice_number: str


@dataclass(frozen=True)
class MatchResult:
    ar_indexes: list[int]
    applied_cents: int
    residual_cents: int
    method: str
    confidence: str
    review_flag: str
    note: str


# ---------------------------------------------------------------------------
# Subset-sum matching
# ---------------------------------------------------------------------------

def find_subset_sum(
    candidates: Iterable[CandidateInvoice],
    target_cents: int,
    tolerance_cents: int = 0,
) -> list[int] | None:
    candidates = [
        c for c in candidates
        if c.amount_cents > 0 and c.amount_cents <= target_cents + tolerance_cents
    ]
    for c in candidates:
        if abs(c.amount_cents - target_cents) <= tolerance_cents:
            return [c.ar_index]
    dp: dict[int, list[int]] = {0: []}
    for c in candidates:
        for existing_sum, existing_indexes in list(dp.items()):
            new_sum = existing_sum + c.amount_cents
            if new_sum > target_cents + tolerance_cents:
                continue
            if new_sum not in dp:
                dp[new_sum] = existing_indexes + [c.ar_index]
            if abs(new_sum - target_cents) <= tolerance_cents:
                return dp[new_sum]
    return None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def require_columns(df: pd.DataFrame, required: list[str], file_label: str) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        available = "\n  - ".join(map(str, df.columns))
        raise ValueError(
            f"{file_label} is missing required columns: {missing}\n\nAvailable columns:\n  - {available}"
        )


def ensure_output_columns(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        if col not in df.columns:
            df[col] = ""
        elif df[col].dtype.kind == "f":
            df[col] = df[col].astype("object")


def is_open_ar_row(ar_row: pd.Series) -> bool:
    """
    Determine if an AR row is eligible for matching.
    Reads only ORIGINAL source columns — never looks at output columns.
    Allow any AR Status that is not explicitly closed/paid/cancelled.
    """
    ar_status    = normalize_text(ar_row.get(AR_STATUS_COL, "")).lower()
    recon_status = normalize_text(ar_row.get(AR_RECON_STATUS_COL, "")).lower()
    amount_cents = int(ar_row.get("__ar_open_amount_cents", 0))
    if amount_cents <= 0:
        return False
    # Exclude only rows that are explicitly finalized
    if recon_status in {"reconciled", "matched", "closed"}:
        return False
    if ar_status in {"closed", "paid", "cancelled", "void", "written off"}:
        return False
    return True


def build_candidate_mask(ar_df: pd.DataFrame, customer_key: str, currency_key: str) -> pd.Series:
    """
    FIX: Uses OUT_MATCHED_BANK_TXN_COL (a new output column) to detect rows
    already matched in this run, instead of reading the original status columns
    which we no longer mutate.
    """
    already_matched = ar_df[OUT_MATCHED_BANK_TXN_COL].fillna("").astype(str).str.strip() != ""

    return (
        (ar_df["__customer_key"] == customer_key)
        & (ar_df["__currency_key"] == currency_key)
        & (ar_df["__ar_open_amount_cents"] > 0)
        & (~already_matched)
        & (~ar_df[AR_RECON_STATUS_COL].fillna("").str.strip().str.lower().isin({"reconciled", "matched", "closed"}))
        & (~ar_df[AR_STATUS_COL].fillna("").str.strip().str.lower().isin({"closed", "paid", "cancelled", "void", "written off"}))
    )


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

def find_invoice_reference_match(
    ar_df: pd.DataFrame,
    invoice_numbers: list[str],
    scenario: str,
    target_gross_cents: int,
    target_net_cents: int,
    bank_fee_cents: int,
) -> MatchResult | None:
    if not invoice_numbers:
        return None
    if scenario in REVIEW_ONLY_SCENARIOS:
        return None

    order_map = {inv: i for i, inv in enumerate(invoice_numbers)}
    inv_df = ar_df.loc[ar_df["__invoice_key"].isin(set(invoice_numbers))].copy()
    if inv_df.empty:
        return None

    # Exclude rows already matched in this run (same fix as build_candidate_mask)
    already_matched = inv_df[OUT_MATCHED_BANK_TXN_COL].fillna("").astype(str).str.strip() != ""
    inv_df = inv_df[~already_matched]
    if inv_df.empty:
        return None

    inv_df = inv_df[inv_df.apply(is_open_ar_row, axis=1)]
    if inv_df.empty:
        return None

    inv_df["__memo_order"] = inv_df["__invoice_key"].map(order_map)
    inv_df = inv_df.sort_values(
        ["__memo_order", AR_DUE_DATE_COL, AR_INVOICE_DATE_COL], kind="stable"
    )
    referenced_total_cents = int(inv_df["__ar_open_amount_cents"].sum())

    if referenced_total_cents == target_gross_cents:
        if bank_fee_cents > 0 or scenario == "Bank Fee":
            return MatchResult(
                ar_indexes=[int(i) for i in inv_df.index],
                applied_cents=referenced_total_cents,
                residual_cents=0,
                method="Memo Invoice Match - Bank Fee",
                confidence="High",
                review_flag="Review Bank Fee GL",
                note=(
                    f"Memo invoice total equals gross receipt. "
                    f"Clear AR at gross {cents_to_money(target_gross_cents):,.2f}; "
                    f"bank cash is net {cents_to_money(target_net_cents):,.2f}; "
                    f"book bank fee {cents_to_money(bank_fee_cents):,.2f} separately."
                ),
            )
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=referenced_total_cents,
            residual_cents=0,
            method="Memo Invoice Match",
            confidence="High",
            review_flag="",
            note="Memo invoice reference and gross receipt amount match open AR.",
        )

    diff_to_gross = target_gross_cents - referenced_total_cents

    if scenario == "Rounding Variance" and abs(diff_to_gross) <= ROUNDING_TOLERANCE_CENTS:
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=referenced_total_cents,
            residual_cents=diff_to_gross,
            method="Memo Invoice Match - Rounding",
            confidence="High",
            review_flag="Auto Rounding Write-Off",
            note=f"Small penny rounding variance {cents_to_money(diff_to_gross):,.2f}.",
        )

    if scenario in {"Partial Payment", "Installment"} and 0 < target_gross_cents < referenced_total_cents:
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=target_gross_cents,
            residual_cents=referenced_total_cents - target_gross_cents,
            method=f"Memo Invoice Match - {scenario}",
            confidence="Medium",
            review_flag="Partial Apply - Keep AR Open",
            note=(
                f"Partial/installment payment. Apply {cents_to_money(target_gross_cents):,.2f}; "
                f"remaining AR balance is {cents_to_money(referenced_total_cents - target_gross_cents):,.2f}."
            ),
        )

    if scenario == "Overpayment" and target_gross_cents > referenced_total_cents:
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=referenced_total_cents,
            residual_cents=target_gross_cents - referenced_total_cents,
            method="Memo Invoice Match - Overpayment",
            confidence="Medium",
            review_flag="Create Unapplied Cash / Credit Balance",
            note=f"Payment exceeds referenced open AR by {cents_to_money(target_gross_cents - referenced_total_cents):,.2f}.",
        )

    if scenario == "Short Payment" and 0 < target_gross_cents < referenced_total_cents:
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=target_gross_cents,
            residual_cents=referenced_total_cents - target_gross_cents,
            method="Memo Invoice Match - Short Payment",
            confidence="Medium",
            review_flag="Short Pay Review",
            note=(
                f"Customer short-paid by {cents_to_money(referenced_total_cents - target_gross_cents):,.2f}; "
                f"do not fully close until discount/write-off is approved."
            ),
        )

    if scenario == "Small Residual Write-Off" and abs(diff_to_gross) <= SMALL_RESIDUAL_WRITE_OFF_CENTS:
        return MatchResult(
            ar_indexes=[int(i) for i in inv_df.index],
            applied_cents=min(target_gross_cents, referenced_total_cents),
            residual_cents=abs(diff_to_gross),
            method="Memo Invoice Match - Small Residual",
            confidence="Medium",
            review_flag="Small Residual Write-Off Review",
            note=f"Small residual {cents_to_money(abs(diff_to_gross)):,.2f} requires policy-based write-off review.",
        )

    return None


def find_amount_combination_match(
    candidates_df: pd.DataFrame, target_gross_cents: int
) -> MatchResult | None:
    if candidates_df.empty:
        return None

    candidates_df = candidates_df.sort_values(
        [AR_DUE_DATE_COL, AR_INVOICE_DATE_COL, AR_INVOICE_COL], kind="stable"
    )
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

    matched_indexes = find_subset_sum(candidates, target_gross_cents, AMOUNT_TOLERANCE_CENTS)
    if not matched_indexes:
        return None

    matched_total = sum(
        int(v) for v in candidates_df.loc[matched_indexes, "__ar_open_amount_cents"]
    )
    return MatchResult(
        ar_indexes=[int(i) for i in matched_indexes],
        applied_cents=matched_total,
        residual_cents=0,
        method="Customer Currency Amount Combination",
        confidence="Medium",
        review_flag="",
        note="No usable memo invoice reference; matched by customer, currency, and exact gross amount combination.",
    )


# ---------------------------------------------------------------------------
# Unmatched helper
# ---------------------------------------------------------------------------

def append_unmatched(
    unmatched_rows: list[dict[str, object]],
    bank_row: pd.Series,
    reason: str,
    scenario: str = "",
    extracted_invoices: str = "",
    candidate_count: int | None = None,
    candidate_total: float | None = None,
) -> None:
    row: dict[str, object] = {
        "Bank Transaction ID":      bank_row.get(BANK_TRANSACTION_ID_COL, ""),
        "Value Date":               bank_row.get(BANK_VALUE_DATE_COL, ""),
        "Posting Date":             bank_row.get(BANK_POSTING_DATE_COL, ""),
        "Customer Name":            bank_row.get(BANK_CUSTOMER_NAME_COL, ""),
        "Remitter Name":            bank_row.get(BANK_REMITTER_NAME_COL, ""),
        "ERP Customer ID":          bank_row.get(BANK_CUSTOMER_ID_COL, ""),
        "Currency":                 bank_row.get(BANK_CURRENCY_COL, ""),
        "Gross Receipt Amount":     bank_row.get(BANK_GROSS_AMOUNT_COL, ""),
        "Bank Fees":                bank_row.get(BANK_FEES_COL, ""),
        "Net Amount":               bank_row.get(BANK_NET_AMOUNT_COL, ""),
        "Memo / Remittance Detail": bank_row.get(BANK_MEMO_COL, ""),
        "Memo Scenario":            scenario,
        "Extracted Invoice Numbers": extracted_invoices,
        "Reason":                   reason,
    }
    if candidate_count is not None:
        row["Candidate Invoice Count"] = candidate_count
    if candidate_total is not None:
        row["Candidate Open AR Total"] = candidate_total
    unmatched_rows.append(row)


# ---------------------------------------------------------------------------
# FIX: apply_match — writes ONLY to new output columns, never touches source cols
# ---------------------------------------------------------------------------

def apply_match(
    ar_df: pd.DataFrame,
    bank_df: pd.DataFrame,
    bank_index: int,
    bank_row: pd.Series,
    result: MatchResult,
    currency_key: str,
    close_invoice: bool,
) -> tuple[int, int]:
    """
    Write reconciliation results back to dataframes.

    GUARANTEE: Only new output columns are written.
    The following source columns are NEVER modified:
      - AR_OPEN_AMOUNT_COL  ("Open Amount")
      - AR_STATUS_COL       ("AR Status")
      - AR_RECON_STATUS_COL ("Reconciliation Status")
      - BANK_STATUS_COL     ("Status")
    """
    batch_id        = f"AUTO-{bank_row[BANK_TRANSACTION_ID_COL]}"
    invoice_numbers = ar_df.loc[result.ar_indexes, AR_INVOICE_COL].astype(str).tolist()

    suggested_ar_status = (
        "Reconciled"
        if close_invoice and result.review_flag not in {"Partial Apply - Keep AR Open", "Short Pay Review"}
        else "Pending Review"
    )

    # --- Pre-compute per-invoice applied_cents so they sum to result.applied_cents ---
    #
    # Core invariant:
    #   SUM(Applied Amount across all matched AR rows) == Bank Gross Receipt Amount
    #
    # Strategy:
    #   Each invoice gets its own open amount as its share.
    #   The last invoice absorbs any rounding remainder so the total is exact.
    #
    invoice_open_cents_list = [
        int(ar_df.at[idx, "__ar_open_amount_cents"]) for idx in result.ar_indexes
    ]
    total_invoice_open = sum(invoice_open_cents_list)

    if close_invoice:
        # Distribute result.applied_cents proportionally by each invoice open amount.
        # Last invoice absorbs rounding remainder so SUM == result.applied_cents exactly.
        per_invoice_applied: list[int] = []
        running = 0
        for i, open_c in enumerate(invoice_open_cents_list):
            if i == len(invoice_open_cents_list) - 1:
                # Last invoice: remainder ensures exact total
                per_invoice_applied.append(result.applied_cents - running)
            else:
                if total_invoice_open > 0:
                    # Proportional share: invoice_open / total_open * applied_total
                    share = int(round(open_c / total_invoice_open * result.applied_cents))
                else:
                    share = 0
                per_invoice_applied.append(share)
                running += share
        per_invoice_residual = [0] * len(result.ar_indexes)
    else:
        # Partial / short-pay: only ever single-invoice; applied = result.applied_cents
        per_invoice_applied  = [result.applied_cents]
        per_invoice_residual = [max(invoice_open_cents_list[0] - result.applied_cents, 0)]

    # --- Write per-invoice output columns ---
    for i, idx in enumerate(result.ar_indexes):
        applied_cents  = per_invoice_applied[i]
        residual_cents = per_invoice_residual[i]

        ar_df.at[idx, OUT_MATCHED_BANK_TXN_COL]      = bank_row[BANK_TRANSACTION_ID_COL]
        ar_df.at[idx, OUT_MATCHED_PAYMENT_DATE_COL]   = bank_row[BANK_VALUE_DATE_COL]
        ar_df.at[idx, OUT_MATCHED_AMOUNT_COL]         = cents_to_money(applied_cents)
        ar_df.at[idx, OUT_APPLIED_AMOUNT_COL]         = cents_to_money(applied_cents)
        ar_df.at[idx, OUT_RESIDUAL_AMOUNT_COL]        = cents_to_money(residual_cents)
        ar_df.at[idx, OUT_MATCH_METHOD_COL]           = result.method
        ar_df.at[idx, OUT_MATCH_CONFIDENCE_COL]       = result.confidence
        ar_df.at[idx, OUT_REVIEW_FLAG_COL]            = result.review_flag
        ar_df.at[idx, OUT_SUGGESTED_RECON_STATUS_COL] = suggested_ar_status
        ar_df.at[idx, OUT_RECON_NOTES_COL]            = (
            f"{result.method}: bank receipt {bank_row[BANK_TRANSACTION_ID_COL]} "
            f"matched invoice(s) {', '.join(invoice_numbers)}; "
            f"applied {cents_to_money(applied_cents):,.2f} {currency_key}. "
            f"{result.note}"
        )
        # ✅ AR_OPEN_AMOUNT_COL  — NOT touched (original source data preserved)
        # ✅ AR_STATUS_COL       — NOT touched
        # ✅ AR_RECON_STATUS_COL — NOT touched

        # Update the internal working column so subsequent installment payments
        # see the reduced balance, without touching the original Open Amount column.
        if not close_invoice:
            new_working_balance = max(int(ar_df.at[idx, "__ar_open_amount_cents"]) - applied_cents, 0)
            ar_df.at[idx, "__ar_open_amount_cents"] = new_working_balance

    # --- Write bank receipt output columns ---
    suggested_bank_status = (
        "Reconciled" if close_invoice and not result.review_flag else "Pending Review"
    )
    bank_df.at[bank_index, BANK_RECON_BATCH_ID_COL]       = batch_id
    bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL]     = (
        f"{result.method}; matched invoices: {', '.join(invoice_numbers)}. {result.note}"
    )
    bank_df.at[bank_index, OUT_MATCH_METHOD_BANK_COL]     = result.method
    bank_df.at[bank_index, OUT_MATCH_CONFIDENCE_BANK_COL] = result.confidence
    bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = suggested_bank_status
    bank_df.at[bank_index, "AR Applied Amount Total"]      = cents_to_money(result.applied_cents)
    # ✅ BANK_STATUS_COL — NOT touched

    return 1, len(result.ar_indexes)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Reading input workbooks...")
    bank_df = pd.read_excel(BANK_FILE, sheet_name=BANK_SHEET)
    ar_df   = pd.read_excel(AR_FILE,   sheet_name=AR_SHEET)

    require_columns(
        bank_df,
        [
            BANK_TRANSACTION_ID_COL,
            BANK_VALUE_DATE_COL,
            BANK_CUSTOMER_NAME_COL,
            BANK_CUSTOMER_ID_COL,
            BANK_CURRENCY_COL,
            BANK_GROSS_AMOUNT_COL,
            BANK_FEES_COL,
            BANK_NET_AMOUNT_COL,
            BANK_STATUS_COL,
            BANK_MEMO_COL,
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

    ensure_output_columns(
        ar_df,
        [
            OUT_MATCHED_BANK_TXN_COL,
            OUT_MATCHED_PAYMENT_DATE_COL,
            OUT_MATCHED_AMOUNT_COL,
            OUT_APPLIED_AMOUNT_COL,
            OUT_RESIDUAL_AMOUNT_COL,
            OUT_MATCH_METHOD_COL,
            OUT_MATCH_CONFIDENCE_COL,
            OUT_REVIEW_FLAG_COL,
            OUT_SUGGESTED_RECON_STATUS_COL,
            OUT_RECON_NOTES_COL,
        ],
    )
    ensure_output_columns(
        bank_df,
        [
            BANK_RECON_BATCH_ID_COL,
            BANK_PROCESSING_NOTES_COL,
            OUT_EXTRACTED_INVOICES_COL,
            OUT_MEMO_SCENARIO_COL,
            OUT_MATCH_METHOD_BANK_COL,
            OUT_MATCH_CONFIDENCE_BANK_COL,
            OUT_SUGGESTED_BANK_STATUS_COL,
            "AR Applied Amount Total",
        ],
    )

    # --- Compute working columns (prefixed __ so they're easy to drop later) ---
    bank_df["__gross_amount_cents"] = bank_df[BANK_GROSS_AMOUNT_COL].apply(money_to_cents)
    bank_df["__bank_fee_cents"]     = bank_df[BANK_FEES_COL].apply(money_to_cents)
    bank_df["__net_amount_cents"]   = bank_df[BANK_NET_AMOUNT_COL].apply(money_to_cents)
    ar_df["__ar_open_amount_cents"] = ar_df[AR_OPEN_AMOUNT_COL].apply(money_to_cents)

    bank_df["__customer_key"] = bank_df[BANK_CUSTOMER_ID_COL].apply(normalize_key)
    bank_df.loc[bank_df["__customer_key"] == "", "__customer_key"] = (
        bank_df[BANK_CUSTOMER_NAME_COL].apply(normalize_key)
    )
    ar_df["__customer_key"] = ar_df[AR_CUSTOMER_ID_COL].apply(normalize_key)
    ar_df.loc[ar_df["__customer_key"] == "", "__customer_key"] = (
        ar_df[AR_CUSTOMER_NAME_COL].apply(normalize_key)
    )

    bank_df["__currency_key"] = bank_df[BANK_CURRENCY_COL].apply(lambda x: normalize_text(x).upper())
    ar_df["__currency_key"]   = ar_df[AR_CURRENCY_COL].apply(lambda x: normalize_text(x).upper())
    ar_df["__invoice_key"]    = ar_df[AR_INVOICE_COL].apply(normalize_invoice)

    bank_df[OUT_EXTRACTED_INVOICES_COL] = bank_df[BANK_MEMO_COL].apply(
        lambda x: ", ".join(extract_invoice_numbers(x))
    )
    bank_df[OUT_MEMO_SCENARIO_COL] = bank_df[BANK_MEMO_COL].apply(classify_memo)

    # --- Data quality check (read-only) ---
    unmatched_bank_rows: list[dict[str, object]] = []
    data_quality_rows:   list[dict[str, object]] = []
    matched_receipt_count  = 0
    matched_invoice_count  = 0
    pending_review_count   = 0

    bad_ar_mask = (
        (ar_df["__ar_open_amount_cents"] != 0)
        & (
            ar_df[AR_STATUS_COL].fillna("").str.strip().str.lower().isin({"closed"})
            | ar_df[AR_RECON_STATUS_COL].fillna("").str.strip().str.lower().isin(
                {"reconciled", "matched", "closed"}
            )
        )
    )
    for idx, row in ar_df.loc[bad_ar_mask].iterrows():
        data_quality_rows.append(
            {
                "Issue":                "Closed/reconciled AR row has non-zero Open Amount",
                "AR Row Index":         idx,
                "ERP Customer ID":      row.get(AR_CUSTOMER_ID_COL, ""),
                "Customer Name":        row.get(AR_CUSTOMER_NAME_COL, ""),
                "Invoice Number":       row.get(AR_INVOICE_COL, ""),
                "Open Amount":          row.get(AR_OPEN_AMOUNT_COL, ""),
                "AR Status":            row.get(AR_STATUS_COL, ""),
                "Reconciliation Status": row.get(AR_RECON_STATUS_COL, ""),
                "Recommendation":       "Exclude from auto-match and review data sync/source mapping.",
            }
        )

    bank_df = bank_df.sort_values(
        [BANK_VALUE_DATE_COL, BANK_TRANSACTION_ID_COL], kind="stable"
    )

    # --- Main reconciliation loop ---
    print("Starting reconciliation...")
    for bank_index, bank_row in bank_df.iterrows():
        # Skip rows already reconciled in source data (read-only check)
        bank_status = normalize_text(bank_row[BANK_STATUS_COL]).lower()
        if bank_status in {"reconciled", "matched", "closed"}:
            continue

        target_gross_cents = int(bank_row["__gross_amount_cents"])
        bank_fee_cents     = int(bank_row["__bank_fee_cents"])
        target_net_cents   = int(bank_row["__net_amount_cents"])
        scenario           = normalize_text(bank_row[OUT_MEMO_SCENARIO_COL])
        invoice_numbers    = extract_invoice_numbers(bank_row[BANK_MEMO_COL])
        extracted_invoices = ", ".join(invoice_numbers)

        if target_gross_cents <= 0 or target_net_cents < 0:
            reason = "Negative or zero receipt amount; route to reversal/refund/chargeback review."
            append_unmatched(unmatched_bank_rows, bank_row, reason, scenario, extracted_invoices)
            bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL]     = reason
            bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = "Pending Review"
            pending_review_count += 1
            continue

        if target_gross_cents - bank_fee_cents != target_net_cents:
            reason = "Gross Receipt Amount minus Bank Fees does not equal Net Amount."
            append_unmatched(unmatched_bank_rows, bank_row, reason, scenario, extracted_invoices)
            bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL]     = reason
            bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = "Pending Review"
            pending_review_count += 1
            continue

        if scenario in REVIEW_ONLY_SCENARIOS:
            reason = f"{scenario}: not eligible for full auto-reconciliation."
            append_unmatched(unmatched_bank_rows, bank_row, reason, scenario, extracted_invoices)
            bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL]     = reason
            bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = "Pending Review"
            pending_review_count += 1
            continue

        match_result = find_invoice_reference_match(
            ar_df=ar_df,
            invoice_numbers=invoice_numbers,
            scenario=scenario,
            target_gross_cents=target_gross_cents,
            target_net_cents=target_net_cents,
            bank_fee_cents=bank_fee_cents,
        )

        if match_result is None:
            customer_key = bank_row["__customer_key"]
            currency_key = bank_row["__currency_key"]
            ar_mask      = build_candidate_mask(ar_df, customer_key, currency_key)
            candidates_df = ar_df.loc[ar_mask].copy()

            if candidates_df.empty:
                reason = "No open AR candidates found for the same ERP Customer ID/customer name and currency."
                append_unmatched(unmatched_bank_rows, bank_row, reason, scenario, extracted_invoices)
                bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL]     = reason
                bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = "Exception"
                continue

            match_result = find_amount_combination_match(candidates_df, target_gross_cents)
            if match_result is None:
                reason = "No exact invoice reference or customer/currency amount combination equals the gross receipt amount."
                append_unmatched(
                    unmatched_bank_rows,
                    bank_row,
                    reason,
                    scenario,
                    extracted_invoices,
                    candidate_count=len(candidates_df),
                    candidate_total=cents_to_money(int(candidates_df["__ar_open_amount_cents"].sum())),
                )
                bank_df.at[bank_index, BANK_PROCESSING_NOTES_COL]     = reason
                bank_df.at[bank_index, OUT_SUGGESTED_BANK_STATUS_COL] = "Exception"
                continue

        close_invoice = (
            match_result.residual_cents == 0
            and match_result.review_flag not in {"Partial Apply - Keep AR Open", "Short Pay Review"}
        )
        receipt_added, invoice_added = apply_match(
            ar_df=ar_df,
            bank_df=bank_df,
            bank_index=bank_index,
            bank_row=bank_row,
            result=match_result,
            currency_key=bank_row["__currency_key"],
            close_invoice=close_invoice,
        )
        matched_receipt_count += receipt_added
        matched_invoice_count += invoice_added
        if match_result.review_flag:
            pending_review_count += 1

    # --- Drop internal working columns before output ---
    helper_cols = [
        "__gross_amount_cents",
        "__bank_fee_cents",
        "__net_amount_cents",
        "__ar_open_amount_cents",
        "__customer_key",
        "__currency_key",
        "__invoice_key",
        "__memo_order",
    ]
    ar_output   = ar_df.drop(columns=[c for c in helper_cols if c in ar_df.columns],   errors="ignore")
    bank_output = bank_df.drop(columns=[c for c in helper_cols if c in bank_df.columns], errors="ignore")

    unmatched_df    = pd.DataFrame(unmatched_bank_rows)
    data_quality_df = pd.DataFrame(data_quality_rows)

    summary_df = pd.DataFrame(
        [
            ["Matched bank receipts",                matched_receipt_count],
            ["Matched AR invoice rows",              matched_invoice_count],
            ["Pending review receipts",              pending_review_count],
            ["Unmatched / exception bank receipts",  len(unmatched_bank_rows)],
            ["Data quality issues",                  len(data_quality_rows)],
            ["Output file",                          OUTPUT_FILE.name],
        ],
        columns=["Metric", "Value"],
    )

    print(f"Matched bank receipts              : {matched_receipt_count}")
    print(f"Matched AR invoice rows            : {matched_invoice_count}")
    print(f"Pending review receipts            : {pending_review_count}")
    print(f"Unmatched / exception bank receipts: {len(unmatched_bank_rows)}")
    print(f"Data quality issues                : {len(data_quality_rows)}")
    print(f"Writing output workbook            : {OUTPUT_FILE.name}")

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        ar_output.to_excel(       writer, index=False, sheet_name="Reconciled Open AR")
        bank_output.to_excel(     writer, index=False, sheet_name="Processed Bank Receipts")
        unmatched_df.to_excel(    writer, index=False, sheet_name="Unmatched Bank Receipts")
        data_quality_df.to_excel( writer, index=False, sheet_name="Data Quality Checks")
        summary_df.to_excel(      writer, index=False, sheet_name="Summary")

    print("Done.")


if __name__ == "__main__":
    main()