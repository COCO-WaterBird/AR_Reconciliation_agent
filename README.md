python -m venv venv


source venv/bin/activate

conda deactivate

python -m pip insta
ll pandas openpyxl


# North America AR Cash Application Automation

This project demonstrates a local Python-based Accounts Receivable cash application and bank receipt reconciliation workflow.

It matches bank cash receipts against open AR invoices using invoice references, ERP Customer ID, currency, gross receipt amount, bank fee logic, and subset-sum invoice combination matching.

## Key Features

- Bank receipt to open AR reconciliation
- Invoice number extraction from remittance memo
- Gross receipt amount used for AR clearing
- Net amount used for bank cash reconciliation
- Bank fee, short pay, overpayment, partial payment, FX, refund, reversal, duplicate, and exception handling
- Output workbook with reconciled AR, processed bank receipts, unmatched receipts, data quality checks, and summary

## Sample Data

The sample data includes 200 bank receipt rows and 200 open AR rows.

Scenario coverage is documented here:

[Scenario Coverage](docs/scenario_coverage.md)

## How to Run

```bash
python -m pip install -r requirements.txt
python auto_reconcile_na.py



