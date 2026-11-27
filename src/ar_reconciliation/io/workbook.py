"""
Reading and writing workbooks.

Two concerns live here:

  * validation - checking that a source workbook has the columns the engine
    needs, and making sure the output columns can hold what gets written to
    them;
  * presentation - freezing headers, adding filters and sizing columns so
    the workbook is a review surface rather than a data dump.

Source workbooks are never written to.  Output always goes to a separate
path.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

__all__ = [
    "require_columns",
    "ensure_output_columns",
    "read_preserved_sheets",
    "apply_workbook_formatting",
    "REVIEW_SHEETS",
]

#: The sheets the AI exception agent owns.  The reconciliation engine
#: rewrites its output workbook from scratch, so these have to be carried
#: over or a reviewer's approval is destroyed.
REVIEW_SHEETS = (
    "AI Exception Review",
    "AI Exception Log",
    # The evidence and the audit records are carried over too: the audit
    # trail is append-only and must survive a re-run of the engine.
    "Supporting Documents",
    "Extracted Evidence",
    "Approval Records",
    "Audit Trail",
)


def require_columns(df: pd.DataFrame, required: list[str], file_label: str) -> None:
    """
    Fail loudly when a source workbook is missing a column the engine needs.

    The error lists what is available, because the usual cause is a renamed
    or re-ordered export.
    """
    missing = [col for col in required if col not in df.columns]
    if missing:
        available = "\n  - ".join(map(str, df.columns))
        raise ValueError(
            f"{file_label} is missing required columns: {missing}\n\n"
            f"Available columns:\n  - {available}"
        )


def ensure_output_columns(df: pd.DataFrame, columns: list[str]) -> None:
    """
    Make sure every output column exists and can hold mixed content.

    These columns receive both strings ("2026-06-01, 2026-06-24" for a
    multi-payment invoice) and date objects, so they have to be plain object
    columns.  Assigning "" to a new column yields the pandas 3 ``str`` dtype,
    which rejects a Timestamp, hence the explicit dtype.
    """
    for col in columns:
        if col not in df.columns:
            df[col] = pd.Series([""] * len(df), index=df.index, dtype=object)
        elif df[col].dtype.kind == "f":
            df[col] = df[col].astype("object")
        elif df[col].dtype != object:
            df[col] = df[col].astype("object")


def read_preserved_sheets(output_file: Path) -> dict[str, pd.DataFrame]:
    """
    Read the sheets this engine does not own, so they survive a re-run.

    The AI exception agent writes its review sheet into this workbook.  The
    engine regenerates the workbook from scratch, which would otherwise throw
    the review sheet - and with it every human approval - away.
    """
    preserved: dict[str, pd.DataFrame] = {}
    if not output_file.exists():
        return preserved
    try:
        excel = pd.ExcelFile(output_file)
    except Exception:
        return preserved

    for sheet_name in REVIEW_SHEETS:
        if sheet_name not in excel.sheet_names:
            continue
        try:
            frame = pd.read_excel(output_file, sheet_name=sheet_name)
        except Exception:
            continue
        # Keep a sheet that has headers even if it has no rows yet, so an
        # empty audit log still shows its columns.  A sheet with neither rows
        # nor columns carries no information and is dropped.
        if not frame.empty or len(frame.columns) > 0:
            preserved[sheet_name] = frame

    return preserved


#: The corporate palette used by the exported report.
#:
#: Navy is the header fill, the four status colours are the tints a finance
#: manager expects to see on a reconciliation review sheet, and the zebra
#: stripe is what makes a 200-row sheet readable without banding every row
#: in a heavy colour.
NAVY = "FF17365D"
WHITE = "FFFFFFFF"
ZEBRA = "FFF2F5F9"
BORDER = "FFD6DCE4"

STATUS_FILLS = {
    # Confirmed: the deterministic engine settled it.
    "green": "FFE2EFDA",
    # Unmatched: nothing was reconciled.
    "red": "FFFCE4E4",
    # AI suggestion: a proposal waiting for a human.
    "yellow": "FFFFF2CC",
    # Exception: needs escalation.
    "orange": "FFFCE4D6",
}

#: The font stack.  Aptos is the current Excel default; Calibri is the
#: fallback for older Excel and for LibreOffice.  Excel for Mac resolves
#: both, so the report renders the same on either platform.
FONT_NAME = "Aptos"
FONT_FALLBACK = "Calibri"

#: Currency, with negatives in parentheses.  This is the format US finance
#: reporting expects on a reconciliation.
CURRENCY_FORMAT = '$#,##0.00;($#,##0.00)'
NUMBER_FORMAT = '#,##0.00;(#,##0.00)'
PERCENT_FORMAT = '0.0%'
DATE_FORMAT = 'MM/DD/YYYY'

#: A cell holding an ISO date as text.  Used to recognise a date column
#: whose header does not say "date" - the Summary sheet's "Value" column
#: holds amounts, and must not be turned into a date.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?")

#: Columns whose values are money.  Matched case-insensitively against the
#: header, so "Gross Receipt Amount", "bank fees" and "AR applied amount
#: total" all qualify.
_CURRENCY_TOKENS = ("amount", "fee", "total", "difference", "balance", "price")
#: Columns that hold a date.  Matched on word boundaries: "Candidate
#: Invoice Count" contains the letters "date" inside "Candidate", and a
#: substring match would turn a count into a date.
_DATE_TOKENS = ("date", "posted", "due")
#: Columns that hold a share or rate.
_PERCENT_TOKENS = ("percent", "percentage", "rate", "ratio")
#: Free-text columns that should wrap rather than run off the page.
_TEXT_TOKENS = (
    "memo", "reason", "note", "explanation", "treatment", "detail",
    "quote", "citation", "summary", "conflict", "allocation", "adjustments",
)


def _has_token(name: str, tokens: tuple[str, ...]) -> bool:
    """
    Match a header token on word boundaries.

    Splitting on non-alphanumeric characters means "Candidate Invoice Count"
    does not match "date" (it only appears inside "Candidate"), while
    "Posted Date" and "Due Date" still do.

    A token also matches when it starts a word, so "fee" matches "Bank Fees"
    and "amount" matches "Amount Total".
    """
    words = set(re.split(r"[^a-z0-9]+", name.lower()))
    for word in words:
        for token in tokens:
            if word == token or word.startswith(token):
                return True
    return False


def _is_datetime_value(value: object) -> bool:
    """True when a cell holds a real date rather than a number."""
    if isinstance(value, datetime):
        return True
    if isinstance(value, date):
        return True
    return isinstance(value, str) and bool(_ISO_DATE.match(value.strip()))


def _column_kind(header: str) -> str:
    """
    Classify a column by its header so the right number format is applied.

    Dates are checked before currency because "Matched Payment Date" contains
    neither of the currency tokens, but "Open Amount" has to win over the
    generic fallback.
    """
    name = str(header).strip().lower()
    if _has_token(name, _DATE_TOKENS):
        return "date"
    if _has_token(name, _PERCENT_TOKENS):
        return "percent"
    if _has_token(name, _CURRENCY_TOKENS):
        return "currency"
    return "text"


def _is_text_column(header: str) -> bool:
    return _has_token(str(header).strip().lower(), _TEXT_TOKENS)


def _status_fill(value: object) -> str | None:
    """
    Map a status cell to its tint.

    Only exact matches on the values the project actually produces are
    coloured.  A cell with an unexpected value keeps the zebra stripe, so a
    new status never ends up with a misleading colour.
    """
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"reconciled", "applied", "matched", "valid", "final"}:
        return STATUS_FILLS["green"]
    if text in {"unmatched", "unapplied", "rejected", "failed"}:
        return STATUS_FILLS["red"]
    if text in {"pending review", "recommend match", "suggested", "downgraded"}:
        return STATUS_FILLS["yellow"]
    if text in {
        "exception", "hold / escalate", "manual review", "return to customer",
        "needs review", "more evidence requested",
    }:
        return STATUS_FILLS["orange"]
    return None


def apply_workbook_formatting(output_file: Path) -> None:
    """
    Make the workbook usable as a review sheet rather than a data dump.

    Each reviewable sheet gets:

      * a frozen header row, so the column names stay visible while the
        reviewer scrolls through 200 rows,
      * an auto-filter, so the reviewer can narrow the sheet down to the
        handful of receipts they actually need to look at,
      * sensible column widths, so a receipt is readable without widening
        every column by hand, and
      * the corporate report styling: a navy header band, zebra-striped
        rows, money/dates in their reporting formats, and a status tint on
        the columns a reviewer sorts by.

    Only the presentation changes.  No cell value, sheet name, column name,
    column order or row count is touched, so the workbook stays readable by
    pandas and by the approval workflow.
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError:  # pragma: no cover - openpyxl is a hard dependency
        return

    # Sheets a reviewer works through, with the number of columns to freeze.
    freeze_specs = {
        "Reconciled Open AR": 3,
        "Processed Bank Receipts": 2,
        "Unmatched Bank Receipts": 2,
        "Data Quality Checks": 2,
        "Summary": 1,
        "AI Exception Review": 2,
        "AI Exception Log": 2,
        "Supporting Documents": 2,
        "Extracted Evidence": 2,
        "Approval Records": 2,
        "Audit Trail": 2,
        "Control Breaks": 1,
    }

    header_fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    header_font = Font(name=FONT_NAME, bold=True, color=WHITE, size=11)
    zebra_fill = PatternFill(start_color=ZEBRA, end_color=ZEBRA, fill_type="solid")
    body_font = Font(name=FONT_NAME, size=10)
    thin_side = Side(style="thin", color=BORDER)
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    try:
        workbook = load_workbook(output_file)
    except Exception:
        return

    try:
        for sheet_name, freeze_cols in freeze_specs.items():
            if sheet_name not in workbook.sheetnames:
                continue
            sheet = workbook[sheet_name]
            if sheet.max_row < 1:
                continue

            last_column = sheet.max_column
            last_row = sheet.max_row

            # Freeze the header row (and the identifying columns).
            sheet.freeze_panes = sheet.cell(row=2, column=freeze_cols + 1)

            # Auto-filter across the used range, but only when there is data
            # to filter: Excel treats a filter on a single empty row as a
            # broken filter.
            if last_row > 1 and last_column > 0:
                sheet.auto_filter.ref = (
                    f"A1:{get_column_letter(last_column)}{last_row}"
                )

            # Style the header row.
            for cell in sheet[1]:
                if cell.value is not None:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(
                        horizontal="center", vertical="center", wrap_text=True
                    )
                    cell.border = cell_border
            sheet.row_dimensions[1].height = 30

            # Classify every column once, by its header.
            kinds = [
                _column_kind(sheet.cell(row=1, column=index).value or "")
                for index in range(1, last_column + 1)
            ]
            text_columns = {
                index
                for index in range(1, last_column + 1)
                if _is_text_column(sheet.cell(row=1, column=index).value or "")
            }
            # The columns a reviewer sorts or filters by, so the status tint
            # lands where it is read.
            status_columns = {
                index
                for index in range(1, last_column + 1)
                if str(sheet.cell(row=1, column=index).value or "").strip()
                in {
                    "Suggested Recon Status",
                    "Suggested Bank Status",
                    "Status",
                    "Recommended Action",
                    "AI Exception Type",
                }
            }

            for row_index in range(2, last_row + 1):
                stripe = (row_index % 2) == 0
                for column_index in range(1, last_column + 1):
                    cell = sheet.cell(row=row_index, column=column_index)
                    if cell.value is None:
                        continue

                    cell.font = body_font
                    cell.border = cell_border

                    kind = kinds[column_index - 1]
                    if kind == "date" and _is_datetime_value(cell.value):
                        cell.number_format = DATE_FORMAT
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    elif kind == "currency":
                        cell.number_format = CURRENCY_FORMAT
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif kind == "percent":
                        cell.number_format = PERCENT_FORMAT
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif isinstance(cell.value, (int, float)) and not isinstance(
                        cell.value, bool
                    ):
                        # A plain number in a column whose header does not name
                        # a format - a count in the Summary sheet, for example.
                        # Right-aligned and grouped, but not given a currency
                        # sign it does not have.
                        cell.number_format = NUMBER_FORMAT
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    else:
                        cell.alignment = Alignment(horizontal="left", vertical="center")

                    if column_index in text_columns:
                        cell.alignment = Alignment(
                            horizontal="left", vertical="top", wrap_text=True
                        )

                    # The status tint wins over the zebra stripe: a reviewer
                    # scanning for exceptions should not have to fight the
                    # banding to find them.
                    tint = None
                    if column_index in status_columns:
                        tint = _status_fill(cell.value)
                    if tint:
                        cell.fill = PatternFill(
                            start_color=tint, end_color=tint, fill_type="solid"
                        )
                    elif stripe:
                        cell.fill = zebra_fill

            # Size the columns from their content.  Long free-text columns
            # (memos, reasons, explanations) are capped so they do not push
            # the numeric columns off the screen.
            for column_index in range(1, last_column + 1):
                letter = get_column_letter(column_index)
                header = sheet.cell(row=1, column=column_index).value or ""
                header_text = str(header)
                longest = len(header_text)
                for row_index in range(2, min(last_row, 60) + 1):
                    value = sheet.cell(row=row_index, column=column_index).value
                    if value is None:
                        continue
                    length = len(str(value))
                    if length > longest:
                        longest = length
                        if longest > 60:
                            break

                if column_index in text_columns:
                    width = 45
                elif kinds[column_index - 1] == "date":
                    width = 12
                elif kinds[column_index - 1] in {"currency", "percent"}:
                    width = 16
                elif "invoice" in header_text.lower():
                    width = 18
                else:
                    # Fit the content, within reason.
                    width = max(10, min(longest + 2, 30))

                sheet.column_dimensions[letter].width = width

        workbook.save(output_file)
    except Exception:
        # Formatting is a convenience.  A workbook that cannot be prettified
        # is still a correct workbook, so never fail the run over it.
        pass
