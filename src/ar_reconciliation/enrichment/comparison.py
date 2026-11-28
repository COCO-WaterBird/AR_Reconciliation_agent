"""
Before / after: what the structured inputs change.

``run_with_comparison`` runs the same bank and AR files twice - the baseline
(the two required workbooks only) and the enriched run (plus the structured
inputs) - and measures both with the same definitions:

* **Automation rate** - receipts whose cash the engine applied without a
  reviewer, divided by in-scope receipts (report metric "Auto-Match Rate");
* **Review workload** - receipts with Review Status "Pending Review";
* **Receipts needing AI investigation** - receipts the engine could not
  settle, which the AI agent analyzes (one LLM call each when AI-assisted
  investigation is on); with the deterministic backend no LLM call is made;
* **Matching accuracy** - against a labeled ground-truth file
  (``expected_matches.csv``): the share of labeled receipts whose outcome
  (automatic match with the expected allocation, or review) is as expected,
  and the share of automatic matches among them that are correct.

Amounts are reported per currency; nothing is added across currencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ar_reconciliation.core.primitives import money_to_cents, normalize_key, normalize_text

__all__ = ["COMPARISON_SHEET", "measure", "measure_accuracy", "comparison_frame", "run_with_comparison"]

COMPARISON_SHEET = "Enrichment Comparison"


def _cents(value: Any) -> int:
    try:
        return money_to_cents(value)
    except Exception:
        return 0


def measure_accuracy(result_path: Path, expected: pd.DataFrame | None) -> dict[str, Any]:
    if expected is None or expected.empty:
        return {"labeled": 0}
    bank = pd.read_excel(result_path, sheet_name="Processed Bank Receipts")
    ar = pd.read_excel(result_path, sheet_name="Reconciled Open AR")
    bank_by = {normalize_key(r["Bank Transaction ID"]): r for _, r in bank.iterrows()}
    agree = auto = auto_correct = 0
    for _, row in expected.iterrows():
        txn = normalize_key(row["bank_transaction_id"])
        receipt = bank_by.get(txn)
        if receipt is None:
            continue
        applied = _cents(receipt.get("AR Applied Amount Total"))
        actual = "Auto" if applied > 0 else "Review"
        correct = actual == row["expected_outcome"]
        if actual == "Auto":
            auto += 1
            wanted = {}
            for part in str(row.get("expected_allocations", "")).split(";"):
                if ":" in part:
                    invoice, amount = part.split(":")
                    wanted[normalize_key(invoice)] = money_to_cents(amount)
            touched = ar[ar["Matched Bank Transaction ID"].astype(object).fillna("").astype(str)
                         .str.upper().str.contains(str(row["bank_transaction_id"]).upper(), regex=False)]
            got = {normalize_key(r["Invoice Number"]) for _, r in touched.iterrows()}
            correct = correct and set(wanted) == got and sum(wanted.values()) == applied
            auto_correct += int(correct)
        agree += int(correct)
    labeled = len(expected)
    return {"labeled": labeled, "agree": agree, "auto": auto, "auto_correct": auto_correct}


def measure(result_path: Path, expected: pd.DataFrame | None = None) -> dict[str, Any]:
    from ar_reconciliation.reporting import build_report_model

    excel = pd.ExcelFile(result_path)
    sheets = {n: pd.read_excel(excel, n) for n in excel.sheet_names
              if n in {"Processed Bank Receipts", "Reconciled Open AR", "Unmatched Bank Receipts",
                       "AI Exception Review", "Approval Records", "Data Quality Checks", "Summary",
                       "Proposed Deductions"}}
    model = build_report_model(sheets)
    review = sheets.get("AI Exception Review", pd.DataFrame())
    llm_calls = int(review["Analysed By"].astype(object).fillna("").astype(str).str.startswith("LLM").sum()) \
        if "Analysed By" in review.columns else 0
    deductions = sheets.get("Proposed Deductions", pd.DataFrame())
    cash = model.metrics.cash_by_currency
    return {
        "receipts": model.metrics.total_receipts,
        "auto_matched": model.metrics.auto_matched,
        "automation_rate": model.metrics.auto_match_rate,
        "pending_review": model.metrics.pending_review,
        "needs_ai": len(sheets.get("Unmatched Bank Receipts", pd.DataFrame())),
        "llm_calls": llm_calls,
        "applied": dict(zip(cash["Currency"], cash["Applied to AR"])) if not cash.empty else {},
        "deductions_authorized": int((deductions.get("Authorization", pd.Series(dtype=str)) == "Authorized").sum()),
        "deductions_unauthorized": int((deductions.get("Authorization", pd.Series(dtype=str)) == "Unauthorized").sum()),
        "controls": model.control_status,
        "accuracy": measure_accuracy(result_path, expected),
    }


def _pct(value) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def comparison_frame(before: dict, after: dict) -> pd.DataFrame:
    def ratio(acc: dict, num: str, den: str) -> float | None:
        return (acc[num] / acc[den]) if acc.get(den) else None

    rows = [
        ("Automation rate", _pct(before["automation_rate"]), _pct(after["automation_rate"]),
         "Receipts the engine applied without a reviewer / in-scope receipts."),
        ("Receipts matched automatically", before["auto_matched"], after["auto_matched"],
         "Deterministic engine (bank + AR, plus structured inputs in the enriched run)."),
        ("Review workload (Pending Review)", before["pending_review"], after["pending_review"],
         "Receipts awaiting a reviewer decision."),
        ("Receipts needing AI investigation", before["needs_ai"], after["needs_ai"],
         "Receipts the engine could not settle; one LLM call each with AI-assisted investigation."),
        ("LLM calls in this run", before["llm_calls"], after["llm_calls"],
         "Measured from the AI Exception Review sheet (0 with the deterministic backend)."),
    ]
    for label, key, den in (("Matching accuracy - labeled outcome agreement", "agree", "labeled"),
                            ("Matching accuracy - correct automatic matches", "auto_correct", "auto")):
        b, a = before["accuracy"], after["accuracy"]
        if a.get("labeled"):
            rows.append((label, f"{_pct(ratio(b, key, den))} ({b.get(key, 0)}/{b.get(den, 0)})",
                         f"{_pct(ratio(a, key, den))} ({a.get(key, 0)}/{a.get(den, 0)})",
                         f"Against expected_matches.csv ({a['labeled']} labeled receipts)."))
    for currency in sorted(set(before["applied"]) | set(after["applied"])):
        rows.append((f"Cash applied to AR ({currency})", f"{before['applied'].get(currency, 0) / 100:,.2f}",
                     f"{after['applied'].get(currency, 0) / 100:,.2f}", "Per currency; never combined."))
    rows.append(("Deductions proposed (Authorized / Unauthorized)",
                 f"{before['deductions_authorized']} / {before['deductions_unauthorized']}",
                 f"{after['deductions_authorized']} / {after['deductions_unauthorized']}",
                 "Never applied automatically; a reviewer decides (G1)."))
    rows.append(("Financial controls", before["controls"], after["controls"], "Every currency."))
    return pd.DataFrame(rows, columns=["Metric", "Baseline (Bank + AR)", "Enriched", "Definition"])


def run_with_comparison(bank_path: Path | None, ar_path: Path | None, output_path: Path, inputs,
                        expected: Path | pd.DataFrame | None = None, verbose: bool = False):
    """Run baseline and enriched; write the comparison into the enriched workbook."""
    from ar_reconciliation.services import run_reconciliation

    output_path = Path(output_path)
    baseline_path = output_path.with_name(f"{output_path.stem}_baseline.xlsx")
    if baseline_path.exists():
        baseline_path.unlink()
    run_reconciliation(bank_path=bank_path, ar_path=ar_path, output_path=baseline_path, verbose=False)
    result = run_reconciliation(bank_path=bank_path, ar_path=ar_path, output_path=output_path,
                                verbose=verbose, enrichment_inputs=inputs)
    if isinstance(expected, (str, Path)):
        expected = pd.read_csv(expected, keep_default_na=False)
    frame = comparison_frame(measure(baseline_path, expected), measure(output_path, expected))
    with pd.ExcelWriter(output_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        frame.to_excel(writer, sheet_name=COMPARISON_SHEET, index=False)
    from ar_reconciliation.io.report_views import refresh_report_views
    from ar_reconciliation.io.workbook import apply_workbook_formatting

    apply_workbook_formatting(output_path)
    refresh_report_views(output_path)
    return result, frame
