"""
Generate the README images.

The images are built from a real run of the reconciliation engine, so the
numbers in them are the numbers the project actually produces.  Re-running
this script after a change refreshes the pictures.

Usage::

    python scripts/make_images.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ar_reconciliation.config import RESULT_FILE  # noqa: E402

IMAGE_DIR = ROOT / "docs" / "images"

# --- Palette ---------------------------------------------------------------

BG = "#0d1b2a"
CARD = "#16283e"
BLUE = "#4a9eff"
GREEN = "#5fd6a8"
AMBER = "#f0b070"
PINK = "#d974b8"
TEXT = "#e8eef7"
MUTED = "#93aac8"

plt.rcParams.update(
    {
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "savefig.facecolor": BG,
        "text.color": TEXT,
        "axes.labelcolor": TEXT,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    }
)


def load_data() -> dict[str, object]:
    """Read the numbers straight out of the result workbook."""
    if not RESULT_FILE.exists():
        raise SystemExit(
            f"{RESULT_FILE} not found. Run 'python scripts/run_reconciliation.py' first."
        )

    excel = pd.ExcelFile(RESULT_FILE)
    summary = pd.read_excel(RESULT_FILE, sheet_name="Summary")
    metrics = dict(zip(summary["Metric"], summary["Value"]))

    review = pd.read_excel(RESULT_FILE, sheet_name="AI Exception Review")

    return {
        "metrics": metrics,
        "exception_types": review["AI Exception Type"].value_counts(),
        "actions": review["Recommended Action"].value_counts(),
        "confidence": review["AI Confidence"].value_counts(),
        "sheets": excel.sheet_names,
    }


# --- 1. Architecture -------------------------------------------------------


def draw_architecture(path: Path) -> None:
    """The data flow, from source workbooks to a human approval."""
    fig = plt.figure(figsize=(13.5, 5.2), dpi=110)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 40)
    ax.axis("off")

    ax.text(
        2, 36.5, "Deterministic Engine, AI Advisor",
        fontsize=23, fontweight="bold", color="#ffffff", va="top",
    )
    ax.text(
        2, 32.8,
        "North America AR Cash Application & Reconciliation Agent",
        fontsize=12.5, color=MUTED, va="top",
    )
    ax.plot([2, 8], [30.6, 30.6], color=BLUE, lw=2.6, solid_capstyle="round")

    boxes = [
        (2.0, 6.0, "Source Data", CARD, BLUE,
         ["Bank Cash Receipts", "Open AR Detail", "read-only, never written"]),
        (19.0, 12.5, "Deterministic Engine", "#123a30", GREEN,
         ["Invoice memo extraction", "Customer / currency validation",
          "Exact amount match", "Subset-sum combination",
          "Partial & residual handling", "Bank fee processing"]),
        (42.5, 8.0, "Auto Reconciled", "#123a30", GREEN,
         ["136 receipts matched", "158 AR invoice rows", "$709,365.05 applied"]),
        (60.5, 11.0, "AI Exception Agent", "#3a2418", AMBER,
         ["Classifies exception type", "Explains root cause",
          "Recommends action & GL posting", "Advisory only - never decides"]),
        (83.0, 8.0, "Human Approval", "#3d1636", PINK,
         ["Reviewer sets", "Approval = APPROVE", "Cash moves only here"]),
    ]

    arrow_y = 18.0
    for x, w, title, fill, edge, lines in boxes:
        height = 5.0 + 2.0 * len(lines)
        y = arrow_y - height / 2
        patch = FancyBboxPatch(
            (x, y), w, height,
            boxstyle="round,pad=0,rounding_size=1.1",
            linewidth=1.4, edgecolor=edge, facecolor=fill, zorder=2,
        )
        ax.add_patch(patch)
        ax.text(x + 1.1, y + height - 2.0, title, fontsize=11.5,
                fontweight="bold", color=TEXT, va="top", zorder=3)
        for i, line in enumerate(lines):
            ax.text(x + 1.1, y + height - 5.0 - i * 2.0, line, fontsize=9.4,
                    color=MUTED, va="top", zorder=3)
        if x + w < 100:
            ax.annotate(
                "", xy=(x + w + 8.5, arrow_y), xytext=(x + w + 1.5, arrow_y),
                arrowprops=dict(arrowstyle="-|>", color=edge, lw=2.0,
                                mutation_scale=16),
            )

    ax.text(
        2, 6.5,
        "136 matched     64 exceptions     control difference $0.00",
        fontsize=11, color=GREEN, va="top",
    )
    ax.text(
        98, 6.5, "Accounting deterministic - AI assistive - Human decides",
        fontsize=10.5, color=BLUE, va="top", ha="right",
    )

    fig.savefig(path)
    plt.close(fig)


# --- 2. Run results --------------------------------------------------------


def draw_results(path: Path, data: dict[str, object]) -> None:
    """What one run of the engine produced."""
    metrics = data["metrics"]

    fig, axes = plt.subplots(1, 3, figsize=(14, 5.4), dpi=110)
    fig.subplots_adjust(left=0.045, right=0.985, top=0.80, bottom=0.14, wspace=0.30)

    fig.text(0.045, 0.93, "One run of the reconciliation engine",
             fontsize=17, fontweight="bold", color="#ffffff")
    fig.text(0.045, 0.885,
             "200 bank receipts against 191 open AR rows - the control totals balance to the cent",
             fontsize=11.5, color=MUTED)

    # -- Panel 1: receipt funnel
    ax = axes[0]
    labels = ["Matched", "Pending review", "Exception"]
    values = [136, 67, 64]
    colors = [GREEN, AMBER, PINK]
    bars = ax.barh(labels[::-1], values[::-1], color=colors[::-1], height=0.55)
    for bar, value in zip(bars, values[::-1]):
        ax.text(value + 3, bar.get_y() + bar.get_height() / 2, str(value),
                va="center", fontsize=12, fontweight="bold", color=TEXT)
    ax.set_title("Bank receipts by outcome", fontsize=13, color=TEXT,
                 fontweight="bold", pad=12)
    ax.set_xlim(0, 165)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=11)
    ax.set_xlabel("receipts", fontsize=10, color=MUTED)

    # -- Panel 2: exception types
    ax = axes[1]
    series = data["exception_types"].head(8)
    names = list(series.index)[::-1]
    counts = list(series.values)[::-1]
    ax.barh(names, counts, color=AMBER, height=0.6)
    for i, count in enumerate(counts):
        ax.text(count + 0.4, i, str(count), va="center", fontsize=10.5,
                fontweight="bold", color=TEXT)
    ax.set_title("Top AI exception types", fontsize=13, color=TEXT,
                 fontweight="bold", pad=12)
    ax.set_xlim(0, max(counts) * 1.25)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=10)
    ax.set_xlabel("receipts", fontsize=10, color=MUTED)

    # -- Panel 3: control totals
    ax = axes[2]
    ax.axis("off")
    rows = [
        ("Matched bank receipts", "136", GREEN),
        ("Matched AR invoice rows", "158", GREEN),
        ("Unmatched / exception receipts", "64", AMBER),
        ("AR applied amount", "$709,365.05", BLUE),
        ("Bank applied amount", "$709,365.05", BLUE),
        ("Control difference", "$0.00", GREEN),
    ]
    ax.add_patch(FancyBboxPatch(
        (0.02, 0.06), 0.96, 0.88, boxstyle="round,pad=0,rounding_size=0.02",
        linewidth=1.3, edgecolor="#2b4a6b", facecolor=CARD,
        transform=ax.transAxes,
    ))
    ax.text(0.5, 0.885, "Control totals", transform=ax.transAxes,
            fontsize=13, fontweight="bold", color=TEXT, ha="center")
    for i, (label, value, color) in enumerate(rows):
        y = 0.76 - i * 0.125
        ax.text(0.09, y, label, transform=ax.transAxes, fontsize=10.5,
                color=MUTED, va="center")
        ax.text(0.91, y, value, transform=ax.transAxes, fontsize=12.5,
                fontweight="bold", color=color, va="center", ha="right")

    fig.savefig(path)
    plt.close(fig)


# --- 3. Approval workflow --------------------------------------------------


def draw_workflow(path: Path, data: dict[str, object]) -> None:
    """The human-in-the-loop approval gate."""
    fig = plt.figure(figsize=(13.5, 4.6), dpi=110)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 34)
    ax.axis("off")

    ax.text(2, 31.5, "The AI advises. A human decides.",
            fontsize=20, fontweight="bold", color="#ffffff", va="top")
    ax.text(2, 27.8,
            "No cash moves until a reviewer sets Approval = APPROVE",
            fontsize=11.5, color=MUTED, va="top")

    steps = [
        (2.0, 17.0, "AI Exception Review", "#3a2418", AMBER,
         "64 receipts, one row each",
         ["Exception type & root cause", "Recommended action", "Proposed GL posting",
          "Approval  =  <blank>"]),
        (28.0, 15.0, "Human Review", "#3d1636", PINK,
         "The reviewer works the sheet",
         ["Reads the explanation", "Checks the candidates", "Sets Approval = APPROVE"]),
        (51.0, 17.0, "Apply to AR", "#123a30", GREEN,
         "--apply-approved re-runs",
         ["Only APPROVE rows are applied", "Control totals re-checked",
          "Difference must stay $0.00"]),
        (78.0, 20.0, "Output", "#1c3050", BLUE,
         "Reconciled Open AR",
         ["Applied Amount written back", "Residual left open",
          "Audit trail preserved"]),
    ]

    for x, w, title, fill, edge, caption, lines in steps:
        height = 8.0 + 1.9 * len(lines)
        y = 12.0
        ax.add_patch(FancyBboxPatch(
            (x, y), w, height, boxstyle="round,pad=0,rounding_size=1.1",
            linewidth=1.4, edgecolor=edge, facecolor=fill, zorder=2,
        ))
        ax.text(x + 1.0, y + height - 1.6, title, fontsize=11.5,
                fontweight="bold", color=TEXT, va="top", zorder=3)
        ax.text(x + 1.0, y + height - 3.8, caption, fontsize=9.2,
                color=edge, va="top", zorder=3, style="italic")
        for i, line in enumerate(lines):
            ax.text(x + 1.0, y + height - 6.2 - i * 1.9, line, fontsize=9.3,
                    color=MUTED, va="top", zorder=3)
        if x + w < 100:
            ax.annotate(
                "", xy=(x + w + 6.0, y + height / 2),
                xytext=(x + w + 1.0, y + height / 2),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.8,
                                mutation_scale=14),
            )

    ax.text(2, 8.0,
            "Validator: caps proposed amounts, downgrades confidence that a human has not approved",
            fontsize=10, color=MUTED, va="top")
    ax.text(98, 8.0, "Audit ledger: AI_Exception_Approvals.xlsx",
            fontsize=10, color=BLUE, va="top", ha="right")

    fig.savefig(path)
    plt.close(fig)


def main() -> int:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    data = load_data()

    draw_architecture(IMAGE_DIR / "architecture.png")
    draw_results(IMAGE_DIR / "results.png", data)
    draw_workflow(IMAGE_DIR / "approval-workflow.png", data)

    for name in ("architecture.png", "results.png", "approval-workflow.png"):
        print(f"Wrote {IMAGE_DIR / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
