"""Plotly charts in the application's palette.  Data always comes from the backend model."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from app.ui.theme import AMBER, BORDER, GREEN, MUTED, NAVY, NEUTRAL, RED, TEAL, TEXT

_STATUS_COLORS = {
    "Fully Matched": GREEN, "Partially Matched": AMBER, "Unmatched": RED,
    "Refund / Reversal": NEUTRAL, "Previously Reconciled": "#CBD5E1",
}


def _layout(fig: go.Figure, height: int = 260) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="Inter, Arial, sans-serif", size=12, color=TEXT), showlegend=False,
    )
    fig.update_xaxes(gridcolor=BORDER, zeroline=False)
    fig.update_yaxes(gridcolor=BORDER, zeroline=False)
    return fig


def match_donut(counts: dict[str, int]) -> go.Figure:
    labels = [k for k, v in counts.items() if v]
    values = [counts[k] for k in labels]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.62, sort=False, direction="clockwise",
        marker=dict(colors=[_STATUS_COLORS.get(k, NAVY) for k in labels], line=dict(color="white", width=2)),
        textinfo="none", hovertemplate="%{label}: %{value} receipts (%{percent})<extra></extra>",
    ))
    total = sum(values)
    fig.add_annotation(text=f"<b>{total:,}</b><br><span style='font-size:11px;color:{MUTED}'>receipts</span>",
                       showarrow=False, font=dict(size=20, color=NAVY))
    fig = _layout(fig, 230)
    return fig


def rate_bars(frame: pd.DataFrame, label_col: str, value_col: str, threshold: float | None = None) -> go.Figure:
    values = frame[value_col].fillna(0).astype(float)
    colors = [TEAL if threshold is None or v >= threshold else AMBER for v in values]
    fig = go.Figure(go.Bar(
        x=values, y=frame[label_col], orientation="h", marker_color=colors,
        text=[f"{v:.1%}" for v in values], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:.1%}<extra></extra>",
    ))
    fig.update_xaxes(range=[0, 1.15], tickformat=".0%")
    if threshold is not None:
        fig.add_vline(x=threshold, line=dict(color=MUTED, dash="dot", width=1))
    return _layout(fig, 70 + 42 * len(frame))


def count_bars(labels: list[str], values: list[int], color: str = TEAL, height: int | None = None) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker_color=color, text=values, textposition="outside",
        cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[0, max(values or [1]) * 1.18])
    return _layout(fig, height or (60 + 26 * len(labels)))
