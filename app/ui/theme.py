"""
Visual design system: palette and the small amount of CSS the theme needs.

Colors carry meaning consistently - red only for failures, blocked actions
and high risk; green only for passed or completed - and every status is also
written out as text.
"""

from __future__ import annotations

import streamlit as st

NAVY = "#17365D"
TEAL = "#287E86"
GREEN = "#16805D"
AMBER = "#D49A32"
RED = "#C03939"
BACKGROUND = "#F5F7FA"
CARD = "#FFFFFF"
BORDER = "#E2E8F0"
TEXT = "#1F2937"
MUTED = "#64748B"
NEUTRAL = "#94A3B8"

#: Status word -> (background, text) for badges.
STATUS_COLORS = {
    "PASS": (GREEN, "#FFFFFF"), "PASSED": (GREEN, "#FFFFFF"), "COMPLETED": (GREEN, "#FFFFFF"),
    "FULLY MATCHED": (GREEN, "#FFFFFF"), "APPROVED": (GREEN, "#FFFFFF"), "VERIFIED": (GREEN, "#FFFFFF"),
    "ELIGIBLE FOR APPROVAL": (GREEN, "#FFFFFF"), "PARSED": (GREEN, "#FFFFFF"), "LOW": ("#E2E8F0", TEXT),
    "WARNING": (AMBER, "#FFFFFF"), "PASSED WITH WARNINGS": (AMBER, "#FFFFFF"), "PENDING": (AMBER, "#FFFFFF"),
    "PENDING REVIEW": (AMBER, "#FFFFFF"), "PARTIALLY MATCHED": (AMBER, "#FFFFFF"), "MEDIUM": (AMBER, "#FFFFFF"),
    "MORE EVIDENCE REQUESTED": (AMBER, "#FFFFFF"), "APPROVED - NOT YET APPLIED": (AMBER, "#FFFFFF"),
    "NOT ANALYZED": ("#E2E8F0", TEXT), "IN PROGRESS": (AMBER, "#FFFFFF"),
    "FAIL": (RED, "#FFFFFF"), "FAILED": (RED, "#FFFFFF"), "HIGH": (RED, "#FFFFFF"), "UNMATCHED": (RED, "#FFFFFF"),
    "REJECTED": (RED, "#FFFFFF"), "BLOCKED BY FINANCIAL POLICY": (RED, "#FFFFFF"), "HOLD / ESCALATE": (RED, "#FFFFFF"),
    "UNREADABLE": (RED, "#FFFFFF"), "UNAUTHORIZED": (RED, "#FFFFFF"), "INVALID": (RED, "#FFFFFF"),
    "AUTHORIZED": (GREEN, "#FFFFFF"), "VALID": (GREEN, "#FFFFFF"), "VALID WITH ISSUES": (AMBER, "#FFFFFF"),
    "ALLOCATIONS STATED": (GREEN, "#FFFFFF"), "ROUTED TO REVIEW": (AMBER, "#FFFFFF"),
    "NOT EVALUATED": ("#E2E8F0", TEXT), "NOT APPLICABLE": ("#E2E8F0", TEXT), "N/A": ("#E2E8F0", TEXT),
    "NOT INTEGRATED": ("#E2E8F0", TEXT), "NOT REQUIRED": ("#E2E8F0", TEXT), "NO DATA": ("#E2E8F0", TEXT),
    "REFUND / REVERSAL": ("#E2E8F0", TEXT), "PREVIOUSLY RECONCILED": ("#E2E8F0", TEXT),
    "NOT STARTED": ("#E2E8F0", TEXT),
}

CSS = f"""
<style>
:root {{ --navy:{NAVY}; --teal:{TEAL}; --border:{BORDER}; --muted:{MUTED}; }}
html, body, [class*="css"] {{ font-family: Inter, -apple-system, "Segoe UI", Arial, sans-serif; }}
.block-container {{ padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1480px; }}
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
div[data-testid="stToolbar"] {{ display: none; }}
h1, h2, h3 {{ color: {TEXT}; letter-spacing: -0.01em; }}
h3 {{ font-size: 1.05rem !important; font-weight: 650 !important; margin: 0.2rem 0 0.4rem 0 !important; }}

/* Sidebar */
section[data-testid="stSidebar"] {{ width: 256px !important; min-width: 256px !important; }}
div[data-testid="stSidebarHeader"] img {{ height: 40px !important; max-width: 220px; }}
section[data-testid="stSidebar"] .sidebar-brand {{ display:flex; align-items:center; gap:10px; margin: 0 0 0.6rem 0; }}
section[data-testid="stSidebar"] .sidebar-brand .logo {{ width:30px; height:30px; border-radius:8px;
  background:{TEAL}; color:#fff; font-weight:800; display:flex; align-items:center; justify-content:center; font-size:14px; }}
section[data-testid="stSidebar"] .sidebar-brand .name {{ color:#fff; font-weight:750; font-size:15px; letter-spacing:0.04em; }}
section[data-testid="stSidebar"] .sidebar-brand .tag {{ color:#9FB3C8; font-size:11px; }}
section[data-testid="stSidebar"] .side-meta {{ font-size:12px; color:#C9D6E3; line-height:1.55;
  border-top:1px solid rgba(255,255,255,0.12); padding-top:0.6rem; margin-top:0.4rem; }}
section[data-testid="stSidebar"] .side-meta b {{ color:#fff; font-weight:600; }}

/* Page header */
.page-header {{ display:flex; justify-content:space-between; align-items:flex-end; gap:16px;
  border-bottom:1px solid {BORDER}; padding-bottom:0.65rem; margin-bottom:0.9rem; }}
.page-header .title {{ font-size:1.55rem; font-weight:720; color:{TEXT}; line-height:1.2; }}
.page-header .desc {{ color:{MUTED}; font-size:0.9rem; margin-top:2px; }}
.page-header .meta {{ text-align:right; color:{MUTED}; font-size:0.78rem; line-height:1.5; white-space:nowrap; }}
.page-header .meta b {{ color:{TEXT}; font-weight:600; }}

/* Status strip */
.status-strip {{ display:grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap:6px; margin: 0 0 1rem 0; }}
@media (max-width: 1100px) {{ .status-strip {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }} }}
.status-cell {{ background:{CARD}; border:1px solid {BORDER}; border-radius:8px; padding:7px 10px; }}
.status-cell .label {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:0.64rem; color:{MUTED}; text-transform:uppercase; letter-spacing:0.05em; font-weight:600; }}
.status-cell .value {{ margin-top:3px; }}

/* Badges */
.badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.72rem; font-weight:650;
  letter-spacing:0.02em; white-space:nowrap; }}

/* KPI cards */
.kpi {{ background:{CARD}; border:1px solid {BORDER}; border-radius:10px; padding:12px 14px; min-height:112px;
  box-shadow: 0 1px 2px rgba(16,24,40,0.04); }}
.kpi .label {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:0.68rem; color:{MUTED}; text-transform:uppercase; letter-spacing:0.05em; font-weight:650; }}
.kpi .value {{ font-size:1.65rem; font-weight:720; color:{NAVY}; margin-top:4px; line-height:1.15; font-variant-numeric: tabular-nums; }}
.kpi .value.small {{ font-size:0.98rem; line-height:1.45; }}
.kpi .note {{ font-size:0.75rem; color:{MUTED}; margin-top:4px; }}

/* Panels and key-value lists */
.kv {{ display:grid; grid-template-columns: 42% 58%; row-gap:5px; font-size:0.86rem; }}
.kv .k {{ color:{MUTED}; }}
.kv .v {{ color:{TEXT}; font-weight:550; text-align:right; font-variant-numeric: tabular-nums; overflow-wrap:anywhere; }}
.callout {{ border-left:4px solid {TEAL}; background:#EEF6F7; padding:9px 12px; border-radius:6px; font-size:0.86rem; color:{TEXT}; }}
.callout.warn {{ border-color:{AMBER}; background:#FBF4E6; }}
.callout.block {{ border-color:{RED}; background:#FBEAEA; }}
.callout.ok {{ border-color:{GREEN}; background:#E8F4EF; }}
.muted {{ color:{MUTED}; font-size:0.8rem; }}
.ai-label {{ display:inline-block; font-size:0.66rem; font-weight:700; color:#5B21B6; background:#EDE9FE;
  border-radius:4px; padding:1px 6px; margin-left:6px; letter-spacing:0.03em; }}
.fact-label {{ display:inline-block; font-size:0.66rem; font-weight:700; color:{NAVY}; background:#E0E9F4;
  border-radius:4px; padding:1px 6px; margin-left:6px; letter-spacing:0.03em; }}

/* Compact HTML tables */
table.ui-table {{ width:100%; border-collapse:separate; border-spacing:0; font-size:0.84rem; background:{CARD};
  border:1px solid {BORDER}; border-radius:8px; overflow:hidden; }}
table.ui-table th {{ background:#F8FAFC; color:{MUTED}; font-weight:650; text-align:left; padding:7px 10px;
  border-bottom:1px solid {BORDER}; font-size:0.74rem; text-transform:uppercase; letter-spacing:0.03em; }}
table.ui-table td {{ padding:7px 10px; border-bottom:1px solid #EEF2F6; vertical-align:top; color:{TEXT};
  font-variant-numeric: tabular-nums; }}
table.ui-table tr:last-child td {{ border-bottom:none; }}

/* Buttons */
div.stButton > button, div.stDownloadButton > button {{ border-radius:7px; font-weight:600; }}
div.stButton > button[kind="primary"] {{ background:{TEAL}; border-color:{TEAL}; }}
div.stButton > button[kind="primary"]:hover {{ background:#1F6970; border-color:#1F6970; }}

/* Tables */
div[data-testid="stDataFrame"] {{ border:1px solid {BORDER}; border-radius:8px; }}
div[data-testid="stVerticalBlockBorderWrapper"] {{ background:{CARD}; }}
div[data-testid="stTabs"] button p {{ font-weight:600; }}
</style>
"""


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
