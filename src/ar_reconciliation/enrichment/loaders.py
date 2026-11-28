"""
Loading the optional structured inputs.

Three optional files enrich the two required workbooks.  Each may be Excel
(.xlsx, first sheet) or CSV.  Columns are validated; a row that cannot be
used (unreadable amount, missing key) is reported as a data quality issue
and left out - it is never silently changed.

Customer Master
    customer_id, customer_name, aliases (";"-separated), parent_customer_id,
    currency, email_domains (";"-separated), payment_terms

Contract and Payment Schedule
    contract_id, customer_id, invoice_number, installment_number,
    installment_count, due_date, installment_amount, currency,
    allowed_deduction_codes (";"-separated), max_deduction_pct

Remittance Advice
    remittance_id, payment_reference, bank_transaction_id, payer_name,
    customer_id, payment_date, currency, invoice_number, amount_paid,
    deduction_amount, deduction_code, deduction_reason
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ar_reconciliation.core.primitives import MoneyParseError, money_to_cents, normalize_text

__all__ = [
    "SCHEMAS",
    "EnrichmentInputs",
    "read_table",
    "load_inputs",
]

#: Required and optional columns of each structured input.
SCHEMAS: dict[str, dict[str, list[str]]] = {
    "Customer Master": {
        "required": ["customer_id", "customer_name"],
        "optional": ["aliases", "parent_customer_id", "currency", "email_domains", "payment_terms"],
    },
    "Contract Schedule": {
        "required": ["contract_id", "customer_id", "invoice_number", "installment_amount"],
        "optional": ["installment_number", "installment_count", "due_date", "currency",
                     "allowed_deduction_codes", "max_deduction_pct"],
    },
    "Remittance Advice": {
        "required": ["remittance_id", "invoice_number", "amount_paid"],
        "optional": ["payment_reference", "bank_transaction_id", "payer_name", "customer_id", "payment_date",
                     "currency", "deduction_amount", "deduction_code", "deduction_reason"],
    },
}

_MONEY = {
    "Contract Schedule": ["installment_amount"],
    "Remittance Advice": ["amount_paid", "deduction_amount"],
}


@dataclass
class EnrichmentInputs:
    """The validated structured inputs (any may be absent)."""

    customers: pd.DataFrame | None = None
    contracts: pd.DataFrame | None = None
    remittances: pd.DataFrame | None = None
    sources: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return all(f is None or f.empty for f in (self.customers, self.contracts, self.remittances))


def read_table(source: Path | str | bytes, name: str = "") -> pd.DataFrame:
    """Read a CSV or Excel table from a path or bytes; every column as text."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        if data[:4] == b"PK\x03\x04":
            return pd.read_excel(io.BytesIO(data), dtype=str).fillna("")
        return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    path = Path(source)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, dtype=str).fillna("")
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _validate(kind: str, frame: pd.DataFrame, label: str, inputs: EnrichmentInputs) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    schema = SCHEMAS[kind]
    missing = [c for c in schema["required"] if c not in frame.columns]
    status = "Valid"
    if missing:
        inputs.issues.append({"Source": kind, "File": label, "Row": "",
                              "Issue": f"Missing required columns: {', '.join(missing)}; the file was not used."})
        inputs.sources.append({"Input": kind, "File": label, "Rows": len(frame), "Rows Used": 0,
                               "Status": "Invalid"})
        return pd.DataFrame()
    for column in schema["optional"]:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame.map(lambda v: normalize_text(v) if isinstance(v, str) else ("" if pd.isna(v) else str(v)))

    keep = []
    for index, row in frame.iterrows():
        problem = ""
        for column in schema["required"]:
            if not row[column]:
                problem = f"{column} is blank"
                break
        for column in _MONEY.get(kind, []):
            if problem or not row.get(column, ""):
                continue
            try:
                money_to_cents(row[column])
            except MoneyParseError:
                problem = f"{column} '{row[column]}' is not a readable amount"
        if problem:
            inputs.issues.append({"Source": kind, "File": label, "Row": int(index) + 2,
                                  "Issue": f"{problem}; the row was not used."})
            status = "Valid with Issues"
        else:
            keep.append(index)
    used = frame.loc[keep].reset_index(drop=True)
    for column in _MONEY.get(kind, []):
        used[f"{column}_cents"] = used[column].apply(lambda v: money_to_cents(v) if v else 0)
    inputs.sources.append({"Input": kind, "File": label, "Rows": len(frame), "Rows Used": len(used),
                           "Status": status})
    return used


def load_inputs(
    customer_master: Path | str | bytes | pd.DataFrame | None = None,
    contracts: Path | str | bytes | pd.DataFrame | None = None,
    remittances: Path | str | bytes | pd.DataFrame | None = None,
    labels: dict[str, str] | None = None,
) -> EnrichmentInputs:
    """Load and validate whichever structured inputs are supplied."""
    inputs = EnrichmentInputs()
    labels = labels or {}
    for kind, source, attr in (("Customer Master", customer_master, "customers"),
                               ("Contract Schedule", contracts, "contracts"),
                               ("Remittance Advice", remittances, "remittances")):
        if source is None:
            continue
        label = labels.get(kind) or (Path(source).name if isinstance(source, (str, Path)) else kind)
        try:
            frame = source if isinstance(source, pd.DataFrame) else read_table(source)
        except Exception:
            inputs.issues.append({"Source": kind, "File": label, "Row": "",
                                  "Issue": "The file could not be read as CSV or Excel; it was not used."})
            inputs.sources.append({"Input": kind, "File": label, "Rows": 0, "Rows Used": 0, "Status": "Invalid"})
            continue
        setattr(inputs, attr, _validate(kind, frame, label, inputs))
    return inputs
