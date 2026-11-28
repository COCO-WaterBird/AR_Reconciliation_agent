"""
Compatibility entry point for the report sheets.

The report layer now lives in :mod:`ar_reconciliation.reporting`.  The five
D6 view sheets (Summary-first, Matched, Unmatched, AI Suggested, Evidence)
were replaced by the enterprise report sheets; see
``docs/reporting_redesign_plan.md`` for the old-to-new mapping.  The writers
keep calling :func:`refresh_report_views`, which now builds the enterprise
report.
"""

from __future__ import annotations

from pathlib import Path

from ar_reconciliation.reporting.excel_exporter import (
    LEGACY_VIEW_SHEETS,
    REPORT_SHEETS as _REPORT_SHEETS,
    write_enterprise_report,
)

__all__ = ["REPORT_SHEETS", "LEGACY_VIEW_SHEETS", "refresh_report_views"]

#: The report sheets, in workbook order.
REPORT_SHEETS = tuple(_REPORT_SHEETS)


def refresh_report_views(path: Path) -> None:
    """Rebuild the report sheets (must run after the last other save)."""
    write_enterprise_report(Path(path))
