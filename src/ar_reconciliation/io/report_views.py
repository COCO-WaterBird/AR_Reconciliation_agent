"""
The five report sheets at the front of the result workbook (decision D6).

The workbook opens on five sheets a finance reader needs:

  1. **Summary**       - run status, counts, controls by currency, AI totals;
  2. **Matched**       - every receipt whose cash was applied (by the engine's
                         rules or by an approved AI resolution), or that was
                         already reconciled in the source;
  3. **Unmatched**     - every receipt still in Needs Review, with the reason
                         and the AI's recommendation and decision;
  4. **AI Suggested**  - the AI proposals waiting for a reviewer's decision;
  5. **Evidence**      - the evidence behind each outcome: the matching rule
                         and its notes, supporting-document facts with their
                         quotes, and reviewer decisions.

Every receipt appears in exactly one of Matched and Unmatched.

These sheets are *views*.  They are rebuilt from the detail sheets that
follow them (the appendix: Reconciled Open AR, Processed Bank Receipts, the
AI review, the audit records), which remain the system of record: the
controls, the approval write-back and the regression baseline all read the
appendix.  A view therefore can never disagree with the data it summarises,
and editing a view changes nothing - decisions are recorded in the
AI Exception Review sheet.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ar_reconciliation.core.primitives import money_to_cents, normalize_key, normalize_text

__all__ = [
    "REPORT_SHEETS",
    "APPENDIX_ORDER",
    "build_report_views",
    "refresh_report_views",
]

#: The five report sheets, in the order they appear.
REPORT_SHEETS = ("Summary", "Matched", "Unmatched", "AI Suggested", "Evidence")

#: The detail and audit sheets, in the order they follow the report sheets.
APPENDIX_ORDER = (
    "AI Exception Review",
    "Reconciled Open AR",
    "Processed Bank Receipts",
    "Unmatched Bank Receipts",
    "Data Quality Checks",
    "Supporting Documents",
    "Extracted Evidence",
    "Approval Records",
    "Audit Trail",
    "AI Exception Log",
)

_SOURCE_RECONCILED = {"reconciled", "matched", "closed"}


def _text(value: object) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else normalize_text(value)


def _cents(value: object) -> int:
    try:
        return money_to_cents(value)
    except Exception:
        return 0


def _money(cents: int) -> float:
    return cents / 100


def _invoices_by_receipt(ar: pd.DataFrame) -> dict[str, list[str]]:
    """Receipt -> invoices it was applied to, from the AR ledger."""
    mapping: dict[str, list[str]] = {}
    if ar.empty or "Matched Bank Transaction ID" not in ar.columns:
        return mapping
    for _, row in ar.iterrows():
        invoice = _text(row.get("Invoice Number"))
        for txn in _text(row.get("Matched Bank Transaction ID")).split(","):
            key = normalize_key(txn)
            if key and invoice and invoice not in mapping.setdefault(key, []):
                mapping[key].append(invoice)
    return mapping


def _review_by_receipt(review: pd.DataFrame) -> dict[str, pd.Series]:
    if review.empty or "Bank Transaction ID" not in review.columns:
        return {}
    return {normalize_key(r["Bank Transaction ID"]): r for _, r in review.iterrows()}


def _matched(bank: pd.DataFrame, ar: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    invoices = _invoices_by_receipt(ar)
    rows: list[dict[str, object]] = []
    keys: set[str] = set()
    for _, row in bank.iterrows():
        applied = _cents(row.get("AR Applied Amount Total"))
        source_reconciled = _text(row.get("Status")).lower() in _SOURCE_RECONCILED
        if applied <= 0 and not source_reconciled:
            continue
        key = normalize_key(row.get("Bank Transaction ID"))
        keys.add(key)
        ai = _cents(row.get("AI-Resolved Applied Amount")) > 0 or _text(row.get("Match Method")).startswith("AI Approved")
        if source_reconciled and applied <= 0:
            settled_by = "Already reconciled in source"
        elif ai:
            settled_by = "AI resolution approved by reviewer"
        else:
            settled_by = "Deterministic rules"
        rows.append({
            "Bank Transaction ID": _text(row.get("Bank Transaction ID")),
            "Value Date": row.get("Value Date"),
            "Customer Name": _text(row.get("Customer Name")),
            "ERP Customer ID": _text(row.get("ERP Customer ID")),
            "Currency": _text(row.get("Currency")),
            "Gross Receipt Amount": _money(_cents(row.get("Gross Receipt Amount"))),
            "Applied to AR": _money(applied),
            "Unapplied Amount": _money(_cents(row.get("Unapplied Amount"))),
            "Fee / Rounding Adjustment": _money(_cents(row.get("Fee / Rounding Adjustment"))),
            "Invoices": ", ".join(invoices.get(key, [])),
            "Match Method": _text(row.get("Match Method")),
            "Settled By": settled_by,
            "Status": _text(row.get("Suggested Bank Status")),
            "Resolution ID": _text(row.get("Resolution ID")),
        })
    return pd.DataFrame(rows, columns=[
        "Bank Transaction ID", "Value Date", "Customer Name", "ERP Customer ID", "Currency",
        "Gross Receipt Amount", "Applied to AR", "Unapplied Amount", "Fee / Rounding Adjustment",
        "Invoices", "Match Method", "Settled By", "Status", "Resolution ID",
    ]), keys


def _unmatched(bank: pd.DataFrame, unmatched: pd.DataFrame, review: pd.DataFrame,
               matched_keys: set[str]) -> pd.DataFrame:
    reasons = {}
    if not unmatched.empty:
        reasons = {normalize_key(r["Bank Transaction ID"]): r for _, r in unmatched.iterrows()}
    reviews = _review_by_receipt(review)
    rows: list[dict[str, object]] = []
    for _, row in bank.iterrows():
        key = normalize_key(row.get("Bank Transaction ID"))
        if key in matched_keys:
            continue
        reason_row = reasons.get(key)
        review_row = reviews.get(key)
        rows.append({
            "Bank Transaction ID": _text(row.get("Bank Transaction ID")),
            "Value Date": row.get("Value Date"),
            "Customer Name": _text(row.get("Customer Name")),
            "Currency": _text(row.get("Currency")),
            "Gross Receipt Amount": _money(_cents(row.get("Gross Receipt Amount"))),
            "Held For Review Amount": _money(_cents(row.get("Held For Review Amount"))),
            "Memo / Remittance Detail": _text(row.get("Memo / Remittance Detail")),
            "Reason": _text(reason_row.get("Reason")) if reason_row is not None
            else _text(row.get("Processing Notes")),
            "Suggested Invoices": _text(reason_row.get("Suggested Invoices")) if reason_row is not None else "",
            "AI Recommended Action": _text(review_row.get("Recommended Action")) if review_row is not None else "",
            "AI Confidence": _text(review_row.get("AI Confidence")) if review_row is not None else "",
            "Supporting Documents": _text(review_row.get("Supporting Documents")) if review_row is not None else "",
            "Decision": _text(review_row.get("Approval")) if review_row is not None else "",
        })
    return pd.DataFrame(rows, columns=[
        "Bank Transaction ID", "Value Date", "Customer Name", "Currency", "Gross Receipt Amount",
        "Held For Review Amount", "Memo / Remittance Detail", "Reason", "Suggested Invoices",
        "AI Recommended Action", "AI Confidence", "Supporting Documents", "Decision",
    ])


def _ai_suggested(review: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Bank Transaction ID", "AI Exception Type", "Recommended Action", "AI Confidence",
        "Proposed Allocations", "Proposed Adjustments", "Proposed Applied Amount",
        "Validation Result", "Evidence Sufficiency", "AI Explanation", "Decision",
        "Record decisions in",
    ]
    if review.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for _, row in review.iterrows():
        if _text(row.get("Applied To AR")) == "Yes":
            continue
        has_proposal = bool(_text(row.get("Proposed Allocations"))) or (
            _text(row.get("Recommended Action")) in {"Auto Match", "Recommend Match"}
            and bool(_text(row.get("Proposed Invoice Numbers")))
        )
        if not has_proposal:
            continue
        allocations = _text(row.get("Proposed Allocations"))
        if not allocations and _text(row.get("Proposed Invoice Numbers")):
            allocations = f"{_text(row.get('Proposed Invoice Numbers'))}: {_cents(row.get('Proposed Applied Amount')) / 100:,.2f}"
        rows.append({
            "Bank Transaction ID": _text(row.get("Bank Transaction ID")),
            "AI Exception Type": _text(row.get("AI Exception Type")),
            "Recommended Action": _text(row.get("Recommended Action")),
            "AI Confidence": _text(row.get("AI Confidence")),
            "Proposed Allocations": allocations,
            "Proposed Adjustments": _text(row.get("Proposed Adjustments")),
            "Proposed Applied Amount": _money(_cents(row.get("Proposed Applied Amount"))),
            "Validation Result": _text(row.get("Validation Result")),
            "Evidence Sufficiency": _text(row.get("Evidence Sufficiency")),
            "AI Explanation": _text(row.get("AI Explanation")),
            "Decision": _text(row.get("Approval")) or "Awaiting decision",
            "Record decisions in": "AI Exception Review",
        })
    return pd.DataFrame(rows, columns=columns)


def _evidence(bank: pd.DataFrame, facts: pd.DataFrame, documents: pd.DataFrame,
              records: pd.DataFrame, matched_keys: set[str]) -> pd.DataFrame:
    columns = [
        "Bank Transaction ID", "Evidence Type", "Source", "Detail", "Amount",
        "Location / Reference", "Quote / Notes", "Recorded At",
    ]
    rows: list[dict[str, object]] = []
    for _, row in bank.iterrows():
        key = normalize_key(row.get("Bank Transaction ID"))
        if key not in matched_keys:
            continue
        rows.append({
            "Bank Transaction ID": _text(row.get("Bank Transaction ID")),
            "Evidence Type": "Matching rule",
            "Source": "AI resolution" if _text(row.get("Resolution ID")) else "Deterministic engine",
            "Detail": _text(row.get("Match Method")),
            "Amount": _money(_cents(row.get("AR Applied Amount Total"))),
            "Location / Reference": _text(row.get("Memo / Remittance Detail")),
            "Quote / Notes": _text(row.get("Processing Notes")),
            "Recorded At": "",
        })
    names = {}
    if not documents.empty and "Document ID" in documents.columns:
        names = dict(zip(documents["Document ID"].astype(str), documents["File Name"].astype(str)))
    if not facts.empty:
        for _, fact in facts.iterrows():
            doc = _text(fact.get("Document ID"))
            amount = fact.get("Amount")
            rows.append({
                "Bank Transaction ID": _text(fact.get("Bank Transaction ID")),
                "Evidence Type": "Document fact",
                "Source": f"{doc} ({names.get(doc, '')})" if names.get(doc) else doc,
                "Detail": f"{_text(fact.get('Field'))}: {_text(fact.get('Value'))}",
                "Amount": _money(_cents(amount)) if _text(amount) else None,
                "Location / Reference": _text(fact.get("Location")),
                "Quote / Notes": _text(fact.get("Quote")),
                "Recorded At": "",
            })
    if not records.empty:
        for _, record in records.iterrows():
            rows.append({
                "Bank Transaction ID": _text(record.get("Bank Transaction ID")),
                "Evidence Type": "Reviewer decision",
                "Source": _text(record.get("Reviewer")),
                "Detail": f"{_text(record.get('Decision'))} - {_text(record.get('Outcome'))}",
                "Amount": None,
                "Location / Reference": _text(record.get("Resolution ID")),
                "Quote / Notes": _text(record.get("Reviewer Notes")) or _text(record.get("Detail")),
                "Recorded At": _text(record.get("Decision Timestamp")),
            })
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        order = {"Matching rule": 0, "Document fact": 1, "Reviewer decision": 2}
        frame = frame.assign(_o=frame["Evidence Type"].map(order)).sort_values(
            ["Bank Transaction ID", "_o"], kind="stable"
        ).drop(columns="_o").reset_index(drop=True)
    return frame


def build_report_views(sheets: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Build Matched, Unmatched, AI Suggested and Evidence from the detail sheets."""
    empty = pd.DataFrame()
    bank = sheets.get("Processed Bank Receipts", empty)
    ar = sheets.get("Reconciled Open AR", empty)
    review = sheets.get("AI Exception Review", empty)
    matched, keys = _matched(bank, ar) if not bank.empty else (pd.DataFrame(), set())
    return {
        "Matched": matched,
        "Unmatched": _unmatched(bank, sheets.get("Unmatched Bank Receipts", empty), review, keys)
        if not bank.empty else pd.DataFrame(),
        "AI Suggested": _ai_suggested(review),
        "Evidence": _evidence(
            bank,
            sheets.get("Extracted Evidence", empty),
            sheets.get("Supporting Documents", empty),
            sheets.get("Approval Records", empty),
            keys,
        ) if not bank.empty else pd.DataFrame(),
    }


#: Grey tab colour for the appendix, so the report sheets stand out.
_APPENDIX_TAB = "FFA6A6A6"
_REPORT_TAB = "FF17365D"


def refresh_report_views(path: Path) -> None:
    """
    Rebuild the four view sheets and put the workbook in report order.

    The five report sheets come first (navy tabs), then the appendix in a
    fixed order (grey tabs), then anything else.  Summary opens first.
    """
    path = Path(path)
    excel = pd.ExcelFile(path)
    names = list(excel.sheet_names)
    if "Processed Bank Receipts" not in names:
        return  # not a result workbook (e.g. a FAILED diagnostic file)
    sheets = {name: pd.read_excel(excel, name) for name in names if name not in REPORT_SHEETS[1:]}
    excel.close()
    views = build_report_views(sheets)

    with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        for name, frame in views.items():
            frame.to_excel(writer, index=False, sheet_name=name)

    from openpyxl import load_workbook

    workbook = load_workbook(path)
    wanted = [n for n in REPORT_SHEETS if n in workbook.sheetnames]
    wanted += [n for n in APPENDIX_ORDER if n in workbook.sheetnames and n not in wanted]
    wanted += [n for n in workbook.sheetnames if n not in wanted]
    workbook._sheets = [workbook[n] for n in wanted]
    for name in wanted:
        sheet = workbook[name]
        sheet.sheet_properties.tabColor = _REPORT_TAB if name in REPORT_SHEETS else _APPENDIX_TAB
        sheet.sheet_view.tabSelected = name == "Summary"
    workbook.active = 0
    workbook.save(path)
