"""
The column vocabulary shared by the engine and the agent.

Both sides have to name the same columns or they will not agree about what
they are looking at, so every column name lives here once.  The column
names themselves are part of the business contract: they are what the
workbook contains and what a reviewer reads, so they must not change.
"""

from __future__ import annotations

__all__ = [
    # Bank source columns (read-only)
    "BANK_TRANSACTION_ID_COL",
    "BANK_ACCOUNT_COL",
    "BANK_VALUE_DATE_COL",
    "BANK_POSTING_DATE_COL",
    "BANK_CUSTOMER_NAME_COL",
    "BANK_REMITTER_NAME_COL",
    "BANK_CUSTOMER_ID_COL",
    "BANK_CURRENCY_COL",
    "BANK_GROSS_AMOUNT_COL",
    "BANK_FEES_COL",
    "BANK_NET_AMOUNT_COL",
    "BANK_STATUS_COL",
    "BANK_EXCEPTION_TYPE_COL",
    "BANK_MEMO_COL",
    # AR source columns (read-only)
    "AR_CUSTOMER_ID_COL",
    "AR_PARENT_CUSTOMER_ID_COL",
    "AR_CUSTOMER_NAME_COL",
    "AR_INVOICE_COL",
    "AR_INVOICE_DATE_COL",
    "AR_DUE_DATE_COL",
    "AR_CURRENCY_COL",
    "AR_OPEN_AMOUNT_COL",
    "AR_STATUS_COL",
    "AR_RECON_STATUS_COL",
    "AR_DISPUTE_REASON_COL",
    # AR output columns
    "OUT_MATCHED_BANK_TXN_COL",
    "OUT_MATCHED_PAYMENT_DATE_COL",
    "OUT_MATCHED_AMOUNT_COL",
    "OUT_APPLIED_AMOUNT_COL",
    "OUT_RESIDUAL_AMOUNT_COL",
    "OUT_MATCH_METHOD_COL",
    "OUT_RECON_NOTES_COL",
    "OUT_SUGGESTED_RECON_STATUS_COL",
    "AR_MATCHED_TXN_COL",
    "AR_RESIDUAL_AMOUNT_COL",
    "AR_APPLIED_AMOUNT_COL",
    # Bank output columns
    "BANK_RECON_BATCH_ID_COL",
    "BANK_PROCESSING_NOTES_COL",
    "OUT_EXTRACTED_INVOICES_COL",
    "OUT_MEMO_SCENARIO_COL",
    "OUT_MATCH_METHOD_BANK_COL",
    "OUT_SUGGESTED_BANK_STATUS_COL",
    "BANK_APPLIED_AMOUNT_TOTAL_COL",
    "BANK_UNAPPLIED_AMOUNT_COL",
    "BANK_ADJUSTMENT_AMOUNT_COL",
    "BANK_HELD_AMOUNT_COL",
    # Review sheet columns
    "REVIEW_BANK_TXN_COL",
    "REVIEW_RECOMMENDED_ACTION_COL",
    "REVIEW_CONFIDENCE_COL",
    "REVIEW_EXCEPTION_TYPE_COL",
    "REVIEW_ROOT_CAUSE_COL",
    "REVIEW_PROPOSED_INVOICES_COL",
    "REVIEW_PROPOSED_AMOUNT_COL",
    "REVIEW_PROPOSED_RESIDUAL_COL",
    "REVIEW_CANDIDATES_COL",
    "REVIEW_ACCOUNTING_TREATMENT_COL",
    "REVIEW_GL_POSTING_COL",
    "REVIEW_EXPLANATION_COL",
    "REVIEW_ENGINE_COL",
    "REVIEW_APPROVAL_COL",
    "REVIEW_REVIEWER_NOTES_COL",
    "REVIEW_APPLIED_COL",
    # Unmatched sheet columns
    "UNMATCHED_REASON_COL",
    "UNMATCHED_CANDIDATE_COUNT_COL",
    "UNMATCHED_CANDIDATE_TOTAL_COL",
    "UNMATCHED_SUGGESTED_INVOICES_COL",
    "UNMATCHED_SUGGESTED_OPEN_COL",
    "UNMATCHED_DIFFERENCE_COL",
    # Log sheet columns
    "LOG_TXN_COL",
    "LOG_OUTCOME_COL",
    "LOG_DETAIL_COL",
]

# ===== Bank file columns (read-only) =====
BANK_TRANSACTION_ID_COL = "Bank Transaction ID"
BANK_ACCOUNT_COL = "Bank Account"
BANK_VALUE_DATE_COL = "Value Date"
BANK_POSTING_DATE_COL = "Posting Date"
BANK_CUSTOMER_NAME_COL = "Customer Name"
BANK_REMITTER_NAME_COL = "Remitter Name"
BANK_CUSTOMER_ID_COL = "ERP Customer ID"
BANK_CURRENCY_COL = "Currency"
BANK_GROSS_AMOUNT_COL = "Gross Receipt Amount"
BANK_FEES_COL = "Bank Fees"
BANK_NET_AMOUNT_COL = "Net Amount"
BANK_STATUS_COL = "Status"  # read-only source column
BANK_EXCEPTION_TYPE_COL = "Exception Type"
BANK_MEMO_COL = "Memo / Remittance Detail"

# ===== Open AR file columns (read-only) =====
AR_CUSTOMER_ID_COL = "ERP Customer ID"
AR_PARENT_CUSTOMER_ID_COL = "Parent Customer ID"
AR_CUSTOMER_NAME_COL = "Customer Name"
AR_INVOICE_COL = "Invoice Number"
AR_INVOICE_DATE_COL = "Invoice Date"
AR_DUE_DATE_COL = "Due Date"
AR_CURRENCY_COL = "Currency"
AR_OPEN_AMOUNT_COL = "Open Amount"  # read-only source column
AR_STATUS_COL = "AR Status"  # read-only source column
AR_RECON_STATUS_COL = "Reconciliation Status"  # read-only source column
AR_DISPUTE_REASON_COL = "Dispute Reason"

# ===== Output columns written to Open AR (never overwrite source cols) =====
OUT_MATCHED_BANK_TXN_COL = "Matched Bank Transaction ID"
OUT_MATCHED_PAYMENT_DATE_COL = "Matched Payment Date"
OUT_MATCHED_AMOUNT_COL = "Matched Amount"
OUT_APPLIED_AMOUNT_COL = "Applied Amount"
OUT_RESIDUAL_AMOUNT_COL = "Residual Amount"
OUT_MATCH_METHOD_COL = "Match Method"
OUT_RECON_NOTES_COL = "Reconciliation Notes"
OUT_SUGGESTED_RECON_STATUS_COL = "Suggested Recon Status"

#: Aliases the agent uses for the same columns.
AR_MATCHED_TXN_COL = OUT_MATCHED_BANK_TXN_COL
AR_RESIDUAL_AMOUNT_COL = OUT_RESIDUAL_AMOUNT_COL
AR_APPLIED_AMOUNT_COL = OUT_APPLIED_AMOUNT_COL

# ===== Output columns written to bank receipt =====
BANK_RECON_BATCH_ID_COL = "Reconciliation Batch ID"
BANK_PROCESSING_NOTES_COL = "Processing Notes"
OUT_EXTRACTED_INVOICES_COL = "Extracted Invoice Numbers"
OUT_MEMO_SCENARIO_COL = "Memo Scenario"
OUT_MATCH_METHOD_BANK_COL = "Match Method"
OUT_SUGGESTED_BANK_STATUS_COL = "Suggested Bank Status"
BANK_APPLIED_AMOUNT_TOTAL_COL = "AR Applied Amount Total"
#: Cash received beyond the AR a receipt settled (an overpayment).  It stays
#: on account until a reviewer decides what to do with it.
BANK_UNAPPLIED_AMOUNT_COL = "Unapplied Amount"
#: Part of the gross receipt that is neither applied to AR nor unapplied
#: cash: a bank fee deducted before AR was cleared at net, or a signed penny
#: rounding variance.
BANK_ADJUSTMENT_AMOUNT_COL = "Fee / Rounding Adjustment"
#: Gross amount of a receipt that was not applied at all and waits in review.
BANK_HELD_AMOUNT_COL = "Held For Review Amount"

# ===== AI Exception Review sheet columns =====
REVIEW_BANK_TXN_COL = "Bank Transaction ID"
REVIEW_RECOMMENDED_ACTION_COL = "Recommended Action"
REVIEW_CONFIDENCE_COL = "AI Confidence"
REVIEW_EXCEPTION_TYPE_COL = "AI Exception Type"
REVIEW_ROOT_CAUSE_COL = "Root Cause"
REVIEW_PROPOSED_INVOICES_COL = "Proposed Invoice Numbers"
REVIEW_PROPOSED_AMOUNT_COL = "Proposed Applied Amount"
REVIEW_PROPOSED_RESIDUAL_COL = "Proposed Residual Amount"
REVIEW_CANDIDATES_COL = "Candidate Invoices Considered"
REVIEW_ACCOUNTING_TREATMENT_COL = "Accounting Treatment"
REVIEW_GL_POSTING_COL = "Proposed GL Posting"
REVIEW_EXPLANATION_COL = "AI Explanation"
REVIEW_ENGINE_COL = "Analysed By"
REVIEW_APPROVAL_COL = "Approval"
REVIEW_REVIEWER_NOTES_COL = "Reviewer Notes"
REVIEW_APPLIED_COL = "Applied To AR"

# ===== Unmatched Bank Receipts sheet columns =====
UNMATCHED_REASON_COL = "Reason"
UNMATCHED_CANDIDATE_COUNT_COL = "Candidate Invoice Count"
UNMATCHED_CANDIDATE_TOTAL_COL = "Candidate Open AR Total"
UNMATCHED_SUGGESTED_INVOICES_COL = "Suggested Invoices"
UNMATCHED_SUGGESTED_OPEN_COL = "Suggested Open Amount"
UNMATCHED_DIFFERENCE_COL = "Receipt vs Open Difference"

# ===== AI Exception Log sheet columns =====
LOG_TXN_COL = "Bank Transaction ID"
LOG_OUTCOME_COL = "Outcome"
LOG_DETAIL_COL = "Detail"
