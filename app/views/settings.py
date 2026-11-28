"""Settings and Diagnostics - reviewer, AI provider status, policy configuration (read-only)."""

from __future__ import annotations

import json
import platform

import streamlit as st

from ar_reconciliation.reporting.config import load_config

from app.ui import components as c
from app.ui import layout, services, state

layout.header("Settings", "Reviewer identity, investigation mode, reporting configuration and diagnostics.",
              show_status=False)

with st.container(border=True):
    st.markdown("### Reviewer")
    st.text_input("Reviewer name", key="reviewer", placeholder="First and last name",
                  help="Recorded with each review decision. Self-declared: there is no enterprise sign-in.")
    st.caption("Simulated review workflow: the name is not authenticated and grants no special authority.")

with st.container(border=True):
    st.markdown("### AI-Assisted Investigation")
    ok, status = services.llm_status()
    c.kv([("AI Provider", "OpenAI-compatible endpoint"), ("API Key", "Configured (hidden)" if ok else "Not Configured"),
          ("Model", __import__("os").environ.get("AR_AGENT_MODEL", "gpt-4o") if ok else "Not Applicable")])
    st.toggle("Use AI-Assisted Investigation (LLM)", key="use_llm", disabled=not ok,
              help="When off, or when no key is configured, investigations use the deterministic analysis. "
                   "Only extracted facts and masked short quotes are sent to the model (decision E3).")
    if not ok:
        st.caption("Set OPENAI_API_KEY in the environment or the .env file to enable it. The key is never displayed.")

with st.container(border=True):
    st.markdown("### Reporting and Financial Policy Configuration")
    config = load_config()
    c.kv([("Reporting Currency", config.reporting_currency or "None - amounts shown in original currency"),
          ("FX Rates", config.fx_status),
          ("As-of Date", str(config.as_of_date) if config.as_of_date else "Latest bank posting date (default)")])
    st.caption("Read-only. Thresholds and policies are changed in reporting_config.json and the policy module by "
               "an administrator, not from this page; the UI cannot relax financial controls.")
    with st.expander("View reporting configuration"):
        st.code(json.dumps({k: v for k, v in config.raw.items() if not k.startswith("_")}, indent=2), language="json")

with st.expander("Diagnostics"):
    c.kv([("Session workspace", str(state.workspace())), ("Result workbook", str(state.result_path())),
          ("Document store", str(state.evidence_dir())), ("Python", platform.python_version()),
          ("Streamlit", st.__version__)])
    st.divider()
    st.markdown("**Reset Workspace**")
    st.caption("Deletes this session's results, uploaded documents and review decisions. This cannot be undone.")
    confirm = st.checkbox("I understand that all results and decisions in this session will be discarded.")
    if st.button("Reset Workspace", disabled=not confirm):
        state.reset_workspace()
        st.rerun()
