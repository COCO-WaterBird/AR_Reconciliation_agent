"""
Look at the reconciliation result from the terminal.

The result workbook is the deliverable, but it is also an Excel file, and
not every question about it is an Excel question.  This script answers the
questions that come up right after a run:

    python scripts/show_result.py            # the summary
    python scripts/show_result.py summary    # the same
    python scripts/show_result.py matches   # how the receipts were matched
    python scripts/show_result.py unmatched # the receipts that need a human
    python scripts/show_result.py review    # what the agent recommended
    python scripts/show_result.py ar        # the reconciled AR rows
    python scripts/show_result.py sheets    # what is in the workbook
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ar_reconciliation.config import RESULT_FILE  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 40)


def _load(sheet: str, path: Path) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet)


def show_summary(path: Path) -> None:
    print("\n=== Summary ===\n")
    summary = _load("Summary", path)
    width = max(len(str(v)) for v in summary["Metric"])
    for _, row in summary.iterrows():
        print(f"  {str(row['Metric']):<{width}}   {row['Value']}")


def show_sheets(path: Path) -> None:
    print("\n=== Sheets ===\n")
    excel = pd.ExcelFile(path)
    for name in excel.sheet_names:
        frame = pd.read_excel(path, sheet_name=name)
        print(f"  {name:<30} {len(frame):>5} rows x {len(frame.columns):>3} cols")


def show_matches(path: Path) -> None:
    print("\n=== How the AR rows were matched ===\n")
    ar = _load("Reconciled Open AR", path)
    if "Match Method" in ar.columns:
        print(ar["Match Method"].fillna("(unmatched)").value_counts().to_string())
    if "Suggested Recon Status" in ar.columns:
        print("\n=== Suggested AR status ===\n")
        print(
            ar["Suggested Recon Status"]
            .fillna("(blank)")
            .value_counts()
            .to_string()
        )


def show_ar(path: Path) -> None:
    print("\n=== Reconciled AR rows ===\n")
    ar = _load("Reconciled Open AR", path)
    cols = [
        c
        for c in (
            "ERP Customer ID",
            "Customer Name",
            "Invoice Number",
            "Currency",
            "Open Amount",
            "Applied Amount",
            "Residual Amount",
            "Match Method",
            "Suggested Recon Status",
        )
        if c in ar.columns
    ]
    applied = ar[ar["Applied Amount"].fillna("").astype(str).str.strip().ne("")]
    print(f"  {len(applied)} of {len(ar)} AR rows carry an applied amount.\n")
    print(applied[cols].head(20).to_string(index=False))


def show_unmatched(path: Path) -> None:
    print("\n=== Unmatched bank receipts ===\n")
    unmatched = _load("Unmatched Bank Receipts", path)
    print(f"  {len(unmatched)} receipts need a human decision.\n")
    if "Reason" in unmatched.columns:
        print("=== Why ===\n")
        print(unmatched["Reason"].fillna("(blank)").value_counts().to_string())
    cols = [
        c
        for c in (
            "Bank Transaction ID",
            "Customer Name",
            "Currency",
            "Net Amount",
            "Memo Scenario",
            "Reason",
            "Suggested Invoices",
            "Receipt vs Open Difference",
        )
        if c in unmatched.columns
    ]
    print("\n=== Detail ===\n")
    print(unmatched[cols].to_string(index=False))


def show_review(path: Path) -> None:
    print("\n=== AI exception review ===\n")
    review = _load("AI Exception Review", path)
    print(f"  {len(review)} rows.\n")
    if "Analysed By" in review.columns:
        print("=== Analysed by ===\n")
        print(review["Analysed By"].value_counts().to_string())
    if "AI Exception Type" in review.columns:
        print("\n=== Exception types ===\n")
        print(review["AI Exception Type"].value_counts().to_string())
    if "Recommended Action" in review.columns:
        print("\n=== Recommended actions ===\n")
        print(review["Recommended Action"].value_counts().to_string())
    if "Approval" in review.columns:
        approved = review["Approval"].fillna("").astype(str).str.strip().ne("")
        print(f"\n=== Approvals ===\n  {approved.sum()} of {len(review)} rows approved.")

    cols = [
        c
        for c in (
            "Bank Transaction ID",
            "AI Exception Type",
            "Recommended Action",
            "AI Confidence",
            "Proposed Invoice Numbers",
            "Proposed Applied Amount",
            "Approval",
        )
        if c in review.columns
    ]
    print("\n=== Detail ===\n")
    print(review[cols].to_string(index=False))


VIEWS = {
    "summary": show_summary,
    "sheets": show_sheets,
    "matches": show_matches,
    "ar": show_ar,
    "unmatched": show_unmatched,
    "review": show_review,
}


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]

    result = RESULT_FILE
    if "--result" in argv:
        result = Path(argv[argv.index("--result") + 1])

    if not result.exists():
        print(
            f"{result} not found. Run 'python scripts/run_reconciliation.py' first.",
            file=sys.stderr,
        )
        return 1

    view = args[0] if args else "summary"
    if view not in VIEWS:
        print(f"Unknown view '{view}'. Choose one of: {', '.join(VIEWS)}")
        return 1

    VIEWS[view](result)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
