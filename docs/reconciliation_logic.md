# Reconciliation Logic

This document explains the matching and exception-handling logic used by the **North America AR Cash Application Automation** project.

The automation is designed for local Excel-based Accounts Receivable cash application and bank receipt reconciliation. It reads bank receipt data and open AR detail, applies matching rules, and generates a new reconciled output workbook.

## 1. Input Files

The automation expects the following files in the same folder as the Python script:

```text
Bank_Cash_Receipts.xlsx
Open_AR_Detail.xlsx
auto_reconcile_na.py
```

Expected input sheets:

| Workbook | Sheet Name | Purpose |
|---|---|---|
| Bank_Cash_Receipts.xlsx | Bank Receipts | Bank cash receipt / remittance data. |
| Open_AR_Detail.xlsx | Open AR | Open customer invoice balances from AR. |

## 2. Output File

The automation creates the following output workbook:

```text
Open_AR_Detail_Reconciled_Result.xlsx
```

Expected output sheets:

| Sheet Name | Purpose |
|---|---|
| Reconciled Open AR | AR detail with updated reconciliation results. |
| Processed Bank Receipts | Bank receipts with updated processing results. |
| Unmatched Bank Receipts | Receipts that were not matched or require manual review. |
| Data Quality Checks | AR data quality issues identified during processing. |
| Summary | Run-level metrics and output file name. |

## 3. Core Amount Logic

The automation distinguishes between AR clearing and bank cash reconciliation.

| Amount Field | Source | Purpose |
|---|---|---|
| Gross Receipt Amount | Bank receipt file | Used to clear open AR invoices. |
| Bank Fees | Bank receipt file | Used to explain the difference between gross cash paid by the customer and net cash received by the bank. |
| Net Amount | Bank receipt file | Used for bank cash reconciliation. |
| Open Amount | Open AR file | Remaining AR amount to be cleared. |

The key validation rule is:

```text
Gross Receipt Amount - Bank Fees = Net Amount
```

If this validation fails, the receipt is routed to review instead of being auto-reconciled.

## 4. Customer and Currency Matching

### Customer Matching

The primary customer matching key is:

```text
ERP Customer ID
```

If `ERP Customer ID` is missing in the bank receipt file, the automation can fall back to normalized `Customer Name` or `Remitter Name`, depending on available data and business rules.

### Currency Matching

The automation only matches receipts and invoices within the same currency.

For example:

```text
USD receipt -> USD invoices only
CAD receipt -> CAD invoices only
EUR receipt -> EUR invoices only
```

A currency mismatch is routed to exception review.

## 5. Matching Priority

The automation uses the following matching sequence.

### Priority 1: Memo Invoice Reference Match

The script first parses invoice numbers from `Memo / Remittance Detail`.

Example memo:

```text
Payment for INV-900001 and INV-900002
```

Extracted invoice numbers:

```text
INV-900001
INV-900002
```

If the referenced invoices are open and their total open amount equals the bank `Gross Receipt Amount`, the receipt is matched with high confidence.

Expected outcome:

```text
Match Method = Memo Invoice Match
Match Confidence = High
```

### Priority 2: Memo Invoice Match with Special Scenario Handling

If the memo includes invoice references but the amount does not match exactly, the automation checks for known scenarios.

| Scenario | Handling |
|---|---|
| Bank Fee | Clear AR at gross amount, reconcile bank at net amount, and flag fee for GL review. |
| Rounding Variance | Match if within tolerance and flag rounding write-off. |
| Partial Payment | Apply available cash and keep the residual invoice balance open. |
| Installment Payment | Apply cash as an installment and keep remaining AR open. |
| Short Payment | Apply cash and route residual balance to short-pay review. |
| Overpayment | Apply to referenced AR and route excess to unapplied cash or credit balance review. |
| Small Residual Write-Off | Flag for write-off review based on policy threshold. |

### Priority 3: Customer + Currency + Amount Combination Match

If no usable invoice reference exists, the automation attempts amount-combination matching.

The match candidate pool is limited to:

```text
same ERP Customer ID or fallback customer key
same Currency
open AR rows only
positive Open Amount only
not already Reconciled, Matched, or Closed
```

The script then uses subset-sum logic to determine whether one or more invoices add up to the bank `Gross Receipt Amount`.

Example:

```text
Bank receipt: 18,500.00 USD

Open invoices:
INV-900145  12,000.00 USD
INV-900146   6,500.00 USD
INV-900147   8,000.00 USD

Matched combination:
12,000.00 + 6,500.00 = 18,500.00
```

Expected outcome:

```text
Match Method = Customer Currency Amount Combination
Match Confidence = Medium
```

### Priority 4: Exception Review

If no safe match is found, the receipt is written to the `Unmatched Bank Receipts` sheet.

Common exception reasons include:

- No open AR candidates found
- No exact amount combination found
- Missing or unusable customer ID
- Currency mismatch
- Ambiguous invoice references
- Risky memo scenario
- Negative or zero receipt amount
- Gross / fee / net amount validation failure

## 6. Auto-Close Policy

The automation only closes AR invoices when the match is clean.

An invoice can be automatically closed when:

1. The match is successful.
2. Residual amount is zero.
3. The invoice is open.
4. The receipt is not a risky review-only scenario.
5. The review flag does not indicate partial payment or short pay review.

Expected output:

```text
AR Status = Closed
Reconciliation Status = Reconciled
```

## 7. Partial and Residual Balance Policy

The automation should not automatically close invoices with unresolved residual balances.

### Partial Payment

Expected output:

```text
AR Status = Open
Reconciliation Status = Open
Applied Amount = amount paid
Residual Amount = remaining unpaid amount
Review Flag = Partial Apply - Keep AR Open
```

### Short Payment

Expected output:

```text
AR Status = Open
Reconciliation Status = Open
Applied Amount = amount paid
Residual Amount = short-paid amount
Review Flag = Short Pay Review
```

### Overpayment

Expected output:

```text
Applied Amount = open AR amount
Residual Amount = excess amount
Review Flag = Create Unapplied Cash / Credit Balance
```

## 8. Review-Only Scenarios

The following scenarios should generally be routed to review instead of being fully auto-reconciled:

| Scenario | Reason |
|---|---|
| Duplicate Review | Risk of applying the same cash twice. |
| Currency Mismatch | Receipt and invoice are not in the same currency. |
| Wrong Customer or Legal Entity | Customer or legal entity does not align. |
| Reversal / Chargeback | Negative bank activity should not be treated as a normal receipt. |
| Refund | Cash outflow should not be processed as customer receipt. |
| FX Exception | Requires exchange rate and FX gain/loss treatment. |
| Future-Dated Receipt | May not be eligible for current-period clearing. |
| Unexpected Bank Account | Needs treasury or bank account review. |
| Collector Hold | Requires collection team approval. |
| Dispute Payment | Disputed AR requires business review. |
| Credit Memo | Requires credit memo application logic. |
| Tax Withholding | Requires tax deduction or withholding treatment. |
| On-account / Unapplied | No invoice reference is available. |
| Missing ERP Customer ID | Customer identity is not reliable enough for auto-close. |
| Ambiguous Invoice Reference | Memo reference does not identify one clear match. |

## 9. Data Quality Checks

The automation identifies AR rows that should not be auto-matched because the source data appears inconsistent.

Current data quality check:

```text
Closed or already reconciled AR row has non-zero Open Amount
```

These records are written to the `Data Quality Checks` sheet with a recommendation to review ERP data sync or source mapping.

## 10. Output Status Values

### Bank Receipt Status

| Status | Meaning |
|---|---|
| Reconciled | Receipt was matched and no review flag prevents final reconciliation. |
| Pending Review | Receipt was partially matched or has a review flag. |
| Exception | Receipt could not be safely matched. |

### AR Status

| Status | Meaning |
|---|---|
| Open | Invoice remains open after processing. |
| Closed | Invoice was fully cleared and closed. |

### Reconciliation Status

| Status | Meaning |
|---|---|
| Open | Invoice remains open or partially applied. |
| Reconciled | Invoice was fully matched and cleared. |

## 11. Match Method Values

| Match Method | Meaning |
|---|---|
| Memo Invoice Match | Invoice numbers were found in bank memo and matched to open AR. |
| Memo Invoice Match - Bank Fee | Memo invoice match where gross clears AR and net reconciles to bank cash after fees. |
| Memo Invoice Match - Rounding | Memo invoice match with small penny variance. |
| Memo Invoice Match - Partial Payment | Memo invoice match with partial payment. |
| Memo Invoice Match - Installment | Memo invoice match with installment payment. |
| Memo Invoice Match - Short Payment | Memo invoice match where the customer short-paid. |
| Memo Invoice Match - Overpayment | Memo invoice match where the customer overpaid. |
| Memo Invoice Match - Small Residual | Memo invoice match with small residual needing write-off review. |
| Customer Currency Amount Combination | No usable memo invoice reference; matched by customer, currency, and amount combination. |

## 12. Summary Metrics

The `Summary` sheet should include the following run metrics:

| Metric | Meaning |
|---|---|
| Matched bank receipts | Number of bank receipt rows matched by the automation. |
| Matched AR invoice rows | Number of AR invoice rows matched by the automation. |
| Pending review receipts | Number of receipts requiring manual review. |
| Unmatched / exception bank receipts | Number of receipts written to exception or unmatched review. |
| Data quality issues | Number of AR data quality issues found. |
| Output file | Name of the generated output workbook. |

## 13. Limitations and Future Enhancements

This project is a local Excel-based automation demo. In a production corporate finance environment, additional controls would normally be added.

Potential future enhancements:

1. ERP API integration.
2. Bank feed API integration.
3. Customer alias and DBA master data table.
4. Parent-child customer hierarchy matching.
5. Multi-currency FX rate table.
6. Tax withholding rules by country and customer.
7. Approval workflow for write-offs.
8. User interface for reviewing exceptions.
9. Audit log with run timestamp and user ID.
10. Automated unit tests for each scenario ID from S01 to S40.
