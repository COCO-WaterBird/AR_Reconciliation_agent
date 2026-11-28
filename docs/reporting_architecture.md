# Enterprise Reporting — Architecture, Workbook and Results

The result workbook is an AR reconciliation reporting package for four
readers:

| Reader | Starts at | Answers |
|---|---|---|
| CFO | Executive Dashboard | What happened to the cash, by currency? Do the controls pass? What needs attention? |
| AR Manager | Action & Review Queue, Exception Analysis | Which receipts need action, in what order, and why? |
| Controller | Control Reconciliation | Does every control tie, in every currency? |
| Auditor | AI Decision & Evidence, Approval Records, Audit Trail | Where did each outcome come from, and who approved what? |

The matching engine, the financial controls, the AI exception agent, the
human review and the audit trail are unchanged. Only the reporting layer was
redesigned. See [reporting_redesign_plan.md](reporting_redesign_plan.md) for
the analysis and [metric_definitions.md](metric_definitions.md) for every
metric.

---

## 1. Architecture

```
result workbook (system-of-record sheets)
        │  pandas
        ▼
reporting/builder.py ── statuses.py          Match / Review / Posting status per receipt
                     ── metrics.py           KPIs + definitions, per-currency figures, FX consolidation
                     ── controls_matrix.py   control x currency (figures from services/controls.py)
                     ── exceptions.py        exception analytics, Action & Review Queue
                     ── aging.py             AR aging, customer follow-up priority
                     ── attention.py         management attention rules
        │  ReportModel (DataFrames, no styling)
        ▼
reporting/excel_exporter.py ── excel_styles.py, charts.py
        │  openpyxl: report sheets, charts, navigation, print setup,
        │  formula-injection guard, sheet order and tab colors
        ▼
result workbook (report sheets + system-of-record sheets)
```

- **Single source of truth:** every report figure comes from `builder.py`. The controls come from the existing `services/controls.py`.
- **Testable:** the calculations are pure functions over DataFrames, tested without Excel.
- **No financial logic in rendering:** the exporter renders the tables it is given and writes no formulas. Every figure is computed in Python, so nothing depends on Excel recalculating.
- **Deterministic:** for the same inputs the figures and the Run ID are the same; only the generation timestamp differs.
- **Configurable:** thresholds, aging buckets, team names, the as-of date and the optional FX-rate file are in `reporting/reporting_config.json`.
- **Rebuilt on every write:** the engine, the agent and the approval write-back all call the exporter after their last save. openpyxl drops charts when it reloads a file, so charts are rebuilt each time.

---

## 2. Workbook structure (old → new)

| Level | Sheet | Previous sheet | Change |
|---|---|---|---|
| 1 Executive | **Executive Dashboard** | Summary (first, D6) | New |
| 1 Executive | **AR Aging & Customer Risk** | — | New |
| 2 Operational | **Exception Analysis** | — | New |
| 2 Operational | **Action & Review Queue** | Unmatched, AI Suggested (D6 views) | Replaces both |
| 2 Operational | **Cash Application Results** | Matched (D6 view) | Replaces |
| 2 Operational | Reconciled Open AR | Reconciled Open AR | Unchanged |
| 3 Controls & Audit | **Control Reconciliation** | Summary control lines | New matrix |
| 3 Controls & Audit | AI Exception Review | AI Exception Review | Unchanged (decisions are recorded here) |
| 3 Controls & Audit | **AI Decision & Evidence** | Evidence (D6 view) | Replaces |
| 3 Controls & Audit | Approval Records, Audit Trail | same | Unchanged |
| 3 Controls & Audit | Data Quality Checks | same | Unchanged |
| 4 Supporting | Processed Bank Receipts, Unmatched Bank Receipts | same | Unchanged; source bank columns plus results |
| 4 Supporting | Supporting Documents, Extracted Evidence, AI Exception Log | same | Unchanged |
| 4 Supporting | Summary | Summary | Unchanged machine-readable lines; adds run time, input SHA-256 and a note that all-currency lines are not FX-converted |
| 4 Supporting | **Metric Definitions** | — | New |
| 4 Supporting | **Run Metadata & Configuration** | — | New |

**Compatibility:** the system-of-record sheets keep their names and columns,
because the engine, the agent, the approval write-back, the regression
baseline, the review UI and the tests read them by name.

**Source data:** separate source copies were not added, because the source
bank and AR columns are already carried unchanged in Processed Bank Receipts
and Reconciled Open AR.

**Sheet names:** every name is 31 characters or fewer.

**Tab colors:** navy for executive sheets, teal for operational, slate for
controls and audit, gray for supporting data.

---

## 3. Before and after

| Topic | Before | After |
|---|---|---|
| First sheet | Summary: a 47-line Metric/Value list | Executive Dashboard: four workflow statuses, 8 KPI cards, cash application by currency, status breakdowns, control center, management attention, charts, navigation |
| Currency | Summary added USD + CAD + EUR ("Total bank receipts 1,015,960.63") | Every amount is per currency; a consolidated total only with supplied FX rates |
| Status counts | Matched 136 + Pending review 67 + Unmatched 64 = 267 for 200 receipts | Match Status (129 / 7 / 58 / 6 / 0 = 200), Review Status and Posting Status kept separate |
| Invoices | "Matched AR invoice rows 158" (applications) | 153 invoices with cash applied, plus 158 applications, each labeled |
| Completion | "Run Status FINAL" | Processing: Completed; Controls: Passed; Exception Review: Pending (97 receipts); Posting: Not Integrated |
| Controls | Text lines | Control × currency matrix with Expected / Actual / Variance / PASS-FAIL-WARNING-N/A |
| Exceptions | Unmatched list | By category and currency, top drivers, high-priority list, confirmed vs. AI-suggested root causes |
| Work queue | Two overlapping views | One prioritized queue with facts and AI suggestions in separate columns |
| AR aging | Source bucket as of 2026-06-01 | Recomputed on the remaining balance as of the Reporting As-of Date, per currency, with customer follow-up priority |
| Number formats | `$` on every amount (including CAD and EUR); MM/DD/YYYY | `#,##0.00;(#,##0.00)` with an ISO currency column; `#,##0` counts; `0.0%`; YYYY-MM-DD |
| Text safety | A memo starting with `=` was stored as a formula | Every formula cell is converted to plain text |
| README image | Hard-coded "$709,365.05" (USD + CAD) | Generated from the run, per currency |
| Command-line output | "Reconciled AR applied amount: 709,365.05" | Applied cash and control differences per currency |

---

## 4. Financial issues identified

| # | Issue | Status |
|---|---|---|
| F1 | Amounts in different currencies summed (Summary, README image, command-line output) | **Resolved** in the report, the image and the command-line output. Summary keeps its lines for compatibility with an explicit not-FX-converted note. |
| F2 | No FX rates in the project | **Handled:** optional rate file with rate, date and source; otherwise no consolidated total. |
| F3 | Overlapping status counts | **Resolved:** three independent status dimensions. |
| F4 | Invoice applications labeled as invoices | **Resolved** in the report; the engine counter is unchanged. |
| F5 | "FINAL" overstated completeness | **Resolved:** four workflow statuses. |
| F6 | `$` on non-USD amounts; US date format | **Resolved** workbook-wide. |
| F7 | Spreadsheet formula injection | **Resolved:** guard plus test. |
| F8 | Source aging bucket not tied to the run | **Resolved:** recomputed. |
| F9 | No metric definitions | **Resolved:** sheet plus doc. |
| F10 | Duplicate view sheets | **Resolved:** replaced. |

No defect was found in the matching rules or in the controls. Every control
was already evaluated per receipt or invoice and per currency.

---

## 5. Test and regression results

| Check | Result |
|---|---|
| Test suite | **286 passed** (241 existing + 45 new in `tests/test_enterprise_reporting.py`), with DeprecationWarning as error |
| Existing tests changed | `tests/test_report_views.py` was rewritten for the new sheet names. It keeps the same invariants (one match status per receipt, amounts tie to the cent, every open item has an action, the AI approval flow). No test was deleted or weakened. |
| `scripts/check_regression.py` | No regression |
| `scripts/compare_runs.py` (same sample data, before vs. after) | **No business-result differences** in matched bank receipt IDs (136), matched AR invoice IDs (153), applied cash by currency, remaining AR by currency, unmatched receipts (64), control differences by currency, or AI classifications (64, deterministic backend) |
| End-to-end (reconcile → agent → apply) | Passes; the report is rebuilt after each step |

---

## 6. Known limitations and future improvements

- **No FX consolidation by default:** the project has no rate source. Supplying `fx_rates_file` (currency, rate_to_reporting, rate_date, source) and `reporting_currency` enables it.
- **As-of date default:** the latest bank Posting Date in the run (2026-06-26 for the sample). Set `as_of_date` for a period-end date.
- **Customer risk:** operational prioritization from current balances only. There is no payment history, so none is analyzed, and it is not a credit rating.
- **Thresholds:** the materiality and priority thresholds (per currency) in `reporting_config.json` are starting values for the synthetic sample and should be set by finance.
- **Excel rendering:** charts are native Excel charts generated with openpyxl. The workbook was validated by reopening it with openpyxl and pandas and by a zip integrity check. Visual review in Microsoft Excel is recommended; there was no Excel automation in this environment.
- **Performance:** the report is rebuilt after every workbook write, which roughly doubles the test-suite time. For large files, building it only at the end of a run would be faster.
- **Review UI:** the Streamlit app still shows its own summary. It could embed the Executive Dashboard figures next.
- **Approvals:** the Action & Review Queue is read-only; decisions are recorded in AI Exception Review, through the UI or with `ar-evidence decide`. No approval is simulated in the report.
