
```markdown

Please help me build and validate a North America-style local AR cash application and bank receipts reconciliation automation.

Current folder contains:

1. Bank_Cash_Receipts.xlsx
2. Open_AR_Detail.xlsx
3. auto_reconcile_na.py

Business context:

This is an Accounts Receivable cash application automation for a North America corporate finance environment.

Use Bank_Cash_Receipts.xlsx as the bank receipts file.
Use Open_AR_Detail.xlsx as the open accounts receivable file.

Please inspect the Excel workbook names, sheet names, and all column headers first before running the script.

Expected sheet names:

1. Bank_Cash_Receipts.xlsx
   Sheet: Bank Receipts

2. Open_AR_Detail.xlsx
   Sheet: Open AR

Core matching rules:

1. Use ERP Customer ID as the primary customer matching key.
2. Use Customer Name only as a fallback when ERP Customer ID is missing.
3. Match only within the same Currency.
4. Use Gross Receipt Amount for clearing Open AR.
5. Use Net Amount for bank cash reconciliation.
6. Validate that Gross Receipt Amount minus Bank Fees equals Net Amount.
7. Use Open Amount from Open_AR_Detail.xlsx as the AR amount to be cleared.

Matching priority:

Priority 1:
Parse invoice numbers from Memo / Remittance Detail in the bank receipt file.
If memo contains invoice numbers such as INV-900001, match those invoice numbers directly to Open AR.

Priority 2:
If no usable invoice reference exists, match by ERP Customer ID, Currency, and amount combination.
One bank receipt may match one invoice or multiple invoices.
Use subset-sum logic to determine whether multiple open invoices add up to the Gross Receipt Amount.

Priority 3:
If no exact match exists, route the receipt to exception review instead of forcing a match.

Scenario handling:

Please support these common North America cash application scenarios:

1. Exact one-to-one invoice payment
2. One bank receipt paying multiple invoices
3. Bank fee deducted
4. Penny rounding variance
5. Short payment
6. Overpayment
7. Partial payment
8. Installment payment
9. Duplicate bank receipt
10. Currency mismatch
11. Missing ERP Customer ID
12. DBA / remitter name mismatch
13. Parent-child customer payment
14. Disputed invoice payment
15. Credit memo
16. Reversal / chargeback
17. Refund
18. FX exception
19. Future-dated receipt
20. Unexpected bank account
21. On-account / unapplied cash
22. Collector hold
23. Small residual write-off candidate

Auto-reconciliation policy:

1. Only fully close AR invoices when the match is clean and residual amount is zero.
2. Do not automatically close partial payments or short payments.
3. Keep partial payments open after applying cash.
4. Do not auto-close risky scenarios.
5. Route risky scenarios to Pending Review or Exception.
6. Add a clear Review Flag explaining why manual review is required.

Output requirements:

Generate a new workbook named:

Open_AR_Detail_Reconciled_Result.xlsx

The output workbook must include these sheets:

1. Reconciled Open AR
2. Processed Bank Receipts
3. Unmatched Bank Receipts
4. Data Quality Checks
5. Summary

For Open AR output, write or update these columns:

1. Reconciliation Status
2. AR Status
3. Matched Bank Transaction ID
4. Matched Payment Date
5. Matched Amount
6. Applied Amount
7. Residual Amount
8. Match Method
9. Match Confidence
10. Review Flag
11. Reconciliation Notes

For Bank Receipts output, write or update these columns:

1. Status
2. Reconciliation Batch ID
3. Processing Notes
4. Extracted Invoice Numbers
5. Memo Scenario
6. Match Method
7. Match Confidence

Data quality checks:

Please identify closed or already reconciled AR rows that still have a non-zero Open Amount.
Put these issues into the Data Quality Checks sheet with a recommendation.

Execution instructions:

1. First inspect the Excel headers and confirm whether the code column names match the actual files.
2. If any column name is missing or different, update the configuration section in auto_reconcile_na.py.
3. Run:

python auto_reconcile_na.py

4. If there are errors, fix the script and run again.
5. After successful execution, summarize:
   - Matched bank receipts
   - Matched AR invoice rows
   - Pending review receipts
   - Unmatched / exception bank receipts
   - Data quality issues
   - Output file name



```