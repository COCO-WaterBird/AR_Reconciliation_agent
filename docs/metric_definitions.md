# Metric Definitions

Every figure in the enterprise report comes from `src/ar_reconciliation/reporting/metrics.py`,
and this page is generated from the same `METRIC_DEFINITIONS` list that feeds the
workbook's **Metric Definitions** sheet, so the two cannot drift apart.

## Currency rule

Amounts are only ever added within one currency. A consolidated (reporting-currency)
total is shown only when an FX-rate file supplies a rate, rate date and source for every
currency involved. No rate is assumed or defaulted to 1, and FX conversion is never
applied to the controls, which are evaluated in original currency.

## Three independent receipt statuses

| Dimension | Values | Rule |
|---|---|---|
| Match Status | Fully Matched, Partially Matched, Unmatched, Refund / Reversal, Previously Reconciled | Mutually exclusive; counts add up to all receipts. |
| Review Status | Pending Review, Approved, Approved - Not Yet Applied, Rejected, More Evidence Requested, Not Required | Only decisions recorded in the AI Exception Review sheet count. Independent of Match Status. |
| Posting Status | Not Posted (ERP/GL Not Integrated) | The project has no ERP or GL connection. |

## Workflow statuses (dashboard header)

| Status | Meaning |
|---|---|
| Processing Status | Completed when the run produced a result workbook. |
| Financial Control Status | Passed / Passed with Warnings / Failed, from the Control Reconciliation matrix. |
| Exception Review Status | Pending (N receipts) while any receipt awaits a decision or application; Completed once all decided. |
| Posting Status | Not Integrated (no ERP/GL posting). |

A PASS on the controls means the books tie to the cent. It does not mean every receipt is
matched, every exception resolved, or anything posted.

## Metrics

### Total Bank Receipts

- **Business Definition:** Bank receipt lines in the bank file for this run.
- **Data Source:** Processed Bank Receipts
- **Calculation:** COUNT(receipts)
- **Denominator:** n/a
- **Currency Treatment:** Count; currency-independent. Amounts shown per currency.
- **Inclusion / Exclusion:** All lines, including refunds, reversals and receipts held by integrity checks.

### Automatically Matched Receipts

- **Business Definition:** Receipts whose cash the deterministic engine applied to AR without a reviewer decision.
- **Data Source:** Processed Bank Receipts (AR Applied Amount Total, Match Method)
- **Calculation:** COUNT(receipts with applied cash > 0 and not applied by an approved AI resolution)
- **Denominator:** n/a
- **Currency Treatment:** Count; currency-independent.
- **Inclusion / Exclusion:** Excludes AI-approved applications and previously reconciled receipts.

### Fully Matched Receipts

- **Business Definition:** Receipts whose cash is fully accounted for by AR application (and any fee / rounding adjustment), with nothing left unapplied.
- **Data Source:** Processed Bank Receipts
- **Calculation:** COUNT(applied > 0 and unapplied = 0)
- **Denominator:** n/a
- **Currency Treatment:** Count; currency-independent.
- **Inclusion / Exclusion:** Receipt-level status. An invoice paid in part by a fully applied receipt stays open on the AR side (see Open AR Balance).

### Partially Matched Receipts

- **Business Definition:** Receipts with cash applied to AR and a remainder held as unapplied cash on account.
- **Data Source:** Processed Bank Receipts
- **Calculation:** COUNT(applied > 0 and unapplied > 0)
- **Denominator:** n/a
- **Currency Treatment:** Count; currency-independent.
- **Inclusion / Exclusion:** Mutually exclusive with the other match statuses.

### Unmatched Receipts

- **Business Definition:** Positive receipts with no cash applied; the full amount is held for review.
- **Data Source:** Processed Bank Receipts
- **Calculation:** COUNT(gross > 0 and applied = 0 and not previously reconciled)
- **Denominator:** n/a
- **Currency Treatment:** Count; currency-independent.
- **Inclusion / Exclusion:** Refunds / reversals (negative receipts) are counted separately.

### Pending Review Receipts

- **Business Definition:** Receipts that still need a reviewer decision.
- **Data Source:** Processed Bank Receipts (Suggested Bank Status) and AI Exception Review (Approval)
- **Calculation:** COUNT(Review Status = Pending Review)
- **Denominator:** n/a
- **Currency Treatment:** Count; currency-independent.
- **Inclusion / Exclusion:** Independent of Match Status: a matched receipt flagged for confirmation is also pending review. Receipts with a recorded decision are excluded.

### Invoices with Cash Applied

- **Business Definition:** Distinct AR invoices that received cash in this run.
- **Data Source:** Reconciled Open AR (Applied Amount)
- **Calculation:** COUNT(DISTINCT invoices with applied amount > 0)
- **Denominator:** n/a
- **Currency Treatment:** Count; currency-independent.
- **Inclusion / Exclusion:** An invoice paid by several receipts counts once (the engine's 'Matched AR invoice rows' counts applications).

### Auto-Match Rate

- **Business Definition:** Share of in-scope receipts the deterministic engine settled on its own.
- **Data Source:** Processed Bank Receipts
- **Calculation:** Automatically Matched Receipts / In-scope Receipts
- **Denominator:** In-scope receipts. In scope: every receipt except those already reconciled in the source before this run.
- **Currency Treatment:** Count-based ratio; currency-independent.
- **Inclusion / Exclusion:** Refunds stay in the denominator (they are exceptions to resolve).

### Cash Application Rate

- **Business Definition:** Share of positive cash received that was applied to AR, per currency.
- **Data Source:** Processed Bank Receipts
- **Calculation:** Applied to AR / (Positive Gross Receipts - Previously Reconciled)
- **Denominator:** Positive gross receipts in the currency, excluding receipts reconciled before this run.
- **Currency Treatment:** Computed separately per currency; never combined.
- **Inclusion / Exclusion:** Negative receipts (refunds, reversals) are excluded from numerator and denominator.

### Exception Rate

- **Business Definition:** Share of in-scope receipts with no cash applied (unmatched or refund / reversal).
- **Data Source:** Processed Bank Receipts
- **Calculation:** (Unmatched + Refund / Reversal receipts) / In-scope Receipts
- **Denominator:** In-scope receipts. In scope: every receipt except those already reconciled in the source before this run.
- **Currency Treatment:** Count-based ratio; currency-independent.
- **Inclusion / Exclusion:** Matched receipts that are pending confirmation are not exceptions here; see Pending Review Receipts.

### Cash Applied to AR

- **Business Definition:** Cash applied to AR invoices in this run (engine plus approved AI resolutions).
- **Data Source:** Processed Bank Receipts (AR Applied Amount Total)
- **Calculation:** SUM(applied) per currency
- **Denominator:** n/a
- **Currency Treatment:** Per currency; consolidated only with FX rates for every currency.
- **Inclusion / Exclusion:** Excludes non-cash adjustments (discounts, deductions).

### Unapplied Cash

- **Business Definition:** Cash received beyond the AR it settled (overpayments), held on account.
- **Data Source:** Processed Bank Receipts (Unapplied Amount)
- **Calculation:** SUM(unapplied) per currency
- **Denominator:** n/a
- **Currency Treatment:** Per currency.
- **Inclusion / Exclusion:** Does not include receipts held for review (shown separately as Held for Review).

### Held for Review

- **Business Definition:** Positive receipts not applied at all, waiting for a decision.
- **Data Source:** Processed Bank Receipts (Held For Review Amount)
- **Calculation:** SUM(held) per currency for positive receipts
- **Denominator:** n/a
- **Currency Treatment:** Per currency.
- **Inclusion / Exclusion:** Refunds / reversals are shown in their own column.

### Open AR Balance (Outstanding AR)

- **Business Definition:** AR still owed after this run's cash and approved non-cash adjustments.
- **Data Source:** Reconciled Open AR
- **Calculation:** SUM(MAX(Open Amount - Applied Amount - Approved Adjustment Amount, 0)) per currency
- **Denominator:** n/a
- **Currency Treatment:** Per invoice currency.
- **Inclusion / Exclusion:** Fully settled invoices contribute 0. Customer credits (negative balances) would be reported separately; the sample has none.

### Overdue AR / AR Over 90 Days

- **Business Definition:** Open AR past its due date as of the Reporting As-of Date; the part more than 90 days past due.
- **Data Source:** Reconciled Open AR (Due Date) and the Reporting As-of Date
- **Calculation:** SUM(open balance where As-of Date - Due Date >= 1) ; >= 91
- **Denominator:** Overdue AR % = Overdue AR / Open AR Balance, per currency.
- **Currency Treatment:** Per currency.
- **Inclusion / Exclusion:** Invoices without a due date are reported as Unknown Aging, never assumed.

## Rounding

Money is held in integer cents, parsed with Decimal half-up (`core/primitives.py`).
FX conversion, when configured, rounds half-up to the cent per currency before adding.
Rates are computed from integer cents and displayed to one decimal place.
