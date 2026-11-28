"""
Exception analytics and the Action & Review Queue.

Exception category
    The engine's deterministic classification of the remittance memo
    (``Memo Scenario``).  It describes what the customer wrote; it is not
    an AI opinion.

Root cause basis
    Kept apart so an AI opinion is never shown as a fact:

    * ``Confirmed by Reviewer``  - a reviewer approved the resolution and it
      was applied;
    * ``AI-Suggested``           - the agent proposed a root cause, no
      decision yet (or the proposal was rejected);
    * ``Unknown / Insufficient Evidence`` - no analysis, an "Unknown" type,
      or evidence the validator found insufficient or conflicting.

Priority
    Deterministic and configurable (``reporting_config.json``): High for an
    unresolved amount at or above the currency's high-priority threshold or a
    high-risk category; Medium at or above the medium threshold; Low
    otherwise.
"""

from __future__ import annotations

import pandas as pd

from ar_reconciliation.core.primitives import normalize_key
from ar_reconciliation.reporting.config import ReportingConfig
from ar_reconciliation.reporting.statuses import cents, text

__all__ = [
    "QUEUE_COLUMNS",
    "exception_records",
    "exception_summary",
    "root_cause_summary",
    "review_queue",
]

_PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}

QUEUE_COLUMNS = [
    "Priority", "Review ID", "Bank Receipt ID", "Customer Name", "Receipt Date", "Currency",
    "Receipt Amount", "Applied Amount", "Unresolved Amount", "Related Invoice ID",
    "Exception Category", "Match Status", "Review Status", "Root Cause Basis",
    "AI Suggested Explanation (AI-generated)", "AI Confidence Level", "AI Proposed Allocation",
    "Supporting Evidence Reference", "Recommended Action", "Suggested Responsible Team",
    "Reviewer", "Review Decision", "Review Comments", "Reviewed At", "Approval Reference",
]


def _reviews(review: pd.DataFrame) -> dict[str, pd.Series]:
    if review is None or review.empty or "Bank Transaction ID" not in review.columns:
        return {}
    return {normalize_key(r["Bank Transaction ID"]): r for _, r in review.iterrows()}


def _approvals(records: pd.DataFrame) -> dict[str, str]:
    """Receipt -> resolution ID of an approval that was actually applied."""
    if records is None or records.empty or "Outcome" not in records.columns:
        return {}
    applied = records[records["Outcome"].astype(str) == "Applied"]
    return {normalize_key(r["Bank Transaction ID"]): text(r.get("Resolution ID")) for _, r in applied.iterrows()}


def _root_cause_basis(review_row: pd.Series | None, review_status: str) -> str:
    if review_row is None:
        return "Unknown / Insufficient Evidence"
    if review_status == "Approved":
        return "Confirmed by Reviewer"
    sufficiency = text(review_row.get("Evidence Sufficiency"))
    ai_type = text(review_row.get("AI Exception Type"))
    if ai_type in {"", "Unknown"} or sufficiency in {"Insufficient", "Conflicting"}:
        return "Unknown / Insufficient Evidence"
    return "AI-Suggested"


def _team(category: str, match_status: str, config: ReportingConfig) -> str:
    if match_status == "Refund / Reversal":
        return config.team("treasury")
    if category in set(config.get("duplicate_categories", [])):
        return config.team("cash_application")
    if "Dispute" in category or "Collector" in category:
        return config.team("disputes")
    if "Missing ERP Customer" in category:
        return config.team("master_data")
    return config.team("cash_application")


def exception_records(statuses: pd.DataFrame, review: pd.DataFrame, unmatched: pd.DataFrame,
                      records: pd.DataFrame, config: ReportingConfig) -> pd.DataFrame:
    """
    One row per receipt that needs attention: not fully settled, or pending a
    reviewer decision.  This is the basis of both the analysis and the queue.
    """
    reviews = _reviews(review)
    approvals = _approvals(records)
    reasons = {}
    if unmatched is not None and not unmatched.empty:
        reasons = {normalize_key(r["Bank Transaction ID"]): r for _, r in unmatched.iterrows()}
    rows = []
    for _, s in statuses.iterrows():
        needs = s["Match Status"] in {"Unmatched", "Partially Matched", "Refund / Reversal"} or s["Review Status"] in {
            "Pending Review", "Approved - Not Yet Applied", "Rejected", "More Evidence Requested"
        }
        settled_by_ai = s["Review Status"] == "Approved"
        if not needs and not settled_by_ai:
            continue
        key = s["key"]
        review_row = reviews.get(key)
        reason_row = reasons.get(key)
        category = s["Memo Scenario"] or "Unclassified"
        unresolved = s["held"] if s["Match Status"] in {"Unmatched", "Refund / Reversal"} else s["unapplied"]
        threshold_high = config.materiality(s["Currency"], "high_priority_amount")
        threshold_medium = config.materiality(s["Currency"], "medium_priority_amount")
        if abs(unresolved) >= threshold_high or category in set(config.get("high_priority_categories", [])):
            priority = "High"
        elif abs(unresolved) >= threshold_medium:
            priority = "Medium"
        else:
            priority = "Low"
        if settled_by_ai:
            priority = "Low"

        docs = text(review_row.get("Supporting Documents")) if review_row is not None else ""
        decision = text(review_row.get("Approval")) if review_row is not None else ""
        recommended = text(review_row.get("Recommended Action")) if review_row is not None else ""
        if not recommended:
            if s["Match Status"] in {"Fully Matched", "Partially Matched"}:
                recommended = f"Confirm the engine's match ({s['Match Method'] or 'see Cash Application Results'})"
            elif reason_row is not None and text(reason_row.get("Reason")):
                recommended = text(reason_row.get("Reason"))
            else:
                recommended = "Investigate the receipt and identify the invoices"
        related = s["Memo Invoices"]
        rows.append({
            "key": key,
            "Priority": priority,
            "Review ID": f"RV-{s['Bank Transaction ID']}",
            "Bank Receipt ID": s["Bank Transaction ID"],
            "Customer Name": s["Customer Name"],
            "Receipt Date": s["Value Date"],
            "Currency": s["Currency"],
            "receipt_cents": s["gross"],
            "applied_cents": s["applied"],
            "unresolved_cents": unresolved,
            "Related Invoice ID": related or "None referenced",
            "Exception Category": category,
            "Match Status": s["Match Status"],
            "Review Status": s["Review Status"],
            "Root Cause Basis": _root_cause_basis(review_row, s["Review Status"]),
            "AI Exception Type (AI-generated)": text(review_row.get("AI Exception Type")) if review_row is not None else "",
            "AI Suggested Explanation (AI-generated)": text(review_row.get("AI Explanation")) if review_row is not None else "Not analyzed",
            "AI Confidence Level": text(review_row.get("AI Confidence")) if review_row is not None else "",
            "AI Proposed Allocation": (
                text(review_row.get("Proposed Allocations"))
                or (f"{text(review_row.get('Proposed Invoice Numbers'))}"
                    if text(review_row.get("Proposed Invoice Numbers")) else "")
            ) if review_row is not None else "",
            "Supporting Evidence Reference": docs or "No supporting document provided",
            "Recommended Action": recommended,
            "Engine Reason": text(reason_row.get("Reason")) if reason_row is not None else s["Processing Notes"],
            "Suggested Responsible Team": _team(category, s["Match Status"], config),
            "Reviewer": text(review_row.get("Reviewer")) if review_row is not None else "",
            "Review Decision": decision,
            "Review Comments": text(review_row.get("Reviewer Notes")) if review_row is not None else "",
            "Reviewed At": text(review_row.get("Decision Timestamp")) if review_row is not None else "",
            "Approval Reference": approvals.get(key, ""),
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["_p"] = frame["Priority"].map(_PRIORITY_ORDER)
    frame["_abs"] = frame["unresolved_cents"].abs()
    return frame.sort_values(["_p", "_abs", "Bank Receipt ID"], ascending=[True, False, True]).drop(
        columns=["_p", "_abs"]
    ).reset_index(drop=True)


def review_queue(exceptions: pd.DataFrame) -> pd.DataFrame:
    """Open work only: receipts already settled by an approved resolution drop out."""
    if exceptions.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)
    open_items = exceptions[exceptions["Review Status"] != "Approved"].copy()
    open_items["Receipt Amount"] = open_items["receipt_cents"] / 100
    open_items["Applied Amount"] = open_items["applied_cents"] / 100
    open_items["Unresolved Amount"] = open_items["unresolved_cents"] / 100
    return open_items[QUEUE_COLUMNS].reset_index(drop=True)


def exception_summary(exceptions: pd.DataFrame) -> pd.DataFrame:
    """Exceptions by category and currency (amounts never mixed across currencies)."""
    columns = ["Exception Category", "Currency", "Receipt Count", "Unresolved Amount",
               "% of Exceptions (count)", "Pending Review", "Decided", "Most Common AI Recommendation"]
    open_items = exceptions[exceptions["Review Status"] != "Approved"] if not exceptions.empty else exceptions
    if open_items.empty:
        return pd.DataFrame(columns=columns)
    total = len(open_items)
    rows = []
    for (category, currency), group in open_items.groupby(["Exception Category", "Currency"], sort=True):
        actions = group["Recommended Action"].value_counts()
        rows.append({
            "Exception Category": category,
            "Currency": currency,
            "Receipt Count": int(len(group)),
            "Unresolved Amount": int(group["unresolved_cents"].sum()),
            "% of Exceptions (count)": len(group) / total,
            "Pending Review": int((group["Review Status"] == "Pending Review").sum()),
            "Decided": int(group["Review Status"].isin(
                ["Rejected", "More Evidence Requested", "Approved - Not Yet Applied"]).sum()),
            "Most Common AI Recommendation": actions.index[0] if len(actions) else "",
        })
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values(["Receipt Count", "Exception Category"], ascending=[False, True]).reset_index(drop=True)


def root_cause_summary(exceptions: pd.DataFrame) -> pd.DataFrame:
    columns = ["Root Cause Basis", "Receipt Count", "Meaning"]
    meanings = {
        "Confirmed by Reviewer": "A reviewer approved the resolution and it was applied.",
        "AI-Suggested": "The AI agent's opinion; not confirmed by a reviewer.",
        "Unknown / Insufficient Evidence": "No usable AI analysis, or the evidence was insufficient or conflicting.",
    }
    counts = exceptions["Root Cause Basis"].value_counts() if not exceptions.empty else pd.Series(dtype=int)
    return pd.DataFrame(
        [{"Root Cause Basis": k, "Receipt Count": int(counts.get(k, 0)), "Meaning": v} for k, v in meanings.items()],
        columns=columns,
    )
