"""Reusable presentation components.  Every dynamic value is HTML-escaped."""

from __future__ import annotations

from typing import Iterable

import streamlit as st

from app.ui.fmt import esc
from app.ui.theme import STATUS_COLORS


def badge(status: str) -> str:
    """A status badge as HTML: color plus the status word."""
    word = str(status or "").strip()
    key = word.upper()
    if key.startswith("PENDING ("):
        key = "PENDING"
    if key.startswith("NOT INTEGRATED"):
        key = "NOT INTEGRATED"
    background, color = STATUS_COLORS.get(key, ("#E2E8F0", "#1F2937"))
    return f'<span class="badge" style="background:{background};color:{color}">{esc(word or "Not Available")}</span>'


def page_header(title: str, description: str, meta: dict[str, str] | None = None) -> None:
    meta_html = "<br>".join(f"{esc(k)}: <b>{esc(v)}</b>" for k, v in (meta or {}).items())
    st.markdown(
        f'<div class="page-header"><div><div class="title">{esc(title)}</div>'
        f'<div class="desc">{esc(description)}</div></div><div class="meta">{meta_html}</div></div>',
        unsafe_allow_html=True,
    )


def status_strip(items: Iterable[tuple[str, str]]) -> None:
    cells = "".join(
        f'<div class="status-cell"><div class="label">{esc(label)}</div><div class="value">{badge(value)}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="status-strip">{cells}</div>', unsafe_allow_html=True)


def kpi(label: str, value: str | list[str], note: str = "") -> None:
    if isinstance(value, list):
        body = "<br>".join(esc(v) for v in value) or "Not Available"
        css = "value small"
    else:
        body = esc(value)
        css = "value"
    st.markdown(
        f'<div class="kpi"><div class="label">{esc(label)}</div><div class="{css}">{body}</div>'
        f'<div class="note">{esc(note)}</div></div>',
        unsafe_allow_html=True,
    )


def kv(rows: Iterable[tuple[str, str]]) -> None:
    cells = "".join(f'<div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>' for k, v in rows)
    st.markdown(f'<div class="kv">{cells}</div>', unsafe_allow_html=True)


def callout(text: str, kind: str = "") -> None:
    st.markdown(f'<div class="callout {esc(kind)}">{esc(text)}</div>', unsafe_allow_html=True)


def labeled(title: str, label: str = "fact") -> None:
    """A small section title tagged as a confirmed fact or as AI-generated."""
    tag = '<span class="ai-label">AI-GENERATED</span>' if label == "ai" else '<span class="fact-label">CONFIRMED</span>'
    st.markdown(f"**{esc(title)}** {tag}", unsafe_allow_html=True)


def empty_state(message: str, hint: str = "") -> None:
    st.info(message + (f"\n\n{hint}" if hint else ""))


# --- Table column formats (one definition for every table) ------------------

def money_col(label: str, **kwargs):
    """Amounts: 1,234.00 with (negatives); the ISO currency sits in its own column."""
    return st.column_config.NumberColumn(label, format="accounting", **kwargs)


def count_col(label: str, **kwargs):
    return st.column_config.NumberColumn(label, format="%d", **kwargs)


def pct_col(label: str, **kwargs):
    return st.column_config.NumberColumn(label, format="percent", **kwargs)


def date_col(label: str, **kwargs):
    return st.column_config.DateColumn(label, format="YYYY-MM-DD", **kwargs)


def html_table(frame, badge_cols=(), right_cols=(), widths: dict | None = None) -> None:
    """
    A compact, fully wrapped table for short lists (rules, candidates).

    Values are escaped; status columns render as badges (color plus text).
    """
    import pandas as pd

    badge_cols, right_cols, widths = set(badge_cols), set(right_cols), widths or {}
    head = "".join(
        f'<th style="{"text-align:right;" if c in right_cols else ""}{"width:" + widths[c] + ";" if c in widths else ""}">{esc(c)}</th>'
        for c in frame.columns
    )
    body = []
    for _, row in frame.iterrows():
        cells = []
        for col in frame.columns:
            value = row[col]
            shown = "" if value is None or (isinstance(value, float) and pd.isna(value)) else value
            content = badge(str(shown)) if col in badge_cols else esc(shown)
            align = ' style="text-align:right;white-space:nowrap"' if col in right_cols else ""
            cells.append(f"<td{align}>{content}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(f'<table class="ui-table"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>',
                unsafe_allow_html=True)
