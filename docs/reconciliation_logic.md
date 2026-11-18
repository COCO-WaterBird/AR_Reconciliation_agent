# Reconciliation Logic

This document explains the matching and exception-handling logic used by the **North America AR Cash Application Automation** project.

The automation is designed for local Excel-based Accounts Receivable cash application and bank receipt reconciliation. It reads bank receipt data and open AR detail, applies matching rules, and generates a new reconciled output workbook.

## 1. Input Files

The automation expects the following files in `data/input/`:
```text
data/input/Bank_Cash_Receipts.xlsx
data/input/Open_AR_Detail.xlsx
```

Expected input sheets:

| Workbook | Sheet Name | Purpose |
|---|---|---|
| Bank_Cash_Receipts.xlsx | Bank Receipts | Bank cash receipt / remittance data. |
| Open_AR_Detail.xlsx | Open AR | Open customer invoice balances from AR. |

## 2. Output File

The automation creates the following output workbook in `data/output/`:

```text
data/output/Open_AR_Detail_Reconciled_Result.xlsx
```

Expected output sheets:

| Sheet Name | Purpose |
|---|---|
| Reconciled Open AR | AR detail with updated reconciliation results. |
| Processed Bank Receipts | Bank receipts with updated processing results. |
| Unmatched Bank Receipts | Receipts that were not matched or require manual review. |
| Data Quality Checks | AR data quality issues identified during processing. |
| Summary | Run-level metrics and output file name. |

`src/ar_reconciliation/agent/` adds two more sheets to the same workbook:

| Sheet Name | Purpose |
|---|---|
| AI Exception Review | The agent's recommendation for each unmatched receipt, plus the reviewer's approval decision. |
| AI Exception Log | Every validation repair, model error and apply outcome. |

See [AI Exception Agent](ai_exception_agent.md) for the design.

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

If the referenced invoices are open and their total open amount equals the bank `Gross Receipt Amount`, the receipt is matched.

Expected outcome:

```text
Match Method = Memo Invoice Match
Suggested Recon Status = Reconciled
```

When the bank deducted a fee, the referenced invoice normally equals the
`Net Amount` rather than the gross amount, because the customer remitted gross
but the bank received net:

```text
Match Method = Memo Invoice Match - Bank Fee
Suggested Bank Status = Pending Review
```

### Priority 2: Memo Invoice Match with Special Scenario Handling

If the memo includes invoice references but the amount does not match exactly, the automation checks for known scenarios.

| Scenario | Handling |
|---|---|
| Bank Fee | Clear AR at net cash, reconcile bank at gross received, and flag the fee for GL review. |
| Rounding Variance | Match if within tolerance and flag rounding write-off. |
| Partial Payment | Apply available cash and keep the residual invoice balance open. |
| Installment Payment | Apply cash as an installment and keep remaining AR open. |
| Short Payment | Apply cash and route residual balance to short-pay review. |
| Overpayment | Apply to referenced AR and route excess to unapplied cash or credit balance review. |
| Small Residual Write-Off | Flag for write-off review based on policy threshold. |
| Sales Tax Adjustment | Apply cash to the referenced invoice and route the tax difference to review. |

Residual control: for scenarios other than Partial Payment and Installment,
the residual between the receipt and the referenced invoice must be within
`MAX_PARTIAL_RESIDUAL_RATIO` (10%) of the invoice open amount, with an
absolute floor of `MIN_PARTIAL_RESIDUAL_CENTS` (1.00). A larger gap usually
means the memo points at a different invoice and is not applied.

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

An invoice that was already partially applied stays in the pool, so that later
installments can be matched against its remaining balance. A fully cleared
invoice is excluded.

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
Suggested Recon Status = Reconciled
```

### Priority 3a: Partial Amount Combination Match

When no combination matches exactly and the scenario legitimately leaves a
residual, the closest single invoice may be used, but only when exactly one
candidate is a plausible match. If several candidates look equally plausible,
the receipt is routed to review rather than consuming an invoice that may
belong to another receipt.

```text
Match Method = Amount Combination - <Scenario>
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

### 5.1 Processing Order

Receipts are processed in two passes so that explicit remittance advice always
wins over amount-based guessing:

1. **Pass 1** — receipts whose memo names an invoice number.
2. **Pass 2** — receipts with no usable memo, which must be matched by
   customer, currency, and amount.

Within each pass, receipts are processed chronologically by value date.

### 5.2 Customer Fallbacks

Two controlled fallbacks are used when the ERP Customer ID cannot be used
directly:

| Case | Rule |
|---|---|
| Missing ERP Customer ID (S15) | Resolve the customer name to exactly one ERP customer that has open AR. If the name is ambiguous, the receipt goes to review. |
| Parent pays child (S22) | The parent ERP Customer ID is matched against `Parent Customer ID` in the AR file to find child customers. The memo must still name a specific open child invoice. |

## 6. Auto-Close Policy

The automation only closes AR invoices when the match is clean.

An invoice can be automatically closed when:

1. The match is successful.
2. Residual amount is zero.
3. The invoice is open.
4. The receipt is not a risky review-only scenario.
5. The match method does not indicate a partial, short-pay, overpayment, fee, or
   rounding write-off case.

Expected output:

```text
Suggested Recon Status = Reconciled
Suggested Bank Status  = Reconciled
```

The source `AR Status`, `Reconciliation Status`, and bank `Status` columns are
never modified. All results are written to new output columns so that the
original data remains auditable.

## 7. Partial and Residual Balance Policy

The automation should not automatically close invoices with unresolved residual balances.

### Partial Payment

Expected output:

```text
Suggested Recon Status = Pending Review
Applied Amount = cumulative amount paid
Residual Amount = remaining unpaid amount
```

### Short Payment

Expected output:

```text
Suggested Recon Status = Pending Review
Applied Amount = amount paid
Residual Amount = short-paid amount
```

### Overpayment

Expected output:

```text
Applied Amount = open AR amount
Residual Amount = excess amount
Suggested Bank Status = Pending Review
```

### Multiple Installments Against One Invoice

When an invoice is paid by several receipts, the AR row accumulates:

```text
Matched Bank Transaction ID = BTRX-000102, BTRX-000101
Applied Amount             = 4,200.00   (cumulative across receipts)
Residual Amount            = 0.00       (once fully settled)
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
| Missing ERP Customer ID | Customer identity is not reliable enough for auto-close. A controlled name fallback may still match it. |
| Ambiguous Invoice Reference | Memo reference does not identify one clear match. |

## 9. Data Quality Checks

The automation identifies AR rows that should not be auto-matched because the source data appears inconsistent.

Current data quality check:

```text
Closed or already reconciled AR row has non-zero Open Amount
```

These records are written to the `Data Quality Checks` sheet with a recommendation to review ERP data sync or source mapping.

## 10. Output Status Values

### Suggested Bank Status (`Suggested Bank Status`)

| Status | Meaning |
|---|---|
| Reconciled | Receipt was matched and no review condition prevents final reconciliation. |
| Pending Review | Receipt was partially matched, or has a fee, rounding, or write-off condition that needs review. |
| Exception | Receipt could not be safely matched. |

### Suggested Recon Status (`Suggested Recon Status`)

| Status | Meaning |
|---|---|
| Reconciled | Invoice was fully matched and cleared. |
| Pending Review | Invoice was partially applied and still has an open residual. |
| Unmatched | Invoice received no match in this run. |

## 11. Match Method Values

| Match Method | Meaning |
|---|---|
| Memo Invoice Match | Invoice numbers were found in bank memo and matched to open AR. |
| Memo Invoice Match - Bank Fee | Memo invoice match where net cash clears AR and the fee is booked separately. |
| Memo Invoice Match - Rounding | Memo invoice match with small penny variance. |
| Memo Invoice Match - Partial Payment | Memo invoice match with partial payment. |
| Memo Invoice Match - Installment | Memo invoice match with installment payment. |
| Memo Invoice Match - Short Payment | Memo invoice match where the customer short-paid. |
| Memo Invoice Match - Overpayment | Memo invoice match where the customer overpaid. |
| Memo Invoice Match - Sales Tax Adjustment | Memo invoice match where a sales tax difference is routed to review. |
| Memo Invoice Match - Small Residual | Memo invoice match with small residual needing write-off review. |
| Customer Currency Amount Combination | No usable memo invoice reference; matched by customer, currency, and amount combination. |
| Amount Combination - &lt;Scenario&gt; | No exact combination; the only plausible invoice was applied with a residual requiring review. |
| Duplicate Payment Check | Receipt identified as a duplicate of an earlier applied receipt. |

## 12. Summary Metrics

The `Summary` sheet should include the following run metrics:

| Metric | Meaning |
|---|---|
| Matched bank receipts | Number of bank receipt rows matched by the automation. |
| Matched AR invoice rows | Number of AR invoice rows matched by the automation. |
| Pending review receipts | Number of receipts requiring manual review. |
| Unmatched / exception bank receipts | Number of receipts written to exception or unmatched review. |
| Data quality issues | Number of AR data quality issues found. |
| Reconciled AR applied amount | Total cash applied on the AR side. |
| Reconciled bank applied amount | Total cash applied on the bank side. |
| Reconciled control difference | Difference between the two control totals. Must be zero. |
| Output file | Name of the generated output workbook. |

## 13. Exception Analysis and Human Approval

The deterministic engine leaves every receipt it cannot prove unmatched. Those
receipts are then analysed by `src/ar_reconciliation/agent/`, which classifies the
exception, explains the root cause and recommends a next action.

The agent is assistive, not authoritative. Its recommendations are written to the
`AI Exception Review` sheet and are only applied to AR after a human sets
`Approval = APPROVE` and the run is repeated with `--apply-approved`. The write-back
is idempotent and the control totals are re-verified afterwards.

See [AI Exception Agent](ai_exception_agent.md) for the full design, the
controlled vocabularies and the validator rules.

## 14. Limitations and Future Enhancements

This project is a local Excel-based automation demo. In a production corporate finance environment, additional controls would normally be added.

Potential future enhancements:

1. ERP API integration.
2. Bank feed API integration.
3. Customer alias and DBA master data table.
4. Multi-currency FX rate table.
5. Tax withholding rules by country and customer.
6. Approval workflow for write-offs.
7. Audit log with run timestamp and user ID.
8. Automated unit tests for each scenario ID from S01 to S40.
9. A general ledger posting service that consumes the agent's proposed postings.
