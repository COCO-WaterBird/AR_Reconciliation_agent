"""
The Control Reconciliation matrix: control x currency, with PASS / FAIL /
WARNING / N/A.

The figures come from :mod:`ar_reconciliation.services.controls`, which stays
the single source of truth for the controls; this module only lays them out.
Each control is evaluated per currency, so a difference in one currency can
never be hidden by an opposite difference in another.

A PASS means the books tie.  It does not mean every receipt is matched or
every exception resolved; those are reported separately.
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.services.controls import ControlResult

__all__ = ["CONTROL_COLUMNS", "control_matrix", "overall_control_status"]

CONTROL_COLUMNS = ["Control", "Currency", "Expected", "Actual", "Variance", "Status", "What It Proves"]


def _row(control: str, currency: str, expected: int | None, actual: int | None, status: str, proves: str,
         unit: str = "amount") -> dict:
    variance = None if expected is None or actual is None else actual - expected
    return {
        "Control": control,
        "Currency": currency,
        "Expected": expected,
        "Actual": actual,
        "Variance": variance,
        "Status": status,
        "What It Proves": proves,
        "_unit": unit,
    }


def control_matrix(controls: ControlResult | None, data_quality: pd.DataFrame | None) -> pd.DataFrame:
    rows: list[dict] = []
    if controls is not None:
        for currency, sub in sorted(controls.by_currency.items()):
            accounted = sub.applied_cents + sub.unapplied_cents + sub.adjustment_cents + sub.held_cents + sub.skipped_cents
            cash_ok = sub.cash_difference_cents == 0 and not sub.cash_breaks
            rows.append(_row(
                "Cash Control", currency, sub.receipts_gross_cents, accounted,
                "PASS" if cash_ok else "FAIL",
                "Every receipt's gross amount is applied, unapplied, adjusted, held or skipped - no cash is unaccounted for.",
            ))
            rows.append(_row(
                "Bank-to-AR Applied Cash", currency, sub.applied_cents, sub.ar_applied_cents,
                "PASS" if sub.applied_difference_cents == 0 else "FAIL",
                "Cash the bank side applied equals cash the AR side received.",
            ))
            expected_remaining = sub.ar_opening_cents - sub.ar_applied_cents - sub.ar_adjustment_cents
            roll_ok = sub.rollforward_difference_cents == 0 and not sub.rollforward_breaks
            rows.append(_row(
                "AR Roll-forward", currency, expected_remaining, sub.ar_remaining_cents,
                "PASS" if roll_ok else "FAIL",
                "Opening AR - cash applied - approved adjustments = remaining AR, invoice by invoice.",
            ))
    dq = data_quality if data_quality is not None else pd.DataFrame()
    issues = len(dq)
    duplicates = 0
    if issues and "Issue" in dq.columns:
        duplicates = int(dq["Issue"].astype(str).str.contains("appears", case=False).sum())
    rows.append(_row(
        "Duplicate Transaction Check", "All", 0, duplicates,
        "PASS" if duplicates == 0 else "WARNING",
        "No bank transaction ID or customer invoice number appears twice (duplicates are held, not applied).",
        unit="count",
    ))
    rows.append(_row(
        "Data Quality Control", "All", 0, issues,
        "PASS" if issues == 0 else "WARNING",
        "Input rows are readable and consistent; issues are held out of matching and listed in Data Quality Checks.",
        unit="count",
    ))
    if controls is None:
        rows.insert(0, _row("Financial Controls", "All", None, None, "N/A",
                            "No reconciled data was available to evaluate.", unit="count"))
    return pd.DataFrame(rows, columns=CONTROL_COLUMNS + ["_unit"])


def overall_control_status(matrix: pd.DataFrame) -> str:
    statuses = set(matrix["Status"]) if not matrix.empty else {"N/A"}
    if "FAIL" in statuses:
        return "Failed"
    if statuses <= {"N/A"}:
        return "Not Evaluated"
    if "WARNING" in statuses:
        return "Passed with Warnings"
    return "Passed"
