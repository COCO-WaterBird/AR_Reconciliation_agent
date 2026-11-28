"""Enrichment Impact - the same run with and without the structured inputs."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.ui import components as c
from app.ui import layout, services
from app.ui.theme import BORDER, NEUTRAL, TEAL, TEXT

layout.header("Enrichment Impact", "What Customer Master, Contract Schedule and Remittance Advice change: "
                                   "the same bank and AR files reconciled with and without them.", show_status=False)
if not layout.require_run():
    st.stop()

sheets = services.sheets()
comparison = sheets.get("Enrichment Comparison")
if comparison is None or comparison.empty:
    c.empty_state("This run used only the bank statement and the open AR.",
                  "Run the reconciliation with structured inputs (or the Synthetic Enriched Demo) in Data Import "
                  "to compare the baseline with the enriched run.")
    st.stop()

metrics = comparison.set_index("Metric")


def number(value) -> float:
    try:
        return float(str(value).split("%")[0].split(" ")[0].replace(",", ""))
    except ValueError:
        return 0.0


cards = st.columns(4)
for col, (label, metric, better) in zip(cards, [
    ("Automation Rate", "Automation rate", "up"),
    ("Matched Automatically", "Receipts matched automatically", "up"),
    ("Needing AI Investigation", "Receipts needing AI investigation", "down"),
    ("Review Workload", "Review workload (Pending Review)", "down"),
]):
    before, after = metrics.loc[metric, "Baseline (Bank + AR)"], metrics.loc[metric, "Enriched"]
    with col:
        c.kpi(label, str(after), f"Baseline {before}")
st.write("")

left, right = st.columns([6, 5])
with left:
    with st.container(border=True):
        st.markdown("### Baseline vs. Enriched")
        labels = ["Matched automatically", "Needing AI investigation", "Pending review"]
        keys = ["Receipts matched automatically", "Receipts needing AI investigation", "Review workload (Pending Review)"]
        fig = go.Figure([
            go.Bar(name="Baseline (Bank + AR)", x=labels, y=[number(metrics.loc[k, "Baseline (Bank + AR)"]) for k in keys],
                   marker_color=NEUTRAL, text=[metrics.loc[k, "Baseline (Bank + AR)"] for k in keys], textposition="outside"),
            go.Bar(name="Enriched", x=labels, y=[number(metrics.loc[k, "Enriched"]) for k in keys],
                   marker_color=TEAL, text=[metrics.loc[k, "Enriched"] for k in keys], textposition="outside"),
        ])
        fig.update_layout(barmode="group", height=320, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="white",
                          plot_bgcolor="white", font=dict(family="Inter, Arial", size=12, color=TEXT),
                          legend=dict(orientation="h", y=1.12))
        fig.update_yaxes(gridcolor=BORDER)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
with right:
    with st.container(border=True):
        st.markdown("### Matching Accuracy and AI Usage")
        rows = [r for r in comparison.to_dict("records") if r["Metric"].startswith("Matching accuracy")
                or "LLM" in r["Metric"] or "AI investigation" in r["Metric"]]
        c.html_table(pd.DataFrame([{"Metric": r["Metric"], "Baseline": r["Baseline (Bank + AR)"],
                                    "Enriched": r["Enriched"]} for r in rows]),
                     right_cols=["Baseline", "Enriched"])
        st.caption("Accuracy is measured against the labeled ground truth (expected_matches.csv) for the demo "
                   "scenarios. LLM calls are 0 with the deterministic backend; with AI-assisted investigation each "
                   "receipt needing investigation is one call.")

with st.container(border=True):
    st.markdown("### All Metrics")
    c.html_table(comparison.rename(columns={"Baseline (Bank + AR)": "Baseline"}),
                 right_cols=["Baseline", "Enriched"], widths={"Metric": "28%", "Baseline": "14%", "Enriched": "14%"})

sources = sheets.get("Data Sources & Enrichment")
if sources is not None and not sources.empty:
    with st.container(border=True):
        st.markdown("### Data Import Status")
        c.html_table(sources.fillna(""), badge_cols=["Status"], right_cols=["Rows", "Rows Used"])
deductions = sheets.get("Proposed Deductions")
if deductions is not None and not deductions.empty:
    with st.container(border=True):
        st.markdown("### Proposed Deductions")
        st.caption("Never applied by the engine. Authorized deductions may be approved on the Exception "
                   "Investigation page; unauthorized ones are resolved as disputes.")
        view = deductions[["Deduction ID", "Bank Transaction ID", "Invoice Number", "Currency", "Deduction Amount",
                           "Deduction Code", "Authorization", "Authorization Rule", "Decision", "Applied"]].astype(object).fillna("")
        view["Deduction Amount"] = [f"{v:,.2f}" if isinstance(v, (int, float)) else v for v in view["Deduction Amount"]]
        c.html_table(view, badge_cols=["Authorization"], right_cols=["Deduction Amount"])
