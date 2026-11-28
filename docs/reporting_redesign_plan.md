# Reporting Redesign Plan — Enterprise AR Reconciliation Reporting

Branch: `feature/enterprise-finance-reporting`
Scope: the reporting layer only. The matching engine, financial controls,
AI exception investigation, human review and audit trail are preserved.

---

## 1. Baseline before any change

| Item | Result |
|---|---|
| Test suite | 241 passed |
| `check_regression.py` against `data/baseline.json` | No regression |
| Business-result snapshot (`scripts/compare_runs.py snapshot`) | 136 receipts with cash applied, 153 AR invoices with cash applied, 64 receipts in review, 64 AI classifications |
| Cash applied by currency (bank side) | USD 557,715.05 · CAD 151,650.00 · EUR 0.00 |
| Remaining open AR by currency | USD 225,810.00 · CAD 0.00 |
| Control differences by currency | 0.00 for every control in USD, CAD and EUR |

The snapshot is the reference for the regression comparison in §8.

---

## 2. How the current workbook is built

```
Engine run ──► Reconciled Open AR, Processed Bank Receipts, Unmatched Bank Receipts,
               Data Quality Checks, Summary                       (system of record)
Agent      ──► AI Exception Review, AI Exception Log, Supporting Documents, Extracted Evidence
Apply      ──► updates AR / bank sheets, Summary; appends Approval Records, Audit Trail
Views      ──► Summary-first ordering + Matched, Unmatched, AI Suggested, Evidence (D6)
```

The engine, the agent, the approval write-back, the controls, the regression
baseline and the tests all **read the system-of-record sheets by name**. Those
sheets therefore keep their names and columns; the redesign adds a reporting
layer on top of them and replaces the D6 view sheets.

---

## 3. Findings — financial accuracy and metric definitions (P0)

| # | Finding | Evidence | Impact | Resolution |
|---|---|---|---|---|
| F1 | **Amounts in different currencies are summed.** | Summary lines "Total bank receipts (gross) 1,015,960.63", "Reconciled AR applied amount 709,365.05" and the AI-assisted totals add USD, CAD and EUR. The README quotes "$709,365.05 applied" and "$1,015,960.63 received". | A reader takes a meaningless mixed-currency number as a USD total. | The new report never shows cross-currency totals. Every amount KPI and control is per currency. The machine-readable Summary keeps its lines for backward compatibility, labelled as non-FX-converted arithmetic. README and docs corrected. |
| F2 | **No FX rates exist in the project.** | No rate table, source or date in the inputs or the code. | A consolidated USD figure cannot be produced honestly. | Optional FX-rate file (`currency, rate_to_reporting, rate_date, source`). Without it, no consolidated total is shown and the report says why. No rate is ever assumed to be 1. |
| F3 | **Overlapping status counts.** | Summary: matched 136 + pending review 67 + unmatched 64 = 267 > 200 receipts. "Pending review" includes receipts whose cash was applied but flagged (fee, rounding, overpayment). | The three counts cannot be read as a breakdown. | Three independent dimensions per receipt: **Match Status** (mutually exclusive, sums to all receipts), **Review Status**, **Posting Status**. |
| F4 | **"Matched AR invoice rows" counts applications, not invoices.** | 158 in Summary vs 153 distinct invoices with cash applied (an invoice paid by two instalments counts twice). | Overstates invoices cleared. | Report shows "Invoices with Cash Applied" (distinct) and "Invoice Applications" separately. The engine counter is unchanged. |
| F5 | **"Run Status: FINAL" overstates completeness.** | FINAL only means the controls tie; 64 receipts are still in review and nothing is posted. | Implies the process is finished. | Four statuses: Processing, Financial Control, Exception Review, Posting (`Not Integrated` — no ERP/GL connection). |
| F6 | **`$` applied to every amount column and US-style dates.** | `CURRENCY_FORMAT = '$#,##0.00;($#,##0.00)'`, `DATE_FORMAT = 'MM/DD/YYYY'`. | CAD and EUR amounts are displayed with a dollar sign. | Amounts `#,##0.00;(#,##0.00)` with an explicit ISO currency column; dates `YYYY-MM-DD`; counts `#,##0`. |
| F7 | **Spreadsheet formula injection.** | Memo text and document quotes are written into cells; openpyxl stores a string starting with `=` as a formula. | A crafted remittance could execute a formula when the workbook is opened. | Every formula cell the project did not create is converted back to plain text before saving. A test feeds an `=HYPERLINK(...)` memo. |
| F8 | **The source "Aging Bucket" is not tied to the run.** | It reproduces exactly as of 2026-06-01, before the receipts in the run. | Aging would ignore the cash applied in this run. | Aging is recomputed from Due Date, the remaining balance after reconciliation and an explicit Reporting As-of Date. |
| F9 | **No metric definitions.** | Summary labels only. | The report cannot be audited. | A Metric Definitions sheet and `docs/metric_definitions.md`. |
| F10 | **D6 view sheets duplicate the appendix.** | Matched / Unmatched / AI Suggested / Evidence overlap with each other and with the detail sheets. | Unclear responsibilities. | Replaced by Cash Application Results, Action & Review Queue and AI Decision & Evidence (mapping in §5). |

No defect was found in the matching rules or in the per-currency controls
themselves: every control was already computed per receipt or per invoice and
per currency, so differences cannot net out across currencies.

---

## 4. Decisions taken in this plan

| Topic | Decision | Reason |
|---|---|---|
| Reporting As-of Date | Configurable (`reporting_config.json` or `--as-of`). Default: the latest bank **Posting Date** in the run (2026-06-26 for the sample). | The statement covers postings through that date. Receipts valued later are future-dated. Configurable because it is a reporting choice. |
| Reporting currency | None unless an FX-rate file is supplied. | No reliable rates in the project (F2). |
| Risk and priority thresholds | In `src/ar_reconciliation/reporting/reporting_config.json`, per currency. | Transparent and adjustable without code changes. |
| Customer risk | Operational prioritization based on open, overdue and >90-day balances, open exceptions and disputes. Not a credit rating, and no payment history (none exists). | Meets the explainability requirement. |
| Management attention | Deterministic rules only; every item cites the transactions it comes from. | No unsupported conclusions. |
| Review decisions | Still recorded in `AI Exception Review` (or the UI / `ar-evidence decide`). The Action & Review Queue displays them and is read-only. | Excel edits in a report view must not count as approvals. |

---

## 5. Target workbook and old-to-new mapping

| Level | Sheet | Source / responsibility |
|---|---|---|
| 1 Executive | **Executive Dashboard** | New. Header, four statuses, KPI cards, cash application by currency, status breakdowns, control center, management attention, navigation, charts. |
| 1 Executive | **AR Aging & Customer Risk** | New. Aging by currency, customer risk table, top customers. |
| 2 Operational | **Exception Analysis** | New. Exceptions by category, currency and review status; root-cause basis. |
| 2 Operational | **Action & Review Queue** | Replaces *Unmatched* and *AI Suggested* (D6). |
| 2 Operational | **Cash Application Results** | Replaces *Matched* (D6). One row per receipt with match, review and posting status. |
| 2 Operational | Reconciled Open AR | Unchanged (system of record). |
| 3 Controls & Audit | **Control Reconciliation** | New. Control × currency matrix. |
| 3 Controls & Audit | AI Exception Review | Unchanged. Reviewers record decisions here. |
| 3 Controls & Audit | **AI Decision & Evidence** | Replaces *Evidence* (D6). |
| 3 Controls & Audit | Approval Records, Audit Trail | Unchanged (append-only). |
| 3 Controls & Audit | Data Quality Checks | Unchanged. |
| 4 Supporting | Processed Bank Receipts | Unchanged. Source bank columns plus results. |
| 4 Supporting | Unmatched Bank Receipts, Supporting Documents, Extracted Evidence, AI Exception Log | Unchanged. |
| 4 Supporting | Summary | Unchanged machine-readable run summary (read by the apply path and tests). |
| 4 Supporting | **Metric Definitions** | New. |
| 4 Supporting | **Run Metadata & Configuration** | New. Run ID, timestamps, as-of date, input hashes, thresholds, FX status. |

Source bank and AR columns are already carried unchanged in Processed Bank
Receipts and Reconciled Open AR, so no duplicate source copies are added.

---

## 6. Architecture

```
src/ar_reconciliation/reporting/
    reporting_config.json   thresholds, aging buckets, team mapping
    config.py               loads the configuration and the optional FX-rate file
    statuses.py             match / review / posting status per receipt
    metrics.py              KPI and per-currency figures, with definitions
    exceptions.py           exception analytics and the review queue
    aging.py                AR aging and customer risk
    attention.py            management attention rules
    controls_matrix.py      control × currency matrix (uses services/controls.py)
    builder.py              assembles every report table from the workbook (no styling)
    excel_styles.py         palette, fonts, number formats
    charts.py               native Excel charts
    excel_exporter.py       writes and styles the report sheets; formula-injection guard
```

The calculations are pure functions over DataFrames, so they can be tested
without Excel. The exporter renders tables it is given and contains no
financial logic. The controls come from the existing
`services/controls.py`, which stays the single source of truth.

---

## 7. Implementation order

1. **P0:** statuses, metrics with definitions, per-currency figures, FX handling, controls matrix, number formats, formula-injection guard, README corrections.
2. **P1:** Executive Dashboard, Exception Analysis, Action & Review Queue, Cash Application Results, AI Decision & Evidence, Control Reconciliation.
3. **P2:** AR Aging & Customer Risk, Run Metadata, charts, navigation, print setup.
4. Documentation, regression comparison, regenerated sample report, PR.

---

## 8. Testing

All existing tests stay. The D6 view tests are updated to the new sheet
names, keeping the same invariants: every receipt is in exactly one match
status, and the amounts tie to the detail sheets. New tests cover
multi-currency handling, missing FX rates, status separation, denominators,
partial and unapplied amounts, aging boundaries and dates, workbook
structure, formats, charts, empty and large datasets, reopen validation and
formula injection. `scripts/compare_runs.py` compares the business results
before and after.
