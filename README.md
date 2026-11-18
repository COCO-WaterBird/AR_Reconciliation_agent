# North America AR Cash Application & Reconciliation Agent

> An AI-assisted Accounts Receivable (AR) cash application platform that combines
> deterministic accounting rules with AI-powered exception resolution to automate
> bank receipt reconciliation.

> **Accounting should be deterministic. AI should be assistive.**

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Workflow](#workflow)
- [Deterministic Reconciliation Engine](#deterministic-reconciliation-engine)
- [AI Exception Resolution Agent](#ai-exception-resolution-agent)
- [Human-in-the-Loop Approval](#human-in-the-loop-approval)
- [Output](#output)
- [Current Capabilities](#current-capabilities)
- [Roadmap](#roadmap)
- [Business Value](#business-value)
- [Documentation](#documentation)
- [Author](#author)

---

## Overview

Manual AR cash application is one of the most repetitive and time-consuming finance
operations. Finance teams often spend hours every day:

- Downloading bank receipts
- Exporting Open AR from ERP
- Matching receipts to invoices
- Investigating payment exceptions
- Handling short payments
- Processing bank fees
- Preparing reconciliation reports

This project transforms that workflow into an AI-assisted reconciliation platform.

Instead of replacing accounting rules with AI, the project follows one design
principle:

> **Accounting should be deterministic. AI should be assistive.**

The reconciliation engine performs all financial calculations using deterministic
rules, while AI is responsible for understanding unstructured payment information,
explaining exceptions, and recommending next actions.

The objective is **not** to replace accountants. The objective is to eliminate
repetitive reconciliation work while maintaining financial accuracy, internal
controls, and auditability.

---

## Key Features

### Deterministic Reconciliation Engine

- Invoice reference matching extracted from free-text remittance memos
- Customer & currency validation
- Exact amount matching
- Multi-invoice combination matching (subset sum)
- Bank fee processing
- Partial payment handling
- Short payment detection
- Overpayment handling
- Duplicate payment detection
- FX exception handling
- Refund / chargeback handling
- Control total validation

### AI Exception Resolution Agent

When deterministic matching cannot confidently reconcile a payment, the AI agent:

- Understands bank remittance text
- Extracts payment intent
- Identifies the exception type
- Explains the root cause
- Recommends a specific reconciliation action
- Proposes the general-ledger posting
- Generates human-readable reconciliation notes
- Routes high-risk cases for approval

---

## Architecture

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

**The control boundary.** The deterministic engine is the only component that can
move money. The AI agent analyses, explains and recommends — it never applies a
reconciliation on its own. Every AI proposal must pass a validator, and it is only
written back to AR after a human sets `Approval = APPROVE` in the review sheet.

### The code layout

The code is layered so that the dependency direction is always downwards and a
business rule can be found in one place:

| Layer | Package | Contains |
|---|---|---|
| Primitives | `core/` | Cell parsing, money, the column vocabulary |
| Policies | `rules/` | Thresholds, tolerances, the controlled taxonomies |
| Decisions | `matching/` | The matching algorithms and their controls |
| Advice | `agent/`, `llm/` | Context building, prompting, validation, LLM clients |
| Workflows | `services/` | The reconciliation and approval workflows |
| I/O | `io/` | Reading, writing and formatting workbooks |

A layer may use the layers below it and never the ones above. The rules in
`rules/policies.py` are the ones a Finance reviewer would audit: every tolerance
and every exception taxonomy is stated once, with the reason it has that value.

---

## Technology Stack

| Category | Technology | Status |
|----------|------------|--------|
| Language | Python 3.12 | In use |
| Data Processing | Pandas | In use |
| Excel Processing | OpenPyXL | In use |
| AI (exception analysis) | OpenAI-compatible chat API | In use, optional |
| API Service | FastAPI | Planned |
| Database | PostgreSQL | Planned |
| Workflow Automation | n8n | Planned |
| Dashboard | Power BI | Planned |
| Version Control | Git | In use |

---

## Project Structure

```
.
├── src/ar_reconciliation/         # The library
│   ├── config.py                   # Project-relative paths and settings
│   ├── cli.py                      # Console entry points
│   ├── core/                       # Shared primitives
│   │   ├── primitives.py           #   Cell parsing, money, text normalisation
│   │   └── schema.py               #   The column vocabulary
│   ├── rules/                      # Financial validation rules
│   │   └── policies.py             #   Thresholds, tolerances, taxonomies
│   ├── matching/                   # The matching engine
│   │   ├── models.py               #   CandidateInvoice, MatchResult
│   │   ├── memo.py                 #   Memo classification
│   │   ├── subset_sum.py           #   Subset-sum matching
│   │   ├── candidates.py           #   Eligibility controls
│   │   ├── algorithms.py           #   The three matching strategies
│   │   └── application.py          #   Writing a match back to the workbook
│   ├── agent/                      # The AI exception agent
│   │   ├── context.py              #   Candidate pool, scoring, context
│   │   ├── prompt.py               #   System prompt and response schema
│   │   ├── rules_backend.py        #   Offline deterministic backend
│   │   ├── validator.py            #   Safety validation of recommendations
│   │   └── exception_agent.py      #   The agent and its CLI
│   ├── llm/                        # LLM integrations
│   │   └── backend.py              #   OpenAI-compatible chat backend
│   ├── services/                   # Reconciliation services
│   │   ├── reconciliation.py       #   The deterministic workflow
│   │   └── review_rows.py          #   Building the review-sheet rows
│   └── io/                         # Workbook I/O
│       └── workbook.py             #   Validation, preservation, formatting
├── scripts/                        # Runnable entry points
│   ├── run_reconciliation.py       #   Run the deterministic engine
│   ├── run_agent.py                #   Run the AI exception agent
│   ├── run_openai.py               #   Run the agent against the OpenAI API
│   ├── run_local_model.py          #   Run the agent against a local model
│   └── check_regression.py         #   Compare a run against the baseline
├── data/
│   ├── input/                      # Source workbooks (read-only)
│   │   ├── Bank_Cash_Receipts.xlsx     200 bank receipts
│   │   └── Open_AR_Detail.xlsx         191 open AR rows
│   ├── output/                     # Generated by a run (not committed)
│   │   ├── Open_AR_Detail_Reconciled_Result.xlsx
│   │   └── AI_Exception_Approvals.xlsx
│   ├── sample/                     # The sample dataset
│   └── baseline.json               # Metrics a run must reproduce
├── tests/                          # pytest suite
│   ├── conftest.py
│   ├── test_core.py
│   ├── test_engine.py
│   ├── test_agent.py
│   ├── test_approval_workflow.py
│   └── test_llm_backend.py
├── docs/
│   ├── data_dictionary.md
│   ├── reconciliation_logic.md
│   ├── scenario_coverage.md
│   ├── ai_exception_agent.md
│   ├── local_models.md
│   └── prompt.md
├── .github/workflows/ci.yml        # Continuous integration
├── pyproject.toml                  # Packaging and tool configuration
├── .gitignore
└── README.md
```

The old top-level entry points (`auto_reconcile_na.py`, `ai_exception_agent.py`,
`core.py`, `check_regression.py`, `run_openai.py`, `run_local_model.py`) remain
as thin compatibility shims, so existing commands and imports keep working.

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pip install -e .
```

`requirements.txt` installs `pandas`, `openpyxl` and `openai`. The engine and the
rules backend need only the first two; `openai` is used by the LLM backend.
`pip install -e .` installs the package itself so `ar-reconcile` and `ar-agent`
are available on the command line.

### 2. Run the reconciliation engine

```bash
python scripts/run_reconciliation.py
```

This reads the two source workbooks and writes a new result workbook. **The source
files are never modified.**

```
Matched bank receipts              : 136
Matched AR invoice rows            : 158
Pending review receipts            : 67
Unmatched / exception bank receipts: 64
Data quality issues                : 0
Reconciled control difference      : 0.00
```

### 3. Run the AI exception agent

```bash
# Deterministic, offline. No API key required.
python scripts/run_agent.py

# LLM backend
OPENAI_API_KEY=sk-... python scripts/run_agent.py --backend llm --model gpt-4o

# Local model (Ollama / LM Studio / vLLM) — free and offline
python scripts/run_local_model.py --model qwen2.5:7b
```

The agent writes an `AI Exception Review` sheet and an `AI Exception Log` sheet
into the result workbook, plus a durable `AI_Exception_Approvals.xlsx` ledger.

See [Free and Open-Source Model Backends](docs/local_models.md) for the local
model setup, model selection and privacy notes.

### 4. Review and approve

Open the `AI Exception Review` sheet, read the recommendation for each receipt and
set `Approval` to `APPROVE` on the rows you accept.

### 5. Apply the approvals

```bash
python scripts/run_agent.py --apply-approved
```

Only the approved rows are applied to AR. The control totals are re-verified
afterwards, and the workbook is left untouched if they do not balance.

### 6. Run the tests

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/
```

The suite covers the money and text primitives, the matching engine, the AI
agent's safety rules and the human approval gate. It runs against the sample
data in this repository, so it exercises the real rules rather than a model of
them.

---

## Testing

The matching rules are the most valuable thing in this project, and they are
also the easiest thing to break by accident. Every rule change is therefore
guarded by a test.

| Suite | What it covers |
|---|---|
| `tests/test_core.py` | Money parsing, text keys, blank detection. The edge cases that decide whether a control total is right. |
| `tests/test_engine.py` | The nine behaviours in [Scenario Coverage](docs/scenario_coverage.md), plus the invariants that hold on every run: source columns untouched, no receipt applied twice, control totals balance, no receipt silently dropped. |
| `tests/test_agent.py` | The validator: an invoice outside the candidate pool is discarded, an amount above the cash is capped, a residual is recomputed from the AR data, a review-only scenario is never auto-matched. |
| `tests/test_approval_workflow.py` | The approval gate: nothing is applied without an explicit approval, applying twice is idempotent, and the control totals still balance afterwards. |
| `tests/test_llm_backend.py` | The LLM code path: request building, JSON parsing and validation against a fake OpenAI-compatible server, with no network access and no API key. |

### The regression baseline

`data/baseline.json` stores the metrics a run must reproduce: row counts, the
applied-amount control totals, the status distribution and the match methods.
`scripts/check_regression.py` compares a fresh run against it:

```bash
python scripts/check_regression.py data/baseline.json
```

This answers "did the numbers move?" in one command, which is the question
you want answered before every release. If a matching rule is changed on
purpose, re-record the baseline and update the tables in
[Scenario Coverage](docs/scenario_coverage.md) in the same change.

### Continuous integration

`.github/workflows/ci.yml` runs the test suite, the offline LLM check, the
full pipeline and the baseline comparison on every push and pull request, on
Python 3.12 and 3.13.

---

## Workflow

### Step 1 — Import

Read `Bank_Cash_Receipts.xlsx` (sheet `Bank Receipts`) and `Open_AR_Detail.xlsx`
(sheet `Open AR`).

### Step 2 — Validate source data quality

Detect AR rows that are closed or reconciled but still carry a non-zero open
amount. Findings go to the `Data Quality Checks` sheet.

### Step 3 — Extract invoice references

Parse free-text remittance memos for invoice numbers and classify the memo into a
scenario (short pay, bank fee, duplicate, dispute, and so on).

### Step 4 — Deterministic reconciliation

Run the matching engine: invoice reference first, then customer / currency /
amount combination matching with subset-sum.

### Step 5 — Auto-reconcile

Apply the confident matches to AR and to the bank receipts. Partial payments,
short pays and overpayments stay in `Pending Review`.

### Step 6 — Route to the AI Exception Agent

Every receipt that could not be reconciled is analysed by the agent, which
classifies the exception, explains the root cause and recommends a next action.

### Step 7 — Human approval and reporting

A reviewer approves or rejects each recommendation. Approved recommendations are
applied to AR, and the reports are generated.

---

## Deterministic Reconciliation Engine

`auto_reconcile_na.py` performs the matching. Its rules are documented in full in
[Reconciliation Logic](docs/reconciliation_logic.md).

### Match priority

| Priority | Rule | Notes |
|---|---|---|
| 1 | Memo invoice reference, exact amount | Strongest evidence of intent |
| 2 | Memo invoice reference, within rounding tolerance | ±$0.02 |
| 3 | Memo invoice reference, partial / short / overpayment | Bounded residual only |
| 4 | Customer + currency + exact amount | Single invoice |
| 5 | Customer + currency + subset-sum combination | Multiple invoices |
| 6 | Customer + currency + partial combination | Only when one candidate is plausible |

### Two-pass ordering

Receipts whose memo names an invoice are processed **first**. Receipts without a
memo reference are processed **second**, so a combination match can never consume
an invoice that a memo-referenced receipt already named.

### Key behaviours

- **Exact decimal arithmetic.** All money is converted to integer cents with
  `ROUND_HALF_UP`. No float arithmetic touches a monetary value.
- **Installments.** An invoice may be settled by several receipts. The applied
  amount and the transaction ID list accumulate, and the invoice only closes when
  the residual reaches zero.
- **Bank fees.** The invoice is cleared at the net cash received; the fee is
  flagged for a separate GL booking.
- **Controlled fallbacks.** A missing ERP Customer ID can be resolved from the
  customer name when it resolves to exactly one ERP customer. A parent entity
  paying a child's invoice is matched through the AR `Parent Customer ID`.
- **Control totals.** The AR applied amount must equal the bank applied amount
  exactly, or the run fails before anything is written.

### Source data is never modified

`Open Amount`, `AR Status` and `Reconciliation Status` in the source workbook are
never written to. Every result goes into new output columns, with the suggested
status in `Suggested Recon Status` and `Suggested Bank Status`.

### Shared primitives (`core/primitives.py`)

The engine and the AI agent both need to turn spreadsheet cells into comparable
values. Those conversions live in `src/ar_reconciliation/core/primitives.py` so
there is exactly one definition of what a cell means:

- `money_to_cents` / `cents_to_money` — money as integer cents, half-up rounding.
- `normalize_text` / `normalize_key` / `normalize_invoice` — canonical text keys.
- `is_blank` — what counts as an empty cell.

Two rules hold everywhere:

1. **Money parsing never guesses.** A value that cannot be read as an amount
   raises `MoneyParseError` instead of collapsing to zero. A silent zero is
   dangerous because every downstream control total — including the AI
   recommendation validator — would treat it as a real value and confirm it.
2. **Text keys are canonical.** `ACME-CORP`, `ACME Corp.` and `acme corp` all
   produce the same key, so the customer-name and invoice fallbacks behave the
   same on both sides of the pipeline.

---

## AI Exception Resolution Agent

`src/ar_reconciliation/agent/` analyses the receipts the deterministic engine
could not resolve. It is documented in full in [AI Exception Agent](docs/ai_exception_agent.md).

### What it does

For each unmatched receipt it produces a structured judgement:

| Field | Meaning |
|---|---|
| `AI Exception Type` | One of 20 controlled exception types |
| `Root Cause` | Why the receipt did not match |
| `Recommended Action` | Auto Match / Recommend Match / Manual Review / Hold & Escalate / Return to Customer |
| `AI Confidence` | High / Medium / Low |
| `Proposed Invoice Numbers` | The invoices it believes the cash belongs to |
| `Proposed Applied Amount` | How much to apply |
| `Proposed Residual Amount` | What stays open on the invoice |
| `Accounting Treatment` | How the accounting should be handled |
| `Proposed GL Posting` | The journal entry it would propose |
| `AI Explanation` | The narrative for the AR analyst |

### Two backends

- **`rules`** — a deterministic, offline heuristic analyzer. It is the default, so
  the pipeline runs and is testable with no API key and no network access.
- **`llm`** — an OpenAI-compatible chat-completions endpoint. This works with
  hosted APIs (OpenAI, Azure) and with **free local models** (Ollama, LM Studio,
  llama.cpp, vLLM). The model receives the receipt, its memo and the candidate
  open invoices, and returns a structured JSON judgement.

Both backends run through the **same validator**, so the safety guarantees do not
depend on which one is used.

### Structured output

The agent negotiates a grammar-constrained response format. Where the endpoint
supports `json_schema` (Ollama, llama.cpp, vLLM, OpenAI), the decoder itself
prevents an off-schema answer — the model physically cannot emit a value outside
the controlled vocabulary. Endpoints without that support fall back to plain JSON
mode, and the validator covers the difference.

This matters most for local models: a 7B model without the constraint tends to
echo the input back instead of answering.

See [Free and Open-Source Model Backends](docs/local_models.md).

### The validator

The model is never trusted. Every answer is repaired or downgraded:

- An invoice outside the candidate pool is discarded.
- An amount above the cash received is capped.
- An amount above the combined open amount of the proposed invoices is capped.
- A residual that disagrees with the AR data is recomputed from the AR data.
- `Auto Match` requires `High` confidence.
- A low-confidence answer is downgraded to `Manual Review`.
- A scenario the engine marked review-only can never be auto-matched.
- A receipt with no positive net amount (refund, chargeback) can never be applied.

Every repair is recorded in the `AI Exception Log` sheet.

---

## Human-in-the-Loop Approval

The agent recommends. It does not decide.

```
AI recommendation
        │
        ▼
  Human review  ──── Approval = APPROVE ────┐
        │                                     │
        │                                     ▼
        │                            Applied to AR
        │                                     │
        └── Approval = REJECT / blank         ▼
              stays in the queue      Control totals re-verified
```

The approval is a hard gate. Nothing is written to AR without it, the process is
idempotent (re-running never applies the same receipt twice), and the durable
`AI_Exception_Approvals.xlsx` ledger keeps the reviewer's decisions across
re-runs of the reconciliation engine.

An approval is only carried over while the proposal itself is unchanged. If the
recommendation moves to a different invoice or a different amount, the stale
approval is dropped and the row goes back for review.

---

## Output

### `Open_AR_Detail_Reconciled_Result.xlsx`

| Sheet | Purpose |
|---|---|
| Reconciled Open AR | Open AR detail with match results, applied/residual amounts and notes. |
| Processed Bank Receipts | Bank receipts with match method, extracted invoices and suggested status. |
| Unmatched Bank Receipts | Receipts routed to review or exception, with the reason. |
| Data Quality Checks | AR data quality issues found during processing. |
| Summary | Run metrics and the AR/bank control total check. |
| AI Exception Review | The AI agent's recommendation for each unmatched receipt, with the approval column. |
| AI Exception Log | Every validation repair, model error and apply outcome. |

Every reviewable sheet has a frozen header row and an auto-filter, so the
reviewer can narrow a 200-row sheet down to the receipts they need to act on.

### Reading the `Unmatched Bank Receipts` sheet

The sheet carries what a reviewer needs to judge a receipt without opening the
AR workbook:

| Column | Meaning |
|---|---|
| `Reason` | Why the engine could not reconcile the receipt. |
| `Candidate Invoice Count` | How many open AR invoices exist for this customer and currency. |
| `Candidate Open AR Total` | What those open invoices are worth in total. |
| `Suggested Invoices` | The open invoices the receipt most plausibly belongs to. |
| `Suggested Open Amount` | What those suggested invoices are worth. |
| `Receipt vs Open Difference` | How far the receipt is from settling them. |

A difference of `0.00` usually means the receipt would settle the invoice
exactly but was held back for another reason (a collector hold, a tax
withholding, a dispute) — the reviewer only has to confirm that reason, not
re-derive the match.

### `AI_Exception_Approvals.xlsx`

The durable review ledger. It holds the review sheet, including the reviewer's
decisions, so they survive a re-run of the reconciliation engine.

---

## Current Capabilities

**Implemented**

- Invoice reference matching from free-text memos
- Customer & currency validation
- Exact amount matching
- Multi-invoice subset-sum matching
- Partial payment, short pay and overpayment handling
- Bank fee processing
- Duplicate payment detection
- Installment handling across multiple receipts
- Parent/child customer and missing-customer-ID fallbacks
- Exception reporting and control totals
- AI exception analysis with a deterministic and an LLM backend
- Human approval gate with a durable ledger and idempotent write-back
- Automated test suite covering the matching rules, the AI safety rules and
  the approval gate
- Continuous integration with a stored regression baseline

**Not implemented**

- Automated posting to a general ledger
- Approval workflow service, email notification and SSO
- Multi-currency revaluation and FX rate tables
- Persistent storage (the engine is file-based by design)

---

## Roadmap

- [x] Phase 1 — Rule-based reconciliation engine
- [x] Phase 4 — AI Exception Resolution Agent
- [x] Test suite and CI with a regression baseline
- [ ] Phase 2 — FastAPI service
- [ ] Phase 3 — PostgreSQL
- [ ] Phase 5 — Human approval workflow
- [ ] Phase 6 — n8n workflow automation
- [ ] Phase 7 — Power BI dashboard
- [ ] Phase 8 — ERP journal entry integration

---

## Business Value

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

## Documentation

- [Reconciliation Logic](docs/reconciliation_logic.md) — matching rules, output statuses, match methods
- [AI Exception Agent](docs/ai_exception_agent.md) — the agent's design, safety rules and review workflow
- [Free and Open-Source Model Backends](docs/local_models.md) — running the agent on a free local model
- [Data Dictionary](docs/data_dictionary.md) — every input and output column
- [Scenario Coverage](docs/scenario_coverage.md) — S01–S40 scenario reference and measured baseline
- [Prompt](docs/prompt.md) — the original automation specification

---

## Author

**Ke Zhang**

MS in Computer Software Engineering
Former Senior Accountant (Tesla)
AI Automation Engineer | Finance Systems | Python | Power BI | SQL
