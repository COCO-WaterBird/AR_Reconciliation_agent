"""
Corporate finance styling for the report sheets.

One palette, one font family, one set of number formats, used by every
report sheet so the workbook reads as one package.  Status colors always come
with the status word; color is never the only signal.
"""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

__all__ = [
    "NAVY", "TEAL", "GREEN", "AMBER", "RED", "LIGHT_GRAY", "MID_GRAY", "WHITE", "FONT",
    "AMOUNT_FORMAT", "COUNT_FORMAT", "PERCENT_FORMAT", "DATE_FORMAT",
    "TITLE_FONT", "SUBTITLE_FONT", "SECTION_FONT", "HEADER_FONT", "BODY_FONT", "NOTE_FONT",
    "KPI_VALUE_FONT", "KPI_LABEL_FONT", "LINK_FONT",
    "HEADER_FILL", "ZEBRA_FILL", "CARD_FILL", "THIN_BORDER", "WRAP", "CENTER", "LEFT", "RIGHT",
    "fill", "status_style",
]

NAVY = "17365D"
TEAL = "287E86"
GREEN = "16805D"
AMBER = "D49A32"
RED = "C03939"
LIGHT_GRAY = "F3F5F7"
MID_GRAY = "8A94A0"
WHITE = "FFFFFF"
FONT = "Aptos"

#: Amounts carry no currency symbol: the ISO code is always in its own column.
AMOUNT_FORMAT = "#,##0.00;(#,##0.00)"
COUNT_FORMAT = "#,##0"
PERCENT_FORMAT = "0.0%"
DATE_FORMAT = "yyyy-mm-dd"

TITLE_FONT = Font(name=FONT, size=20, bold=True, color=NAVY)
SUBTITLE_FONT = Font(name=FONT, size=10, color="555555")
SECTION_FONT = Font(name=FONT, size=13, bold=True, color=NAVY)
HEADER_FONT = Font(name=FONT, size=10, bold=True, color=WHITE)
BODY_FONT = Font(name=FONT, size=10, color="222222")
NOTE_FONT = Font(name=FONT, size=9, italic=True, color="555555")
KPI_VALUE_FONT = Font(name=FONT, size=20, bold=True, color=TEAL)
KPI_LABEL_FONT = Font(name=FONT, size=9, bold=True, color="555555")
LINK_FONT = Font(name=FONT, size=10, color=TEAL, underline="single")


def fill(color: str) -> PatternFill:
    return PatternFill(start_color=color, end_color=color, fill_type="solid")


HEADER_FILL = fill(NAVY)
ZEBRA_FILL = fill(LIGHT_GRAY)
CARD_FILL = fill(LIGHT_GRAY)
_SIDE = Side(style="thin", color="D6DCE4")
THIN_BORDER = Border(left=_SIDE, right=_SIDE, top=_SIDE, bottom=_SIDE)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="top")
RIGHT = Alignment(horizontal="right", vertical="top")

_STATUS = {
    # Passed / healthy
    "PASS": GREEN, "PASSED": GREEN, "COMPLETED": GREEN, "FULLY MATCHED": GREEN, "APPROVED": GREEN,
    "LOW": GREEN, "NOT REQUIRED": "6B7B8C",
    # Needs attention
    "WARNING": AMBER, "PASSED WITH WARNINGS": AMBER, "PENDING REVIEW": AMBER, "MEDIUM": AMBER,
    "PARTIALLY MATCHED": AMBER, "MORE EVIDENCE REQUESTED": AMBER, "APPROVED - NOT YET APPLIED": AMBER,
    # Failed / high risk
    "FAIL": RED, "FAILED": RED, "HIGH": RED, "UNMATCHED": RED, "REJECTED": RED,
    # Neutral
    "N/A": MID_GRAY, "NOT EVALUATED": MID_GRAY, "NOT INTEGRATED (NO ERP/GL POSTING)": MID_GRAY,
    "NOT POSTED (ERP/GL NOT INTEGRATED)": MID_GRAY, "REFUND / REVERSAL": MID_GRAY,
    "PREVIOUSLY RECONCILED": MID_GRAY, "NO DATA": MID_GRAY,
}


def status_style(value: object) -> tuple[PatternFill, Font] | None:
    """Fill and font for a status word, or None when it is not a status."""
    word = str(value or "").strip().upper()
    if word.startswith("PENDING ("):
        word = "PENDING REVIEW"
    color = _STATUS.get(word)
    if color is None:
        return None
    return fill(color), Font(name=FONT, size=10, bold=True, color=WHITE)
