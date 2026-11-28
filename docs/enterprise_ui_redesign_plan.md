# Enterprise UI Redesign Plan

Goal: turn the single-page Streamlit demo into **AR Intelligence**, a
multipage B2B finance application. The reconciliation engine, the financial
controls, the AI agent and the approval rules stay in the backend; the UI
displays their results and collects decisions.

## 1. Review of the previous UI

| Area | Finding |
|---|---|
| Structure | One 330-line script: reconcile, investigate, documents, AI analysis and review on one long scrolling page. |
| Session state | A temporary workspace per session; no navigation, so nothing to preserve. |
| Backend coupling | Called services directly; recomputed nothing financial (good), but read sheets ad hoc. |
| Reusable services | `run_reconciliation`, `reporting.build_report_model`, `agent.context.build_context`, `agent.workflow` (analysis, decisions, apply), `evidence` store and extraction. |

### Issues observed (with the cause)

| # | Issue | Cause | Resolution |
|---|---|---|---|
| 1 | "136 receipts matched" vs. KPI "Matched (no AI needed): 103" | 136 = receipts with cash applied; 103 = receipts with engine status "Reconciled". 33 matched receipts are flagged for confirmation (fee, rounding, overpayment). Both are correct; the labels were not. | KPIs come from the report model: Matched Receipts 136 (129 fully, 7 partially), and Pending Review 97 shown separately. |
| 2 | "Run status: FINAL" with 64 receipts in review | One word for the whole process | Seven workflow statuses: Data Import, Processing, Financial Controls, AI Investigation, Exception Review, Approval, ERP Posting (Not Integrated). |
| 3 | Upload and "use sample data" selectable together | Checkbox next to uploaders | Mutually exclusive radio; the active data source is shown everywhere. |
| 4 | Excessive scrolling | Single page | Ten pages, tabs and expanders. |
| 5 | Exception chosen from a dropdown | No queue | Exception Workbench with search, filters, sorting, multi-select and batch investigation; Previous / Next / Go to transaction. |
| 6 | Truncated amounts | Default dataframe widths; raw floats | Column configuration, `accounting` number format, short tables as wrapped HTML tables. |
| 7 | Evidence status ambiguous | Only a document list | Stages: Uploaded → Parsed → Linked → Retrieved → Cited, plus integrity. |
| 8 | Engineering terms | "Use the LLM backend", workspace path, "Analyse" | Business wording; technical details moved to Settings → Diagnostics. |

### Control gap found during the review

An approved recommendation on a hold-only scenario (for example BTRX-000168,
Collector Hold, Hold / Escalate, still proposing INV-900186 for USD 7,125.00)
**would have been applied** by the approval write-back, which never checked
the recommendation's action. The project's own policy says these scenarios
are "always escalated".

The fix is the new `agent/policy.py`. It evaluates the deterministic rules once
per receipt, and both the UI (Approve enabled or disabled) and the write-back
(refusal) use it. The rules read only deterministic data, so editing the
Recommended Action cell cannot unlock a blocked proposal.

## 2. Architecture

```
app/review_app.py          entry: theme, logo, st.navigation, sidebar context, widget-state persistence
app/views/*.py             one file per page (Overview, Data Import, Reconciliation, Exception Workbench,
                           Exception Investigation, Evidence Center, Financial Controls, Reports,
                           Audit History, Settings)
app/ui/services.py         backend adapter (the only module that calls ar_reconciliation); caching
app/ui/state.py            session workspace, run metadata, selection, flash messages
app/ui/components.py       header, status strip, KPI cards, badges, key-value panels, HTML tables, column formats
app/ui/fmt.py              amounts with ISO codes, dates, counts, percentages, missing-value labels, HTML escaping
app/ui/theme.py, charts.py palette and CSS; Plotly charts
.streamlit/config.toml     theme, error details hidden, 10 MB upload limit
src/ar_reconciliation/agent/policy.py   deterministic policy decision (new backend module)
```

Caching keys on the workbook's modification time, so any action that changes
the workbook invalidates every cached figure; stale balances or approvals are
never shown.

## 3. Priorities

- **P0:** consistent KPIs, exclusive data source, accurate statuses, policy enforcement.
- **P1:** navigation, Overview, Data Import, Reconciliation, Workbench, Investigation, design system, formatting.
- **P2:** Evidence Center, validation matrix, AI vs. policy decision, review, Financial Controls.
- **P3:** Reports, Audit History, Settings and Diagnostics.

## 4. Test baseline

286 tests passed before the redesign (enterprise-reporting branch). Per the
product owner's instruction, automated tests are run once the UI design is
approved.
