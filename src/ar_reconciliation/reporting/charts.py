"""
Native Excel charts for the report sheets.

Every chart references cells the exporter has just written from the report
model, so a chart can never show numbers that are not in the workbook.
Charts are 2-D, single-currency (or count-based), and a chart with no data is
replaced by a "No Data Available" note.
"""

from __future__ import annotations

from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.worksheet.worksheet import Worksheet

from ar_reconciliation.reporting.excel_styles import NOTE_FONT, TEAL

__all__ = ["bar_chart", "no_data"]


def no_data(ws: Worksheet, anchor: str, title: str) -> None:
    ws[anchor] = f"{title}: No Data Available"
    ws[anchor].font = NOTE_FONT


def bar_chart(
    ws: Worksheet,
    anchor: str,
    title: str,
    categories: tuple[int, int, int, int],
    values: tuple[int, int, int, int],
    y_title: str = "",
    percent: bool = False,
    horizontal: bool = False,
    width: float = 16,
    height: float = 7.5,
    from_rows: bool = False,
) -> None:
    """
    A 2-D bar chart.  ``categories`` and ``values`` are (min_col, min_row,
    max_col, max_row) on ``ws``.
    """
    min_col, min_row, max_col, max_row = values
    if max_row < min_row or max_col < min_col:
        no_data(ws, anchor, title)
        return
    chart = BarChart()
    chart.type = "bar" if horizontal else "col"
    chart.title = title
    chart.style = 2
    chart.y_axis.title = y_title
    chart.legend = None
    chart.width = width
    chart.height = height
    data = Reference(ws, min_col=min_col, min_row=min_row, max_col=max_col, max_row=max_row)
    cats = Reference(ws, min_col=categories[0], min_row=categories[1], max_col=categories[2], max_row=categories[3])
    chart.add_data(data, titles_from_data=False, from_rows=from_rows)
    chart.set_categories(cats)
    for series in chart.series:
        series.graphicalProperties.solidFill = TEAL
        series.graphicalProperties.line.solidFill = TEAL
    chart.dataLabels = DataLabelList()
    chart.dataLabels.showVal = True
    if percent:
        chart.y_axis.numFmt = "0%"
        chart.y_axis.scaling.min = 0
        chart.y_axis.scaling.max = 1
    chart.y_axis.majorGridlines = None
    chart.y_axis.delete = False
    chart.x_axis.delete = False
    ws.add_chart(chart, anchor)
