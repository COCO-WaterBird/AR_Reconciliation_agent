# UI Implementation Status

Branch: `feature/enterprise-finance-reporting` (local, not pushed). State: **awaiting design review**.

## Pages

| Page | Status | Notes |
|---|---|---|
| Overview | Implemented | 8 KPI cards, match-status donut (mutually exclusive), review status separate, cash application by currency, exception overview, control summary, management attention with jump to the Workbench |
| Data Import | Implemented | Exclusive data source, file validation (records, missing fields, duplicates, issues), preview, guarded run |
| Reconciliation | Implemented | Views (All, Fully / Partially Matched, Unmatched, Needs Review), search, filters, CSV export, detail panel |
| Exception Workbench | Implemented | Summary cards, 9 filters, priority / amount (within currency) / date sort, multi-select, Analyze Selected / All Pending / Re-run Failed, export |
| Exception Investigation | Implemented | Header, Previous / Next / Go to, bank vs. candidate invoices with eligibility, Evidence / AI / Validation & Decision / Review tabs |
| Evidence Center | Implemented | Lifecycle metrics, upload with links, sample documents, library, facts and text viewer, link suggestions |
| Financial Controls | Implemented | Control matrix by currency, blocked transactions, overridden AI proposals, pending application, data quality |
| Reports | Implemented | Excel package and seven CSV extracts with period, run ID, generation time |
| Audit History | Implemented | Runs, investigation, validation, review and execution events; filters; persistence limitation stated |
| Settings | Implemented | Reviewer, AI-assisted mode (key never shown), read-only policy configuration, diagnostics, guarded reset |

## Backend changes (presentation needs only, no business-rule change)

- `agent/policy.py`: per-receipt rule results and final decision.
  - Enforced in the approval write-back, which closes the hold-only approval gap.
- `analyse_exceptions(transaction_ids=...)` and `run_analysis(transaction_ids=...)`: investigate selected receipts and merge their rows into the existing review sheet.
- Review sheet adds an `Original AI Recommendation` column, so the original AI recommendation can be shown next to the policy-validated proposal.
- `BANK_REQUIRED_COLUMNS` / `AR_REQUIRED_COLUMNS` constants, shared by the engine and the upload validation.

## Not implemented (by design or not supported by the backend)

- Escalate and Modify Proposed Resolution as separate review actions: the backend supports APPROVE / REJECT / MORE_EVIDENCE. Editing allocations remains possible in the Excel review sheet, where they are revalidated.
- Enterprise authentication and role-based approval: the review workflow is labeled simulated.
- Persistent cross-session audit store: records live in the session's workbook.
- Timestamps on investigation and validation log events: the backend log has none.

## Screenshots

- Before: `docs/images/ui/before_*.png`
- After: `docs/images/ui/after_*.png`

Both were captured from the running application with `scripts/capture_ui_screenshots.py`.

## Next steps after design approval

1. Automated tests: navigation, state persistence, data source exclusivity, KPI and policy display, blocked approval, downloads, LLM fallback, no repeated runs.
2. Full regression run.
3. README update.
4. Commit and push.
