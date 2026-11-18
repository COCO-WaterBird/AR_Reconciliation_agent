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
REVIEW_SHEETS = ("AI Exception Review", "AI Exception Log")


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


def apply_workbook_formatting(output_file: Path) -> None:
    """
    Make the workbook usable as a review sheet rather than a data dump.

    Each reviewable sheet gets:

      * a frozen header row, so the column names stay visible while the
        reviewer scrolls through 200 rows,
      * an auto-filter, so the reviewer can narrow the sheet down to the
        handful of receipts they actually need to look at, and
      * sensible column widths, so a receipt is readable without widening
        every column by hand.
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:  # pragma: no cover - openpyxl is a hard dependency
        return

    # Sheets a reviewer works through, with the number of columns to freeze.
    freeze_specs = {
        "Reconciled Open AR": 3,
        "Processed Bank Receipts": 2,
        "Unmatched Bank Receipts": 2,
        "Data Quality Checks": 2,
        "AI Exception Review": 2,
        "AI Exception Log": 2,
    }

    header_fill = PatternFill(
        start_color="FF1F3864", end_color="FF1F3864", fill_type="solid"
    )
    header_font = Font(bold=True, color="FFFFFFFF")

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

            # Size the columns.  Long free-text columns (memos, reasons,
            # explanations) are capped so they do not push the numeric
            # columns off the screen.
            for column_index in range(1, last_column + 1):
                letter = get_column_letter(column_index)
                header = sheet.cell(row=1, column=column_index).value or ""
                header = str(header).lower()
                if any(
                    token in header
                    for token in ("memo", "reason", "note", "explanation", "treatment")
                ):
                    width = 45
                elif "date" in header:
                    width = 12
                elif "amount" in header or "total" in header or "difference" in header:
                    width = 16
                elif "invoice" in header:
                    width = 18
                else:
                    width = 22

                sheet.column_dimensions[letter].width = width

        workbook.save(output_file)
    except Exception:
        # Formatting is a convenience.  A workbook that cannot be prettified
        # is still a correct workbook, so never fail the run over it.
        pass
