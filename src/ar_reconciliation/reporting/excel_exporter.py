"""
Write the enterprise report sheets into the result workbook.

The exporter renders the tables :func:`build_report_model` produced.  It
contains no financial logic.  It adds the report sheets in front of the
system-of-record sheets (which keep their names and columns, because the
engine, the agent, the controls and the tests read them), sets tab colors,
navigation links, freeze panes, filters and print settings, and neutralizes
any formula a text cell would otherwise carry (spreadsheet formula
injection from memos, emails or contracts).

It must be the last step that saves the workbook: openpyxl does not keep
charts when it reloads a file, so the charts are rebuilt on every write.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ar_reconciliation.reporting import excel_styles as st
from ar_reconciliation.reporting.builder import ReportModel, build_report_model
from ar_reconciliation.reporting.charts import bar_chart, no_data
from ar_reconciliation.reporting.config import ReportingConfig
from ar_reconciliation.reporting.metrics import METRIC_DEFINITIONS
from ar_reconciliation.reporting.statuses import MATCH_STATUSES, REVIEW_STATUSES

__all__ = [
    "DASHBOARD",
    "REPORT_SHEETS",
    "SYSTEM_SHEETS",
    "LEGACY_VIEW_SHEETS",
    "write_enterprise_report",
    "neutralize_formulas",
]

DASHBOARD = "Executive Dashboard"
AGING = "AR Aging & Customer Risk"
EXCEPTIONS = "Exception Analysis"
QUEUE = "Action & Review Queue"
CASH = "Cash Application Results"
CONTROLS = "Control Reconciliation"
EVIDENCE = "AI Decision & Evidence"
DEFINITIONS = "Metric Definitions"
METADATA = "Run Metadata & Configuration"

#: Report sheets with their reporting level, in workbook order.
REPORT_SHEETS: dict[str, int] = {
    DASHBOARD: 1, AGING: 1,
    EXCEPTIONS: 2, QUEUE: 2, CASH: 2,
    CONTROLS: 3, EVIDENCE: 3,
    DEFINITIONS: 4, METADATA: 4,
}
#: The system-of-record sheets, placed among the report sheets by level.
SYSTEM_SHEETS: dict[str, int] = {
    "Reconciled Open AR": 2,
    "AI Exception Review": 3, "Approval Records": 3, "Audit Trail": 3, "Data Quality Checks": 3,
    "Proposed Deductions": 3, "Data Sources & Enrichment": 4, "Enrichment Comparison": 1,
    "Processed Bank Receipts": 4, "Unmatched Bank Receipts": 4, "Supporting Documents": 4,
    "Extracted Evidence": 4, "AI Exception Log": 4, "Summary": 4,
}
#: Views from the previous layout (decision D6), replaced by the report sheets.
LEGACY_VIEW_SHEETS = ("Matched", "Unmatched", "AI Suggested", "Evidence")

_ORDER = [
    DASHBOARD, "Enrichment Comparison", AGING,
    EXCEPTIONS, QUEUE, CASH, "Reconciled Open AR",
    CONTROLS, "AI Exception Review", "Proposed Deductions", EVIDENCE, "Approval Records", "Audit Trail",
    "Data Quality Checks", "Data Sources & Enrichment",
    "Processed Bank Receipts", "Unmatched Bank Receipts", "Supporting Documents", "Extracted Evidence",
    "AI Exception Log", "Summary", DEFINITIONS, METADATA,
]
_TAB = {1: st.NAVY, 2: st.TEAL, 3: "5B6B7D", 4: "A6A6A6"}
_MAX_WIDTH = 60


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------

def _value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime().date()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return value
    return value


def _put(ws: Worksheet, row: int, col: int, value: Any, kind: str = "text", font=None, align=None):
    cell = ws.cell(row=row, column=col, value=_value(value))
    cell.font = font or st.BODY_FONT
    if kind == "amount":
        cell.number_format = st.AMOUNT_FORMAT
        cell.alignment = align or st.RIGHT
    elif kind == "count":
        cell.number_format = st.COUNT_FORMAT
        cell.alignment = align or st.RIGHT
    elif kind == "pct":
        cell.number_format = st.PERCENT_FORMAT
        cell.alignment = align or st.RIGHT
    elif kind == "date":
        cell.number_format = st.DATE_FORMAT
        cell.alignment = align or st.LEFT
    elif kind == "wrap":
        cell.alignment = align or st.WRAP
    else:
        cell.alignment = align or st.LEFT
    return cell


def _title(ws: Worksheet, title: str, subtitle: str, back_link: bool = True) -> None:
    ws.sheet_view.showGridLines = False
    _put(ws, 1, 1, title, font=st.TITLE_FONT)
    _put(ws, 2, 1, subtitle, font=st.SUBTITLE_FONT)
    if back_link:
        cell = _put(ws, 3, 1, "<< Back to Executive Dashboard", font=st.LINK_FONT)
        cell.hyperlink = f"#'{DASHBOARD}'!A1"


def _section(ws: Worksheet, row: int, col: int, text: str) -> None:
    _put(ws, row, col, text, font=st.SECTION_FONT)


def _note(ws: Worksheet, row: int, col: int, text: str) -> None:
    _put(ws, row, col, text, font=st.NOTE_FONT, align=st.LEFT)


def _write_table(
    ws: Worksheet,
    row: int,
    col: int,
    frame: pd.DataFrame,
    kinds: dict[str, str] | None = None,
    cents: Iterable[str] = (),
    status_cols: Iterable[str] = (),
    widths: dict[str, float] | None = None,
    empty_text: str = "No Data Available",
) -> tuple[int, int, int, int]:
    """Write a header + rows; returns (first_row, last_row, first_col, last_col)."""
    kinds = kinds or {}
    cents = set(cents)
    status_cols = set(status_cols)
    columns = list(frame.columns)
    for offset, name in enumerate(columns):
        cell = ws.cell(row=row, column=col + offset, value=name)
        cell.font = st.HEADER_FONT
        cell.fill = st.HEADER_FILL
        cell.alignment = st.CENTER
        cell.border = st.THIN_BORDER
    if frame.empty:
        _put(ws, row + 1, col, empty_text, font=st.NOTE_FONT)
        return row, row + 1, col, col + max(len(columns) - 1, 0)
    for r_offset, (_, record) in enumerate(frame.iterrows(), start=1):
        for c_offset, name in enumerate(columns):
            value = record[name]
            if name in cents and value is not None and not (isinstance(value, float) and pd.isna(value)):
                value = int(value) / 100
            cell = _put(ws, row + r_offset, col + c_offset, value, kinds.get(name, "text"))
            cell.border = st.THIN_BORDER
            if r_offset % 2 == 0:
                cell.fill = st.ZEBRA_FILL
            if name in status_cols:
                styled = st.status_style(value)
                if styled:
                    cell.fill, cell.font = styled
                    cell.alignment = st.CENTER
    last_row = row + len(frame)
    for c_offset, name in enumerate(columns):
        letter = get_column_letter(col + c_offset)
        if widths and name in widths:
            width = widths[name]
        else:
            sample = [len(str(name))] + [len(str(v)) for v in frame[name].head(200).tolist() if v is not None]
            width = min(max(sample) + 2, _MAX_WIDTH)
            if kinds.get(name) in {"amount", "count", "pct", "date"}:
                width = max(width, 14)
        current = ws.column_dimensions[letter].width or 0
        ws.column_dimensions[letter].width = max(current, max(10, width))
    return row, last_row, col, col + len(columns) - 1


def _detail_sheet(ws: Worksheet, frame: pd.DataFrame, title: str, subtitle: str, note: str,
                  kinds: dict[str, str], cents: Iterable[str] = (), status_cols: Iterable[str] = (),
                  freeze_col: int = 2, widths: dict[str, float] | None = None) -> None:
    _title(ws, title, subtitle)
    _note(ws, 3, 4, note)
    first, last, c1, c2 = _write_table(ws, 4, 1, frame, kinds, cents, status_cols, widths)
    ws.freeze_panes = ws.cell(row=5, column=freeze_col)
    if not frame.empty:
        ws.auto_filter.ref = f"{get_column_letter(c1)}{first}:{get_column_letter(c2)}{last}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "4:4"


def _amount_lines(amounts: dict[str, int]) -> str:
    if not amounts:
        return "No Data Available"
    return "\n".join(f"{currency} {value / 100:,.2f}" for currency, value in sorted(amounts.items()))


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------

def _dashboard(ws: Worksheet, model: ReportModel) -> None:
    m = model.metrics
    ws.sheet_view.showGridLines = False
    widths = {"A": 2, "B": 26}
    for letter in "CDEFGHIJ":
        widths[letter] = 16
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width

    _put(ws, 1, 2, "AR Reconciliation Dashboard", font=st.TITLE_FONT)
    currency_text = (
        f"Reporting Currency: {model.config.reporting_currency}"
        if model.config.reporting_currency
        else "Reporting Currency: none - amounts shown in original currency (no FX rates supplied)"
    )
    _put(ws, 2, 2, f"Reporting As-of Date: {model.as_of or 'n/a'}   |   Report Generated: {model.generated_at} UTC"
                   f"   |   Run ID: {model.run_id}   |   {currency_text}", font=st.SUBTITLE_FONT)
    links = [AGING, EXCEPTIONS, QUEUE, CASH, CONTROLS, EVIDENCE, DEFINITIONS, METADATA]
    _put(ws, 3, 2, "Go to:", font=st.NOTE_FONT)
    for offset, name in enumerate(links):
        cell = _put(ws, 3, 3 + offset, name, font=st.LINK_FONT, align=st.WRAP)
        cell.hyperlink = f"#'{name}'!A1"
    ws.row_dimensions[3].height = 28

    # --- Workflow status -------------------------------------------------
    _section(ws, 5, 2, "Workflow Status")
    statuses = [
        ("Processing Status", model.processing_status),
        ("Financial Control Status", model.control_status),
        ("Exception Review Status", model.review_status),
        ("Posting Status", model.posting_status),
    ]
    for index, (label, value) in enumerate(statuses):
        col = 2 + index * 2
        ws.merge_cells(start_row=6, start_column=col, end_row=6, end_column=col + 1)
        ws.merge_cells(start_row=7, start_column=col, end_row=7, end_column=col + 1)
        _put(ws, 6, col, label, font=st.KPI_LABEL_FONT, align=st.CENTER).fill = st.CARD_FILL
        cell = _put(ws, 7, col, value, align=st.CENTER)
        styled = st.status_style(value) or (st.fill(st.MID_GRAY), st.HEADER_FONT)
        cell.fill, cell.font = styled
    ws.row_dimensions[7].height = 30
    _note(ws, 8, 2, "A PASS on the controls means the books tie. It does not mean every receipt is matched, "
                    "every exception resolved, or anything posted to the general ledger.")

    # --- KPI cards ---------------------------------------------------------
    _section(ws, 10, 2, "Key Metrics")
    counts_by_currency = model.statuses.groupby("Currency").size().to_dict() if len(model.statuses) else {}
    cash = m.cash_by_currency
    applied = dict(zip(cash["Currency"], cash["Applied to AR"])) if not cash.empty else {}
    unapplied = dict(zip(cash["Currency"], cash["Unapplied Cash"])) if not cash.empty else {}
    cards = [
        ("Auto-Match Rate", m.auto_match_rate if m.auto_match_rate is not None else "N/A",
         f"{m.auto_matched:,} of {m.in_scope_receipts:,} in-scope receipts matched by rules"),
        ("Total Bank Receipts", m.total_receipts,
         " | ".join(f"{c} {n:,}" for c, n in sorted(counts_by_currency.items())) or "No receipts"),
        ("Matched Bank Receipts", m.matched_receipts,
         f"{m.match_counts['Fully Matched']:,} fully | {m.match_counts['Partially Matched']:,} partially"),
        ("Invoices with Cash Applied", m.invoices_with_cash,
         f"{m.invoice_applications:,} invoice applications" if m.invoice_applications is not None else "Distinct invoices"),
        ("Pending Review Receipts", m.pending_review, "Awaiting a reviewer decision"),
        ("Cash Applied to AR", _amount_lines(applied), model.metrics.consolidated["Applied to AR"][1]),
        ("Unapplied Cash", _amount_lines(unapplied), "Overpayments held on account"),
        ("Outstanding AR Balance", _amount_lines(m.open_ar_by_currency), "After this run's cash and adjustments"),
    ]
    for index, (label, value, note) in enumerate(cards):
        row = 11 + (index // 4) * 4
        col = 2 + (index % 4) * 2
        for r in range(row, row + 3):
            ws.merge_cells(start_row=r, start_column=col, end_row=r, end_column=col + 1)
            for c in (col, col + 1):
                ws.cell(row=r, column=c).fill = st.CARD_FILL
        _put(ws, row, col, label, font=st.KPI_LABEL_FONT, align=st.CENTER)
        multi = isinstance(value, str) and "\n" in value
        font = st.KPI_VALUE_FONT if not multi else st.Font(name=st.FONT, size=12, bold=True, color=st.TEAL)
        kind = "pct" if isinstance(value, float) else ("count" if isinstance(value, int) else "text")
        _put(ws, row + 1, col, value, kind, font=font, align=st.CENTER)
        _put(ws, row + 2, col, note, font=st.NOTE_FONT, align=st.CENTER)
        ws.row_dimensions[row + 1].height = 52 if multi or index >= 5 else 30
        ws.row_dimensions[row + 2].height = 26

    # --- Cash application by currency -------------------------------------
    row = 20
    _section(ws, row, 2, "Cash Application by Currency")
    table = cash.drop(columns=["Previously Reconciled"]) if not cash.empty else cash
    first, last, c1, c2 = _write_table(
        ws, row + 1, 2, table,
        kinds={c: "amount" for c in table.columns} | {"Currency": "text", "Receipts": "count", "Cash Application Rate": "pct"},
        cents=[c for c in table.columns if c not in {"Currency", "Receipts", "Cash Application Rate"}],
        widths={c: 16 for c in table.columns},
    )
    _note(ws, last + 1, 2, "Gross Receipts (positive) = Applied to AR + Unapplied Cash + Fee / Rounding Adjustment + "
                           "Held for Review (+ Previously Reconciled). Refunds / reversals are shown separately. "
                           "Currencies are never added together.")
    rate_col = c1 + list(table.columns).index("Cash Application Rate") if not table.empty else c1
    if not table.empty:
        bar_chart(ws, "L5", "Cash Application Rate by Currency", (c1, first + 1, c1, last),
                  (rate_col, first + 1, rate_col, last), percent=True, width=14, height=7)
    else:
        no_data(ws, "L5", "Cash Application Rate by Currency")

    # --- Status breakdown ----------------------------------------------------
    row = last + 3
    _section(ws, row, 2, "Reconciliation Status (mutually exclusive)")
    _section(ws, row, 6, "Review Status (independent)")
    total = max(m.total_receipts, 1)
    match_table = pd.DataFrame(
        [{"Match Status": s, "Receipts": m.match_counts[s], "% of Receipts": m.match_counts[s] / total}
         for s in MATCH_STATUSES]
    )
    review_table = pd.DataFrame(
        [{"Review Status": s, "Receipts": m.review_counts[s], "% of Receipts": m.review_counts[s] / total}
         for s in REVIEW_STATUSES]
    )
    mf, ml, mc1, _ = _write_table(ws, row + 1, 2, match_table,
                                  {"Receipts": "count", "% of Receipts": "pct"}, status_cols=["Match Status"])
    _write_table(ws, row + 1, 6, review_table, {"Receipts": "count", "% of Receipts": "pct"},
                 status_cols=["Review Status"])
    bar_chart(ws, "L21", "Receipts by Match Status", (mc1, mf + 1, mc1, ml), (mc1 + 1, mf + 1, mc1 + 1, ml),
              y_title="Receipts", horizontal=True, width=14, height=7)
    row = max(ml, row + 1 + len(REVIEW_STATUSES)) + 2

    # --- Financial control center -------------------------------------------
    _section(ws, row, 2, "Financial Control Center")
    controls = model.controls.drop(columns=["What It Proves"])
    first, last, c1, _ = _write_table(ws, row + 1, 2, controls.drop(columns=["_unit"]),
                                      {"Expected": "amount", "Actual": "amount", "Variance": "amount"},
                                      status_cols=["Status"])
    for offset, unit in enumerate(controls["_unit"].tolist(), start=1):
        for col in (c1 + 2, c1 + 3, c1 + 4):
            cell = ws.cell(row=first + offset, column=col)
            if unit == "amount" and isinstance(cell.value, (int, float)):
                cell.value = cell.value / 100
            elif unit == "count":
                cell.number_format = st.COUNT_FORMAT
    _note(ws, last + 1, 2, "Each control is evaluated per currency; a difference in one currency cannot be offset by "
                           "another. Count-based checks show the number of issues found.")
    row = last + 3

    # --- Management attention -------------------------------------------------
    _section(ws, row, 2, "Management Attention")
    _note(ws, row + 1, 2, "Generated by deterministic rules from this run's data (see the Rule column). "
                          "No AI-generated content. Teams are suggested functions, not named individuals.")
    attention = model.attention
    first, last, _, _ = _write_table(
        ws, row + 2, 2,
        attention[["Priority", "Issue", "Currency", "Financial Exposure", "Items", "Recommended Action",
                   "Suggested Responsible Team", "References", "Rule"]] if not attention.empty else attention,
        {"Financial Exposure": "amount", "Items": "count", "Recommended Action": "wrap",
         "References": "wrap", "Issue": "wrap", "Rule": "wrap"},
        cents=["Financial Exposure"], status_cols=["Priority"],
        widths={"Priority": 11, "Issue": 26, "Currency": 10, "Financial Exposure": 16, "Items": 9,
                "Recommended Action": 34, "Suggested Responsible Team": 18, "References": 34, "Rule": 24},
        empty_text="No items require management attention.",
    )
    for r in range(first + 1, last + 1):
        ws.row_dimensions[r].height = 42

    ws.print_area = f"A1:R{last + 1}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.freeze_panes = "A5"


def _aging(ws: Worksheet, model: ReportModel) -> None:
    _title(ws, AGING, f"Remaining AR after this run, aged from Due Date as of {model.as_of or 'n/a'} "
                      f"({model.as_of_basis}). Amounts by invoice currency; never combined.")
    aging = model.aging_summary
    _section(ws, 5, 1, "AR Aging Summary")
    kinds = {c: "amount" for c in aging.columns}
    kinds.update({"Currency": "text", "Overdue AR %": "pct", "Open Invoices": "count"})
    cents = [c for c in aging.columns if c not in {"Currency", "Overdue AR %", "Open Invoices"}]
    first, last, c1, _ = _write_table(ws, 6, 1, aging, kinds, cents, widths={c: 15 for c in aging.columns})
    _note(ws, last + 1, 1, "Fully settled invoices are excluded; partially paid invoices age their remaining balance. "
                           "Invoices without a due date are shown as Unknown, never assumed. Disputed / On Hold is "
                           "included in the buckets and also shown separately.")
    buckets = [b["label"] for b in model.config.aging_buckets]
    chart_row = last + 3
    if aging.empty:
        no_data(ws, f"A{chart_row}", "AR Aging Distribution")
    for index, currency in enumerate(aging["Currency"].tolist() if not aging.empty else []):
        data_row = first + 1 + index
        bar_chart(ws, f"{get_column_letter(1 + index * 8)}{chart_row}", f"AR Aging Distribution - {currency}",
                  (c1 + 1, first, c1 + len(buckets), first), (c1 + 1, data_row, c1 + len(buckets), data_row),
                  y_title=currency, from_rows=True, width=15, height=7)
    row = chart_row + 16

    risk = model.customer_risk
    _section(ws, row, 1, "Customer Follow-up Priority")
    _note(ws, row + 1, 1, "Operational prioritization for collections follow-up from transparent rules "
                          "(reporting_config.json) - not a credit rating. No payment history is available, so none "
                          "is analyzed. Unapplied / held cash is an operational item (cash already received).")
    kinds = {c: "amount" for c in ["Open AR", "Overdue AR", "AR Over 90 Days", "Disputed / On Hold",
                                   "Unapplied / Held Cash (operational)"]}
    kinds.update({"Open Payment Exceptions": "count", "Recommended Action": "wrap", "Risk Rule": "wrap"})
    first, last, c1, c2 = _write_table(ws, row + 2, 1, risk, kinds,
                                       cents=[k for k, v in kinds.items() if v == "amount"],
                                       status_cols=["Risk Level"],
                                       widths={"Recommended Action": 48, "Risk Rule": 30, "Customer Name": 30})
    if not risk.empty:
        ws.auto_filter.ref = f"{get_column_letter(c1)}{first}:{get_column_letter(c2)}{last}"
    row = last + 3

    top_n = int(model.config.get("top_customers", 10))
    for index, (currency, group) in enumerate(risk.groupby("Currency", sort=True) if not risk.empty else []):
        top = group.sort_values("Open AR", ascending=False).head(top_n)[["Customer Name", "Open AR"]]
        _section(ws, row, 1, f"Top Customers by Outstanding AR - {currency}")
        tf, tl, tc1, _ = _write_table(ws, row + 1, 1, top, {"Open AR": "amount"}, cents=["Open AR"])
        bar_chart(ws, f"D{row}", f"Top Customer Outstanding Balances - {currency}", (tc1, tf + 1, tc1, tl),
                  (tc1 + 1, tf + 1, tc1 + 1, tl), y_title=currency, horizontal=True, width=16,
                  height=max(6, 0.6 * len(top) + 2))
        row = max(tl, row + 14) + 3
    ws.freeze_panes = "B5"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _exceptions(ws: Worksheet, model: ReportModel) -> None:
    _title(ws, EXCEPTIONS, "Receipts not fully settled or awaiting review, by the engine's rule-based category. "
                           "AI opinions are labeled and kept apart from confirmed facts.")
    summary = model.exception_summary
    _section(ws, 5, 1, "Exceptions by Category and Currency")
    first, last, _, _ = _write_table(
        ws, 6, 1, summary,
        {"Receipt Count": "count", "Unresolved Amount": "amount", "% of Exceptions (count)": "pct",
         "Pending Review": "count", "Decided": "count"},
        cents=["Unresolved Amount"], widths={"Exception Category": 30, "Most Common AI Recommendation": 28},
    )
    _note(ws, last + 1, 1, "Unresolved Amount = held amount for unmatched receipts and refunds, unapplied cash for "
                           "partially matched receipts; matched receipts awaiting confirmation show 0. Per currency.")
    row = last + 3

    counts = (summary.groupby("Exception Category")["Receipt Count"].sum().sort_values(ascending=False)
              .reset_index() if not summary.empty else pd.DataFrame(columns=["Exception Category", "Receipt Count"]))
    _section(ws, row, 1, "Exception Count by Category (all currencies - counts only)")
    cf, cl, cc1, _ = _write_table(ws, row + 1, 1, counts, {"Receipt Count": "count"}, widths={"Exception Category": 30})
    if not counts.empty:
        bar_chart(ws, f"D{row}", "Exception Count by Category", (cc1, cf + 1, cc1, cl), (cc1 + 1, cf + 1, cc1 + 1, cl),
                  y_title="Receipts", horizontal=True, width=18, height=max(7, 0.45 * len(counts) + 2))
    else:
        no_data(ws, f"D{row}", "Exception Count by Category")
    row = max(cl, row + 18) + 3

    _section(ws, row, 1, "Top Exception Drivers by Amount (per currency)")
    drivers = (summary.sort_values("Unresolved Amount", ascending=False).groupby("Currency").head(3)
               [["Currency", "Exception Category", "Receipt Count", "Unresolved Amount"]]
               if not summary.empty else pd.DataFrame(columns=["Currency", "Exception Category", "Receipt Count",
                                                                 "Unresolved Amount"]))
    _, last, _, _ = _write_table(ws, row + 1, 1, drivers, {"Receipt Count": "count", "Unresolved Amount": "amount"},
                                 cents=["Unresolved Amount"])
    row = last + 3

    _section(ws, row, 1, "Root Cause Basis - Confirmed vs. AI-Suggested")
    _, last, _, _ = _write_table(ws, row + 1, 1, model.root_causes, {"Receipt Count": "count", "Meaning": "wrap"},
                                 widths={"Meaning": 60})
    row = last + 3

    _section(ws, row, 1, "High-Priority Exceptions")
    queue = model.queue
    high = queue[queue["Priority"] == "High"] if not queue.empty else queue
    cols = ["Priority", "Bank Receipt ID", "Customer Name", "Currency", "Unresolved Amount", "Exception Category",
            "Root Cause Basis", "AI Suggested Explanation (AI-generated)", "Recommended Action", "Review Status"]
    _write_table(ws, row + 1, 1, high[cols] if not high.empty else pd.DataFrame(columns=cols),
                 {"Unresolved Amount": "amount", "AI Suggested Explanation (AI-generated)": "wrap",
                  "Recommended Action": "wrap"},
                 status_cols=["Priority", "Review Status"],
                 widths={"AI Suggested Explanation (AI-generated)": 50, "Recommended Action": 30},
                 empty_text="No high-priority exceptions.")
    ws.freeze_panes = "A5"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _controls(ws: Worksheet, model: ReportModel) -> None:
    _title(ws, CONTROLS, f"Financial control results by currency. Overall: {model.control_status}.")
    _section(ws, 5, 1, "Control Matrix")
    matrix = model.controls.drop(columns=["_unit"])
    first, last, c1, _ = _write_table(ws, 6, 1, matrix, {"Expected": "amount", "Actual": "amount",
                                                         "Variance": "amount", "What It Proves": "wrap"},
                                      status_cols=["Status"], widths={"What It Proves": 70, "Control": 28})
    for offset, unit in enumerate(model.controls["_unit"].tolist(), start=1):
        for col in (c1 + 2, c1 + 3, c1 + 4):
            cell = ws.cell(row=first + offset, column=col)
            if unit == "amount" and isinstance(cell.value, (int, float)):
                cell.value = cell.value / 100
            elif unit == "count":
                cell.number_format = st.COUNT_FORMAT
    _note(ws, last + 1, 1, "PASS = the figures tie to the cent. FAIL = a difference exists; the report is not final. "
                           "WARNING = held items to resolve. N/A = not evaluated. A PASS does not mean every receipt "
                           "is matched or every exception resolved.")
    row = last + 3
    _section(ws, row, 1, "Cash Disposition by Currency (Cash Control detail)")
    cash = model.metrics.cash_by_currency
    kinds = {c: "amount" for c in cash.columns}
    kinds.update({"Currency": "text", "Receipts": "count", "Cash Application Rate": "pct"})
    _write_table(ws, row + 1, 1, cash, kinds,
                 cents=[c for c in cash.columns if c not in {"Currency", "Receipts", "Cash Application Rate"}],
                 widths={c: 16 for c in cash.columns})
    ws.freeze_panes = "A5"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _definitions(ws: Worksheet) -> None:
    frame = pd.DataFrame(METRIC_DEFINITIONS)
    _detail_sheet(ws, frame, DEFINITIONS, "How every report metric is defined and calculated.",
                  "Amounts are only added within one currency.", {c: "wrap" for c in frame.columns},
                  widths={"Metric": 26, "Business Definition": 45, "Data Source": 30, "Calculation": 40,
                          "Denominator": 32, "Currency Treatment": 30, "Inclusion / Exclusion": 45})


def _metadata(ws: Worksheet, model: ReportModel) -> None:
    frame = pd.DataFrame(model.metadata, columns=["Item", "Value"])
    _detail_sheet(ws, frame, METADATA, "Run identification, inputs and the configuration used for this report.",
                  "Thresholds come from reporting_config.json.", {"Value": "wrap"}, freeze_col=1,
                  widths={"Item": 34, "Value": 90})


# ---------------------------------------------------------------------------
# Workbook-level steps
# ---------------------------------------------------------------------------

def neutralize_formulas(workbook) -> int:
    """
    Turn every formula cell into plain text.

    The project never writes formulas (every figure is computed in Python),
    so any formula found is text from a memo, an email or a contract that
    starts with "=".  Kept as text, it can never execute.  Returns the number
    of cells neutralized.
    """
    changed = 0
    for ws in workbook.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    text = str(cell.value)
                    cell.value = text
                    cell.data_type = "s"
                    changed += 1
    return changed


def _order_and_tabs(workbook) -> None:
    names = workbook.sheetnames
    wanted = [n for n in _ORDER if n in names] + [n for n in names if n not in _ORDER]
    workbook._sheets = [workbook[n] for n in wanted]
    for name in wanted:
        level = REPORT_SHEETS.get(name) or SYSTEM_SHEETS.get(name) or 4
        workbook[name].sheet_properties.tabColor = _TAB[level]
        workbook[name].sheet_view.tabSelected = name == DASHBOARD
    workbook.active = 0


def write_enterprise_report(path: Path, config: ReportingConfig | None = None,
                            generated_at: datetime | None = None) -> ReportModel | None:
    """Rebuild every report sheet from the workbook's system-of-record sheets."""
    path = Path(path)
    excel = pd.ExcelFile(path)
    names = list(excel.sheet_names)
    if "Processed Bank Receipts" not in names:
        excel.close()
        return None  # not a result workbook (e.g. a FAILED diagnostic file)
    skip = set(REPORT_SHEETS) | set(LEGACY_VIEW_SHEETS)
    sheets = {name: pd.read_excel(excel, name) for name in names if name not in skip}
    excel.close()
    model = build_report_model(sheets, config, generated_at)

    workbook = load_workbook(path)
    for name in list(skip):
        if name in workbook.sheetnames:
            del workbook[name]

    def sheet(name: str) -> Worksheet:
        return workbook.create_sheet(name)

    _dashboard(sheet(DASHBOARD), model)
    _aging(sheet(AGING), model)
    _exceptions(sheet(EXCEPTIONS), model)
    _detail_sheet(
        sheet(QUEUE), model.queue, QUEUE,
        f"{len(model.queue):,} open items, highest priority first. Facts and AI suggestions are in separate columns.",
        "Read-only view. Record decisions in 'AI Exception Review' (or the review UI / ar-evidence decide); "
        "entries typed here are not approvals.",
        {"Receipt Date": "date", "Receipt Amount": "amount", "Applied Amount": "amount", "Unresolved Amount": "amount",
         "AI Suggested Explanation (AI-generated)": "wrap", "Recommended Action": "wrap", "Review Comments": "wrap"},
        status_cols=["Priority", "Review Status", "Match Status"], freeze_col=4,
        widths={"AI Suggested Explanation (AI-generated)": 50, "Recommended Action": 32, "Customer Name": 28},
    )
    _detail_sheet(
        sheet(CASH), model.cash_results, CASH,
        "Every bank receipt with its cash disposition and three independent statuses (match, review, posting).",
        "Match Status is mutually exclusive: the counts add up to all receipts.",
        {"Receipt Date": "date", **{c: "amount" for c in ["Gross Receipt Amount", "Bank Fees", "Applied to AR",
                                                          "Unapplied Cash", "Fee / Rounding Adjustment",
                                                          "Held for Review"]}},
        status_cols=["Match Status", "Review Status"], freeze_col=2,
        widths={"Customer Name": 28, "Invoices Applied": 28, "Match Method": 30},
    )
    _controls(sheet(CONTROLS), model)
    _detail_sheet(
        sheet(EVIDENCE), model.evidence, EVIDENCE,
        "The evidence behind each receipt's outcome: matching rule, AI recommendation, document facts, decisions.",
        "The Basis column separates confirmed facts from AI-generated content.",
        {"Amount": "amount", "Detail": "wrap", "Quote / Notes": "wrap"},
        widths={"Detail": 40, "Quote / Notes": 60, "Basis": 32, "Source": 28}, freeze_col=2,
    )
    _definitions(sheet(DEFINITIONS))
    _metadata(sheet(METADATA), model)

    neutralize_formulas(workbook)
    _order_and_tabs(workbook)
    workbook.save(path)
    return model
