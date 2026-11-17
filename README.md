<!-- python -m venv venv


source venv/bin/activate

conda deactivate

python -m pip install pandas openpyxl


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

The sample data includes 200 bank receipt rows and 191 open AR rows.

Scenario coverage is documented here:

[Scenario Coverage](docs/scenario_coverage.md)

## How to Run

```bash

python -m pip install pandas openpyxl
python auto_reconcile_na.py
```

The script reads the two source workbooks and writes a new result workbook. The
source files are never modified.

## Output

```text
Open_AR_Detail_Reconciled_Result.xlsx
```

| Sheet | Purpose |
|---|---|
| Reconciled Open AR | Open AR detail with match results, applied/residual amounts and notes. |
| Processed Bank Receipts | Bank receipts with match method, extracted invoices and suggested status. |
| Unmatched Bank Receipts | Receipts routed to review or exception, with the reason. |
| Data Quality Checks | AR data quality issues found during processing. |
| Summary | Run metrics and the AR/bank control total check. |

## Documentation

- [Reconciliation Logic](docs/reconciliation_logic.md) — matching rules, output statuses, match methods
- [Data Dictionary](docs/data_dictionary.md) — every input and output column
- [Scenario Coverage](docs/scenario_coverage.md) — S01–S40 scenario reference
- [Prompt](docs/prompt.md) — the original automation specification


# 🤖 AI-Powered AR Cash Application & Reconciliation Agent

> An AI-assisted Accounts Receivable (AR) cash application platform that combines deterministic accounting rules with AI-powered exception resolution to automate bank receipt reconciliation.

---

## 🚀 Overview

Manual AR cash application is one of the most repetitive and time-consuming finance operations.

Finance teams often spend hours every day:

- Downloading bank receipts
- Exporting Open AR from ERP
- Matching receipts to invoices
- Investigating payment exceptions
- Handling short payments
- Processing bank fees
- Preparing reconciliation reports

This project transforms that workflow into an AI-assisted reconciliation platform.

Instead of replacing accounting rules with AI, this project follows one design principle:

> **Accounting should be deterministic. AI should be assistive.**

The reconciliation engine performs all financial calculations using deterministic rules, while AI is responsible for understanding unstructured payment information, explaining exceptions, and recommending next actions.

---

# ✨ Key Features

## Deterministic Reconciliation Engine

✔ Invoice Reference Matching

✔ Customer & Currency Validation

✔ Exact Amount Matching

✔ Multi-Invoice Combination Matching (Subset Sum)

✔ Bank Fee Processing

✔ Partial Payment Handling

✔ Short Payment Detection

✔ Overpayment Handling

✔ Duplicate Payment Detection

✔ FX Exception Handling

✔ Refund / Chargeback Handling

✔ Control Total Validation

---

## AI Exception Resolution Agent

When deterministic matching cannot confidently reconcile a payment, an AI Agent automatically:

- Understands bank remittance text
- Extracts payment intent
- Identifies exception type
- Explains root cause
- Recommends reconciliation actions
- Generates human-readable reconciliation notes
- Routes high-risk cases for approval

---

# 🏗 Architecture

```
                Bank Receipts
                      │
                      ▼
             Data Validation Agent
                      │
                      ▼
         Deterministic Matching Engine
          ├── Invoice Match
          ├── Customer Match
          ├── Currency Match
          ├── Amount Match
          └── Combination Match
                      │
          ┌───────────┴────────────┐
          │                        │
          ▼                        ▼
   Auto Reconciled          AI Exception Agent
                                      │
                                      ▼
                          Root Cause Analysis
                                      │
                                      ▼
                            Human Approval
                                      │
                                      ▼
                          ERP Posting Ready
```

---

# ⚙ Technology Stack

| Category | Technology |
|----------|------------|
| Language | Python |
| Data Processing | Pandas |
| Excel Processing | OpenPyXL |
| API (Planned) | FastAPI |
| Database (Planned) | PostgreSQL |
| Workflow (Planned) | n8n |
| Dashboard (Planned) | Power BI |
| AI | OpenAI API |
| Version Control | Git |

---

# 📂 Project Structure

```
.
├── auto_reconcile_na.py
├── Bank_Cash_Receipts.xlsx
├── Open_AR_Detail.xlsx
├── output/                                  (generated results)
│   ├── Reconciled Open AR
│   ├── Processed Bank Receipts
│   ├── Unmatched Bank Receipts
│   ├── Data Quality Checks
│   └── Summary
├── docs/
│   ├── data_dictionary.md
│   ├── prompt.md
│   ├── reconciliation_logic.md
│   └── scenario_coverage.md
├── archive/                                 (superseded working copies)
└── README.md
```

---

# 🔄 Workflow

## Step 1

Import bank receipts and Open AR.

↓

## Step 2

Validate source data quality.

↓

## Step 3

Extract invoice numbers from remittance memo.

↓

## Step 4

Perform deterministic reconciliation.

↓

## Step 5

Automatically reconcile high-confidence transactions.

↓

## Step 6

Route unmatched transactions to AI Exception Agent.

↓

## Step 7

Generate reconciliation reports.

---

# 🤖 AI Agent Responsibilities

Unlike traditional reconciliation scripts, this project introduces an AI Agent responsible for:

### Remittance Understanding

Extract invoice references from free-text payment memos.

Example:

```
Payment for INV-10025
Less Freight Claim $250
```

↓

```json
{
  "invoice":"INV-10025",
  "deduction":"Freight Claim",
  "amount":250
}
```

---

### Exception Classification

Examples:

- Short Payment
- Overpayment
- Duplicate Payment
- Bank Fee
- FX Difference
- Missing Customer
- Wrong Legal Entity
- Credit Memo
- Refund
- Chargeback

---

### Root Cause Analysis

Example output:

```
Root Cause

Customer deducted $250
because of a freight damage claim.

Recommendation

Apply partial payment.

Create deduction case.

Route to AR Collector.
```

---

### Human-in-the-loop Approval

High confidence

↓

Auto Match

Medium confidence

↓

Recommend Match

Low confidence

↓

Manual Review

---

# 📊 Outputs

The platform automatically generates:

- Reconciled Open AR
- Processed Bank Receipts
- Exception Report
- Data Quality Report
- Summary Dashboard

---

# 📈 Current Capabilities

✅ Invoice Matching

✅ Customer Matching

✅ Currency Matching

✅ Exact Amount Matching

✅ Multi-Invoice Matching

✅ Partial Payment

✅ Bank Fees

✅ Duplicate Detection

✅ Exception Reporting

✅ Control Totals

---

# 🚧 Roadmap

### Phase 1

Rule-based reconciliation engine ✅

### Phase 2

FastAPI Service

### Phase 3

PostgreSQL

### Phase 4

AI Exception Resolution Agent

### Phase 5

Human Approval Workflow

### Phase 6

n8n Workflow Automation

### Phase 7

Power BI Dashboard

### Phase 8

ERP Journal Entry Integration

---

# 🎯 Business Value

This project is designed for enterprise Finance Transformation initiatives.

Potential use cases include:

- Accounts Receivable Automation
- Finance Shared Services
- Cash Application
- Treasury Operations
- ERP Integration
- Finance Systems Modernization
- AI-Powered Finance Automation

---

# 👤 Author

**Ke Zhang**

MS in Computer Software Engineering

Former Senior Accountant (Tesla)

AI Automation Engineer | Finance Systems | Python | Power BI | SQL

---

## ⭐ Design Philosophy

> Accounting should be deterministic.

> AI should be assistive.

The objective is **not** to replace accountants.

The objective is to eliminate repetitive reconciliation work while maintaining financial accuracy, internal controls, and auditability.