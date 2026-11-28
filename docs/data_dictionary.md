# Data Dictionary

This document defines the fields used by the sample input and output files for the **North America AR Cash Application Automation** project.

The project uses two primary input workbooks, in `data/input/`:

1. `Bank_Cash_Receipts.xlsx`
2. `Open_AR_Detail.xlsx`

The automation produces one output workbook, in `data/output/`:

1. `Open_AR_Detail_Reconciled_Result.xlsx`

## 1. Bank_Cash_Receipts.xlsx

### Sheet: `Bank Receipts`

This sheet represents bank cash receipt activity, similar to a bank feed, lockbox file, or treasury cash receipt extract.

| Column Name | Type | Description | Used by Automation |
|---|---|---|---|
| Bank Transaction ID | Text | Unique identifier for the bank transaction. | Yes. Used as the receipt identifier and reconciliation batch reference. |
| Bank Account | Text | Bank account where the cash was received. | Yes. Used for bank account mismatch scenarios. |
| Value Date | Date | Date when the bank considers the cash available. | Yes. Written to matched AR as payment date. |
| Posting Date | Date | Date when the transaction was posted by the bank or treasury system. | Yes. Used for reporting and review. |
| ERP Customer ID | Text | Customer identifier from the ERP or customer master. | Yes. Primary customer matching key. |
| Customer Name | Text | Legal or ERP customer name. | Yes. Fallback customer matching key when ERP Customer ID is missing. |
| Remitter Name | Text | Name shown in the bank remittance or payment file. | Yes. Useful for DBA, alias, and missing customer ID scenarios. |
| Bank Reference | Text | Bank reference, trace number, wire ID, ACH ID, check number, or lockbox reference. | Optional. Useful for manual review and audit trail. |
| Lockbox ID | Text | Lockbox identifier, if payment was received through lockbox. | Optional. Used for lockbox batch scenarios. |
| Payment Method | Text | Payment method such as ACH, Wire, Check, Lockbox, or Card. | Optional. Useful for review and reporting. |
| Currency | Text | Currency code such as USD, CAD, or EUR. | Yes. Matching is only allowed within the same currency. |
| Gross Receipt Amount | Number | Full amount paid by the customer before bank fees. | Yes. Used to clear AR open amounts. |
| Bank Fees | Number | Fees deducted by the bank or payment processor. | Yes. Used to validate `Gross Receipt Amount - Bank Fees = Net Amount`. |
| Net Amount | Number | Cash amount actually received after bank fees. | Yes. Used for bank cash reconciliation. |
| Memo / Remittance Detail | Text | Customer memo, remittance advice, invoice references, PO references, or payment notes. | Yes. Used to extract invoice numbers and classify scenarios. |
| Status | Text | Processing status of the bank receipt. Example: New, Reconciled, Pending Review, Exception. | Yes. Updated by automation. |
| Exception Type | Text | Optional exception category from source file or manual review. | Optional. Useful for analysis. |
| Reconciliation Batch ID | Text | Batch or run identifier created during reconciliation. | Yes. Updated by automation. |
| Notes | Text | General notes from source file. | Optional. Preserved for review. |
| Scenario ID | Text | Synthetic scenario identifier from S01 to S40. | Optional. Used for sample data testing and documentation. |

### Bank Receipt Output Columns

The script may add or update the following columns in the processed bank receipt output:

| Column Name | Description |
|---|---|
| Processing Notes | Explanation of how the receipt was handled or why it was routed to review. |
| Extracted Invoice Numbers | Invoice numbers parsed from `Memo / Remittance Detail`. |
| Memo Scenario | Classified scenario based on remittance text, such as Bank Fee, Short Payment, Refund, or FX Exception. |
| Match Method | Matching method used for the receipt. |
| Suggested Bank Status | Suggested status for the receipt: `Reconciled`, `Pending Review`, or `Exception`. The source `Status` column is never modified. |
| AR Applied Amount Total | Total cash applied to AR by this receipt. Used for the AR/bank control total. |
| Unapplied Amount | Cash received beyond the AR the receipt settled (an overpayment). It stays on account for review. |
| Fee / Rounding Adjustment | Part of the gross receipt that is neither applied to AR nor unapplied cash: a bank fee deducted before AR was cleared at net, or a signed penny rounding variance. |
| Held For Review Amount | Gross amount of a receipt that was not applied at all and is waiting in review (unmatched, ambiguous, duplicate, held by the input integrity checks). |
| AI-Resolved Applied Amount | Cash applied by an approved evidence-based resolution. The engine's own result is `AR Applied Amount Total` minus this column. |
| Resolution ID | The approved resolution applied to the receipt (`RES-…`). Set once; a second attempt to apply the same receipt is skipped. |
| Supporting Documents | Document IDs (`DOC-…`) of the evidence the approved resolution relied on. |

## 2. Open_AR_Detail.xlsx

### Sheet: `Open AR`

This sheet represents the open Accounts Receivable detail from an ERP system.

| Column Name | Type | Description | Used by Automation |
|---|---|---|---|
| ERP Customer ID | Text | Customer identifier from ERP customer master. | Yes. Primary customer matching key. |
| Parent Customer ID | Text | Parent account or corporate group identifier. | Optional. Useful for parent-child customer payment scenarios. |
| Customer Name | Text | Customer legal or ERP name. | Yes. Fallback matching key when ERP Customer ID is missing. |
| Invoice Number | Text | Unique customer invoice number. | Yes. Used for memo invoice-reference matching. |
| Invoice Date | Date | Date when the invoice was issued. | Yes. Used for sorting candidate invoices. |
| Due Date | Date | Invoice due date. | Yes. Used for aging and sorting candidate invoices. |
| PO Number | Text | Customer purchase order number. | Optional. Useful for PO-only remittance review. |
| Currency | Text | Invoice currency such as USD, CAD, or EUR. | Yes. Matching is only allowed within the same currency. |
| Invoice Original Amount | Number | Original invoice amount at invoice issuance. | Optional. Preserved for context. |
| Open Amount | Number | Remaining unpaid AR balance. | Yes. Main AR amount used in matching. |
| Aging Bucket | Text | Aging category such as Current, 0-30, 31-60, 61-90, or 90+. | Optional. Useful for review and reporting. |
| Business Unit | Text | Business unit or division associated with the invoice. | Optional. Useful for mixed business unit allocation review. |
| Region | Text | Customer or business region. | Optional. Useful for reporting. |
| Collector | Text | AR collector or collection owner. | Optional. Useful for collector hold and review workflow. |
| AR Status | Text | Invoice status, such as Open, Closed, Disputed, or Hold. | Yes. Non-open invoices are excluded from normal auto-match. |
| Dispute Reason | Text | Reason for dispute if the invoice is disputed. | Optional. Used for dispute review context. |
| Reconciliation Status | Text | Reconciliation status, such as Open, Reconciled, Matched, or Closed. | Yes. Already reconciled rows are excluded from auto-match. |
| Matched Bank Transaction ID | Text | Bank transaction ID matched to the invoice. | Yes. Updated by automation. |
| Matched Payment Date | Date | Payment date from the matched bank receipt. | Yes. Updated by automation. |
| Matched Amount | Number | Bank receipt amount matched to the invoice or invoice group. | Yes. Updated by automation. |
| Reconciliation Notes | Text | Explanation of reconciliation result. | Yes. Updated by automation. |
| Scenario ID | Text | Synthetic scenario identifier from S01 to S40. | Optional. Used for sample data testing and documentation. |

### Open AR Output Columns

The script may add or update the following columns in the reconciled AR output:

| Column Name | Description |
|---|---|
| Applied Amount | Cumulative amount applied from bank receipts to the AR item. Accumulates when an invoice is paid by several instalments. |
| Residual Amount | Remaining open balance after applying cash, or the excess amount on an overpayment. |
| Match Method | Method used to match the invoice, such as Memo Invoice Match or Customer Currency Amount Combination. |
| Suggested Recon Status | Suggested status for the AR item: `Reconciled`, `Pending Review`, or `Unmatched`. The source `Reconciliation Status` column is never modified. |
| Reconciliation Notes | Detailed explanation of the matching result. |
| AI-Resolved Applied Amount | Cash applied to the invoice by approved evidence-based resolutions. |
| Approved Adjustment Amount | Non-cash credit approved with a resolution: an Early Payment Discount or a Customer Deduction (decision E1). Reduces the open balance; never part of the cash control. |
| Approved Adjustment Type | `Early Payment Discount` and/or `Customer Deduction`. |
| Resolution ID | The approved resolution(s) applied to the invoice. |

## 3. Output Workbook

### File: `Open_AR_Detail_Reconciled_Result.xlsx`

The automation creates a new workbook instead of overwriting the source files.

| Sheet Name | Description |
|---|---|
| Reconciled Open AR | Open AR detail after reconciliation processing. Contains updated matching, status, applied amount, residual amount, and notes. |
| Processed Bank Receipts | Bank receipt detail after processing. Contains updated receipt status, extracted invoice numbers, memo scenario, match method, and processing notes. |
| Unmatched Bank Receipts | Bank receipts that could not be auto-matched or were routed to exception review. |
| Data Quality Checks | Data quality issues found in the AR data, such as closed or reconciled invoices with non-zero open amount. |
| Summary | Key run metrics such as matched receipts, matched invoices, pending review receipts, exceptions, and data quality issue count, followed by the controls. **Cash control:** total gross receipts = applied to AR + unapplied cash + fee/rounding adjustments + held for review + already reconciled in source, checked per receipt. **AR roll-forward:** open AR before the run − applied in this run = open AR remaining, checked per invoice. The run writes nothing if either control breaks. |
| AI Exception Review | Written by `src/ar_reconciliation/agent/`. One row per unmatched receipt with the agent's exception type, root cause, recommended action, confidence, proposed invoices and amounts, accounting treatment, proposed GL posting, explanation, and the reviewer's `Approval` decision. |
| AI Exception Log | Written by `src/ar_reconciliation/agent/`. One row per validation repair, model error or apply outcome, so every change the agent made to a recommendation is auditable. |
| Supporting Documents | One row per stored document: Document ID, file name, type, size, SHA-256, upload time and uploader, linked bank transactions, parse status, and whether the preserved original still matches its hash. |
| Extracted Evidence | One row per fact read from a document: receipt, document, field (invoice, allocation, discount terms, deduction, …), value, invoice, amount, currency, location (page/line/row) and the verbatim quote. |
| Approval Records | One row per reviewer decision processed: decision (APPROVE / REJECT / MORE_EVIDENCE), reviewer, decision timestamp, comments, approved allocations and adjustments, outcome. Every approval attempt is kept, including repeats ("Already applied"). |
| Audit Trail | Append-only. One row per balance change: timestamp, resolution, receipt, invoice, customer, open balance before, cash applied, adjustment, open balance after, documents, reviewer. Survives a re-run of the engine. |
| Control Breaks | Only in a `…_FAILED.xlsx` diagnostic workbook: every receipt or invoice that does not tie. |

### Summary controls (evidence-based resolution)

The Summary sheet starts its control block with **Run Status** (`FINAL`, or
`FAILED` in a diagnostic workbook). The AR roll-forward reads
`Open AR before this run − applied cash − approved non-cash adjustments = Open AR remaining`.
The cash control, the bank-vs-AR applied check and the roll-forward are then
repeated **per currency** (`USD …`, `CAD …`, `EUR …`); the all-currency lines are
arithmetic totals shown for completeness. After approvals are applied, an
**AI-ASSISTED RESOLUTION** block reports the resolutions applied, the
AI-resolved cash, the approved adjustments and the receipts still in Needs Review.

### AI Exception Review — evidence columns

| Column | Description |
|---|---|
| Supporting Documents | The documents linked to the receipt. Blank when there are none; the row is then exactly as before. |
| Evidence Summary | The extracted facts, one per line, with their document and location. |
| Evidence Citations | The document lines the proposal relies on, each verified to exist in the stored document. |
| Evidence Conflicts | Disagreements between documents (amounts, discount terms, currency). |
| Evidence Sufficiency | `Sufficient`, `Insufficient` or `Conflicting`. |
| Proposed Allocations | Cash per invoice, e.g. `INV-1: 1,000.00; INV-2: 300.00`. A reviewer may edit it; it is revalidated before anything is applied. |
| Proposed Adjustments | Non-cash adjustments, e.g. `INV-2: Early Payment Discount 40.00`. |
| Validation Result | `Valid`, `Downgraded` (an unsupported adjustment or citation was dropped), `Rejected` (a cash check failed) or `Needs Review`. |
| Validation Notes | Why each check passed, repaired or failed. |
| Resolution ID | Identifies the exact proposal (receipt + allocations + adjustments). |
| Approval | `APPROVE`, `REJECT` or `MORE_EVIDENCE`. |
| Reviewer / Decision Timestamp | Who decided, and when. |

## 4. Key Business Definitions

| Term | Definition |
|---|---|
| AR | Accounts Receivable. Amounts owed by customers. |
| Open AR | AR that has not yet been fully paid or cleared. |
| Cash Application | The process of applying customer payments to open AR invoices. |
| Gross Receipt Amount | Total customer payment amount before bank fees. Used to clear AR. |
| Bank Fees | Fees withheld by the bank or payment processor. |
| Net Amount | Cash actually received after bank fees. Used for bank cash reconciliation. |
| Remittance Detail | Payment information provided by the customer, often containing invoice numbers or PO numbers. |
| Subset-Sum Matching | Matching logic that finds whether multiple invoices add up to one bank receipt amount. |
| Pending Review | A receipt or invoice that needs manual review before final reconciliation. |
| Exception | A receipt or invoice that cannot be safely auto-reconciled. |
| Supporting Document | A contract, customer email or remittance advice uploaded for a receipt in Needs Review. Stored unchanged and read-only; its content is treated as data, never as instructions. |
| Resolution | The proposed cash allocations (and any approved non-cash adjustments) for one receipt, validated deterministically and applied only after a reviewer approves it. |
| Third-Party Payment | A payment made by one customer for another customer's invoice. Applied only when a document names the invoice customer and a reviewer approves (decision E2). |
