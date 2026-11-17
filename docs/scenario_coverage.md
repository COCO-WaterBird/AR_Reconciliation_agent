# Scenario Coverage

This document describes the scenario coverage used by the sample data for the **North America AR Cash Application Automation** project.

The sample data is designed to simulate common Accounts Receivable cash application and bank receipt reconciliation situations in a North America corporate finance environment. Each scenario is identified by a `Scenario ID` from `S01` to `S40`.

## Scenario Summary

| Scenario ID | Scenario Name | Business Description | Expected Handling | Automation Risk Level |
|---|---|---|---|---|
| S01 | Exact 1:1 Match | One bank receipt matches one open AR invoice exactly by customer, currency, and amount. | Auto-reconcile and close the invoice. | Low |
| S02 | One Receipt to Multiple Invoices | One bank receipt pays multiple open invoices for the same customer and currency. | Auto-reconcile using subset-sum invoice combination matching. | Low to Medium |
| S03 | Remittance References Invoice | The bank memo includes one or more invoice numbers. | Prioritize memo invoice-reference matching before amount-combination matching. | Low |
| S04 | Customer Alias / DBA | The remitter name differs from the legal customer name or customer master name. | Match by ERP Customer ID when available; use name only as fallback. | Medium |
| S05 | Bank Fee Deducted | The bank deducts a processing fee, so net cash received is lower than the customer payment amount. | Clear AR using Gross Receipt Amount; reconcile bank cash using Net Amount; flag bank fee for GL review. | Medium |
| S06 | Rounding Penny Variance | The receipt differs from the invoice by a very small rounding amount, typically 1-2 cents. | Auto-match if within rounding tolerance; flag rounding write-off when applicable. | Low to Medium |
| S07 | Short Payment / Early-Pay Discount | The customer pays less than the open invoice balance, possibly due to a discount claim. | Apply cash, keep residual open, and route short-pay balance to review. | Medium |
| S08 | Overpayment / Unapplied Cash | The customer pays more than the referenced open AR amount. | Apply to matched AR and route excess to unapplied cash or credit balance review. | Medium |
| S09 | Partial Payment | The customer pays only part of an invoice. | Apply partial cash, update Applied Amount and Residual Amount, and keep invoice open. | Medium |
| S10 | Multiple Receipts to One Invoice | A single invoice is paid through multiple receipts or installments. | Apply each receipt as partial payment until invoice is fully cleared. | Medium |
| S11 | Duplicate Bank Transaction | The same or highly similar bank receipt appears more than once. | Route to duplicate review; do not auto-close. | High |
| S12 | Duplicate Invoice Amounts | Multiple invoices for the same customer have identical or similar open amounts. | Use invoice references when possible; otherwise flag ambiguous amount matching. | Medium |
| S13 | Multi-Currency Normal Match | Receipts and invoices are denominated in multiple currencies, such as USD, CAD, or EUR. | Match only within the same currency. | Low |
| S14 | Currency Mismatch | The bank receipt currency does not match the open AR currency. | Route to exception review; do not auto-reconcile. | High |
| S15 | Missing ERP Customer ID | The bank feed is missing ERP Customer ID. | Attempt controlled fallback to customer/remitter name; otherwise route to review. | Medium to High |
| S16 | PO-Only Remittance | The memo references only a PO number and does not include invoice numbers. | Do not treat PO reference as definitive; match by customer/currency/amount only if unambiguous. | Medium |
| S17 | Disputed Invoice Payment | The referenced invoice is marked as disputed. | Route to dispute review; do not fully auto-close unless policy allows. | High |
| S18 | Closed / Already Reconciled AR | The AR row is already closed or reconciled but still appears in the data. | Exclude from matching and report in Data Quality Checks if open amount is non-zero. | High |
| S19 | Credit Memo / Negative AR | The customer has a credit memo or negative open AR amount. | Route to credit memo review; do not auto-clear with positive receipts unless explicitly supported. | High |
| S20 | Chargeback / Reversal | The bank transaction represents a chargeback or reversal. | Route to reversal/chargeback review; do not process as normal receipt. | High |
| S21 | Refund to Customer | The bank transaction represents money sent back to the customer. | Route to refund review; do not process as customer cash receipt. | High |
| S22 | Parent-Child Customer Payment | A parent company pays invoices for a subsidiary or child customer. | Match only if parent-child relationship is supported by customer master logic; otherwise review. | Medium to High |
| S23 | Ambiguous Combination | Multiple invoice combinations can equal the same receipt amount. | Route to manual review unless memo invoice references make the match clear. | High |
| S24 | Same Amount Repeated | Multiple receipts or invoices have the same amount for the same customer. | Avoid blind matching; use date, memo, bank reference, and invoice reference when available. | Medium to High |
| S25 | On-Account / Unapplied Payment | The customer pays without specifying invoices. | Route to unapplied cash or on-account review. | Medium |
| S26 | Aging 90+ / Stale AR | The receipt may relate to very old AR balances. | Match carefully; optionally route to collector review depending on policy. | Medium |
| S27 | Wrong Customer or Legal Entity | The receipt appears to belong to a different customer or legal entity. | Route to exception review; do not auto-reconcile. | High |
| S28 | Tax Withholding / Deduction | The customer deducts withholding tax or another tax-related amount. | Apply cash if supported and route residual tax difference to review. | Medium to High |
| S29 | Lockbox Batch Payment | The bank receipt comes through a lockbox batch and may include grouped remittance information. | Match by invoice references or controlled batch logic; review ambiguous allocations. | Medium |
| S30 | Manual Write-Off Candidate | A small residual remains after applying cash. | Flag for policy-based write-off review. | Medium |
| S31 | International Wire / FX Exception | The receipt involves FX conversion, international wire fees, or exchange rate differences. | Route to FX exception review; do not force exact domestic-currency matching. | High |
| S32 | Missing or Bad Memo | The bank memo is blank, incomplete, or unusable. | Fall back to customer/currency/amount combination; route to exception if ambiguous. | Medium |
| S33 | Future-Dated Receipt | The value date or posting date indicates a future-dated receipt. | Route to review to avoid premature reconciliation. | High |
| S34 | Missing AR Master Data | The AR record is missing important attributes such as due date, PO number, collector, or business unit. | Match only if key fields are sufficient; otherwise report as data quality issue. | Medium |
| S35 | Customer Credit Balance | The customer has a credit balance or negative open amount. | Route to credit balance review; avoid auto-clearing against regular invoices unless policy supports it. | High |
| S36 | Sales Tax Adjustment | The difference between receipt and invoice may relate to sales/use tax. | Route residual to tax adjustment review. | Medium |
| S37 | Bank Account Mismatch | The receipt posted to an unexpected or incorrect bank account. | Route to bank account review; do not auto-reconcile. | High |
| S38 | Collector Hold | The AR item is on collector hold or requires collection team approval. | Route to collector review. | High |
| S39 | Memo References Wrong Invoice | The memo references an invoice number that does not align with customer, currency, or amount. | Route to exception review; do not trust memo reference blindly. | High |
| S40 | Mixed Business Unit Allocation | One receipt covers invoices across multiple business units. | Match only when allocation is clear; otherwise route cross-BU allocation to review. | Medium to High |

## Scenario Groups

### Low-Risk Auto-Reconciliation Candidates

These scenarios are generally suitable for automated reconciliation when the data is clean:

- `S01` Exact 1:1 Match
- `S02` One Receipt to Multiple Invoices
- `S03` Remittance References Invoice
- `S05` Bank Fee Deducted, with GL review for the fee
- `S06` Rounding Penny Variance, within tolerance
- `S13` Multi-Currency Normal Match, when currencies match

### Partial or Residual Balance Scenarios

These scenarios may allow partial application of cash but should not automatically close the invoice unless the residual is resolved:

- `S07` Short Payment / Early-Pay Discount
- `S08` Overpayment / Unapplied Cash
- `S09` Partial Payment
- `S10` Multiple Receipts to One Invoice
- `S28` Tax Withholding / Deduction
- `S30` Manual Write-Off Candidate
- `S36` Sales Tax Adjustment

### Exception / Review Scenarios

These scenarios should normally be routed to `Pending Review` or `Exception`:

- `S11` Duplicate Bank Transaction
- `S14` Currency Mismatch
- `S17` Disputed Invoice Payment
- `S18` Closed / Already Reconciled AR
- `S19` Credit Memo / Negative AR
- `S20` Chargeback / Reversal
- `S21` Refund to Customer
- `S23` Ambiguous Combination
- `S27` Wrong Customer or Legal Entity
- `S31` International Wire / FX Exception
- `S33` Future-Dated Receipt
- `S37` Bank Account Mismatch
- `S38` Collector Hold
- `S39` Memo References Wrong Invoice

## Expected Testing Behavior

A successful test run should show that:

1. Clean matches are reconciled automatically.
2. One bank receipt can clear multiple invoices when the amount combination is exact.
3. Memo invoice references are prioritized over generic amount matching.
4. Gross Receipt Amount is used to clear AR.
5. Net Amount is used to reconcile bank cash after bank fees.
6. Partial payments remain open with a residual balance.
7. High-risk scenarios are routed to `Pending Review` or `Exception` instead of being auto-closed.
8. Data quality issues are captured separately in the `Data Quality Checks` sheet.
9. The AR and bank control totals agree, and the control difference is zero.

## Verified Baseline

The following baseline was produced by running the automation against the
sample data in this repository (`Open_AR_Detail_Reconciled_Result.xlsx`):

| Metric | Value |
|---|---|
| Bank receipt rows processed | 200 |
| Matched bank receipts | 136 |
| Matched AR invoice rows | 158 |
| Pending review receipts | 67 |
| Unmatched / exception receipts | 64 |
| Data quality issues | 0 |
| Reconciled control difference | 0.00 |

Scenario outcomes after processing:

| Outcome | Rows |
|---|---|
| Reconciled | 103 |
| Pending Review | 83 |
| Exception | 14 |

The 14 exceptions are receipts whose scenario is intentionally ambiguous: they
have no usable memo invoice reference, several open invoices of a plausible
amount, and a residual too large to apply safely. They are routed to manual
review rather than matched on a guess.

Notes on specific scenarios:

| Scenario | Behaviour |
|---|---|
| S05 Bank Fee | The referenced invoice equals the **net** amount, so AR is cleared at net cash and the fee is flagged for GL review. |
| S10 Multiple Receipts to One Invoice | The invoice accumulates `Applied Amount` across receipts and is `Reconciled` once the balance reaches zero. |
| S15 Missing ERP Customer ID | Matched through a controlled customer-name fallback when the name resolves to exactly one ERP customer. |
| S22 Parent-Child | The parent ERP Customer ID is resolved to the child customer through `Parent Customer ID`, and the memo must still name the child invoice. |
