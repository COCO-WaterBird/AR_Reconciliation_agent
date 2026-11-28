#!/usr/bin/env python
"""
Compare the business results of two reconciliation workbooks.

    python scripts/compare_runs.py snapshot RESULT.xlsx OUT.json
    python scripts/compare_runs.py compare BEFORE.json AFTER.json

The snapshot holds what a reporting change must not move: matched bank
receipt IDs, matched AR invoice IDs, applied amounts and remaining AR by
currency, unmatched receipts, control differences by currency and the AI
exception classification (deterministic backend).  Amounts are compared in
integer cents and are never summed across currencies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.core.primitives import money_to_cents, normalize_text  # noqa: E402
from ar_reconciliation.services.controls import compute_output_controls  # noqa: E402


def _cents(value: object) -> int:
    try:
        return money_to_cents(value)
    except Exception:
        return 0


def snapshot(path: Path) -> dict:
    excel = pd.ExcelFile(path)
    bank = pd.read_excel(excel, "Processed Bank Receipts")
    ar = pd.read_excel(excel, "Reconciled Open AR")
    unmatched = pd.read_excel(excel, "Unmatched Bank Receipts")
    review = pd.read_excel(excel, "AI Exception Review") if "AI Exception Review" in excel.sheet_names else pd.DataFrame()

    bank_applied = bank["AR Applied Amount Total"].apply(_cents)
    ar_applied = ar["Applied Amount"].apply(_cents)
    adjustments = ar["Approved Adjustment Amount"].apply(_cents) if "Approved Adjustment Amount" in ar else 0
    remaining = (ar["Open Amount"].apply(_cents) - ar_applied - adjustments).clip(lower=0)

    def by_currency(frame: pd.DataFrame, values: pd.Series) -> dict:
        return {k: int(v) for k, v in values.groupby(frame["Currency"].astype(str)).sum().items()}

    controls = compute_output_controls(ar, bank)
    return {
        "matched_bank_receipts": sorted(bank.loc[bank_applied > 0, "Bank Transaction ID"].astype(str)),
        "matched_ar_invoices": sorted(ar.loc[ar_applied > 0, "Invoice Number"].astype(str)),
        "bank_applied_by_currency_cents": by_currency(bank, bank_applied),
        "ar_applied_by_currency_cents": by_currency(ar, ar_applied),
        "remaining_ar_by_currency_cents": by_currency(ar, remaining),
        "unmatched_receipts": sorted(unmatched["Bank Transaction ID"].astype(str)),
        "bank_status": {k: int(v) for k, v in bank["Suggested Bank Status"].value_counts().items()},
        "ar_status": {k: int(v) for k, v in ar["Suggested Recon Status"].value_counts().items()},
        "control_differences_cents": {
            currency: {
                "cash": sub.cash_difference_cents,
                "bank_vs_ar": sub.applied_difference_cents,
                "rollforward": sub.rollforward_difference_cents,
            }
            for currency, sub in controls.by_currency.items()
        },
        "ai_classification": {
            normalize_text(r["Bank Transaction ID"]): [
                normalize_text(r.get("AI Exception Type")),
                normalize_text(r.get("Recommended Action")),
                normalize_text(r.get("AI Confidence")),
            ]
            for _, r in review.iterrows()
        },
    }


def compare(before: dict, after: dict) -> list[str]:
    differences = []
    for key in before:
        if before[key] != after.get(key):
            differences.append(key)
    return differences


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "snapshot":
        Path(argv[2]).write_text(json.dumps(snapshot(Path(argv[1])), indent=2, sort_keys=True))
        print(f"Wrote {argv[2]}")
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        before = json.loads(Path(argv[1]).read_text())
        after = json.loads(Path(argv[2]).read_text())
        differences = compare(before, after)
        if not differences:
            print("No business-result differences: every compared result is identical.")
            return 0
        print("Differences in: " + ", ".join(differences))
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
