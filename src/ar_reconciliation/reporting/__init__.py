"""
Enterprise reporting layer.

Analytics (pure functions over DataFrames): :mod:`statuses`, :mod:`metrics`,
:mod:`controls_matrix`, :mod:`exceptions`, :mod:`aging`, :mod:`attention`,
assembled by :mod:`builder`.  Rendering: :mod:`excel_styles`, :mod:`charts`,
:mod:`excel_exporter`.  Thresholds: ``reporting_config.json``.
"""

from ar_reconciliation.reporting.builder import ReportModel, build_report_model
from ar_reconciliation.reporting.config import ReportingConfig, load_config
from ar_reconciliation.reporting.excel_exporter import (
    DASHBOARD,
    REPORT_SHEETS,
    write_enterprise_report,
)

__all__ = [
    "DASHBOARD",
    "REPORT_SHEETS",
    "ReportModel",
    "ReportingConfig",
    "build_report_model",
    "load_config",
    "write_enterprise_report",
]
