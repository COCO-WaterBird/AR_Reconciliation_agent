"""
Turning structured inputs into facts about each bank receipt.

For every receipt the resolver states, deterministically:

* the customer, when the bank record lacks a usable customer ID and the
  Customer Master resolves the payer name to exactly one customer in the
  receipt's currency (decision G3);
* the remittance lines that belong to it - by bank transaction ID, by payment
  reference, or (only when unique) by payer + amount + date;
* the cash allocations per invoice and any deductions, each deduction marked
  Authorized or Unauthorized against the contract terms (decision G1);
* a contract instalment that explains the receipt when no remittance does;
* conflicts: two remittances that disagree, a remittance total that does not
  equal the receipt, an invoice of another customer (third party, E2).

It decides nothing.  The engine (``services/reconciliation.py``) decides
whether a fact-backed allocation may be applied automatically.  Facts from
emails and PDFs never enter here (decision G2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd

from ar_reconciliation.core.primitives import format_money, normalize_invoice, normalize_key, normalize_text
from ar_reconciliation.enrichment.loaders import EnrichmentInputs

__all__ = ["Deduction", "ReceiptEnrichment", "build_enrichment", "DATE_WINDOW_DAYS"]

#: Payer + amount + date fallback: the remittance date must be this close.
DATE_WINDOW_DAYS = 3


@dataclass
class Deduction:
    invoice: str
    amount_cents: int
    code: str
    reason: str
    authorization: str  # Authorized / Unauthorized
    rule: str
    source: str


@dataclass
class ReceiptEnrichment:
    txn_id: str
    resolved_customer_key: str = ""
    resolved_customer_id: str = ""
    customer_note: str = ""
    allocations: list[tuple[str, int]] = field(default_factory=list)
    deductions: list[Deduction] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    source_type: str = ""  # Remittance Advice / Contract Schedule
    notes: list[str] = field(default_factory=list)

    @property
    def has_allocation(self) -> bool:
        return bool(self.allocations) and not self.conflicts


def _split(value: str) -> list[str]:
    return [v.strip() for v in str(value or "").split(";") if v.strip()]


def _alias_map(customers: pd.DataFrame | None) -> tuple[dict[str, set[str]], dict[str, dict]]:
    aliases: dict[str, set[str]] = {}
    records: dict[str, dict] = {}
    if customers is None or customers.empty:
        return aliases, records
    for _, row in customers.iterrows():
        cid = row["customer_id"]
        records[normalize_key(cid)] = dict(row)
        for name in [row["customer_name"], *_split(row.get("aliases", ""))]:
            key = normalize_key(name)
            if key:
                aliases.setdefault(key, set()).add(cid)
    return aliases, records


def _date(value) -> pd.Timestamp | None:
    stamp = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(stamp) else stamp.normalize()


def _pct(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).replace("%", "").strip()) if str(value).strip() else None
    except InvalidOperation:
        return None


def build_enrichment(bank_df: pd.DataFrame, ar_df: pd.DataFrame, inputs: EnrichmentInputs) -> dict[str, ReceiptEnrichment]:
    """
    Facts per receipt, keyed by normalized bank transaction ID.

    Expects the engine's prepared frames (``__customer_key``,
    ``__currency_key``, ``__gross_amount_cents`` on the bank side and
    ``__customer_key``, ``__ar_original_open_cents`` on the AR side).
    """
    result: dict[str, ReceiptEnrichment] = {}
    if inputs is None or inputs.empty:
        return result

    aliases, records = _alias_map(inputs.customers)
    known_ids = set(records) | set(ar_df["__customer_key"].astype(str))
    invoice_owner = {normalize_invoice(r["Invoice Number"]): r for _, r in ar_df.iterrows()}

    remit = inputs.remittances if inputs.remittances is not None else pd.DataFrame()
    contracts = inputs.contracts if inputs.contracts is not None else pd.DataFrame()

    # Remittance lines: dedupe identical lines (same invoice, amounts, code) that
    # arrive from several files or twice in one file.
    if not remit.empty:
        remit = remit.copy()
        remit["_dup_key"] = (remit["payment_reference"].map(normalize_key) + "|" + remit["bank_transaction_id"].map(normalize_key)
                             + "|" + remit["invoice_number"].map(normalize_invoice) + "|" + remit["amount_paid_cents"].astype(str)
                             + "|" + remit["deduction_amount_cents"].astype(str) + "|" + remit["deduction_code"].map(normalize_key))
        duplicated = remit[remit.duplicated("_dup_key", keep="first")]
        remit = remit.drop_duplicates("_dup_key", keep="first")
    else:
        duplicated = pd.DataFrame()

    for _, bank in bank_df.iterrows():
        txn = normalize_text(bank["Bank Transaction ID"])
        key = normalize_key(txn)
        enr = ReceiptEnrichment(txn_id=txn)
        currency = normalize_key(bank["__currency_key"])
        gross = int(bank["__gross_amount_cents"])
        customer_key = str(bank["__customer_key"])
        bank_id = normalize_key(bank.get("ERP Customer ID", ""))

        # --- Customer resolution (G3) --------------------------------------
        if aliases and (not bank_id or bank_id not in known_ids):
            for name in (bank.get("Customer Name", ""), bank.get("Remitter Name", "")):
                ids = aliases.get(normalize_key(name), set())
                if len(ids) == 1:
                    cid = next(iter(ids))
                    record = records.get(normalize_key(cid), {})
                    if record.get("currency") and normalize_key(record["currency"]) != currency:
                        enr.notes.append(f"Customer Master maps '{name}' to {cid}, but in another currency; not used.")
                        break
                    enr.resolved_customer_id = cid
                    enr.resolved_customer_key = normalize_key(cid)
                    enr.customer_note = f"Customer resolved from Customer Master alias '{normalize_text(name)}' -> {cid}"
                    customer_key = enr.resolved_customer_key
                    break
                if len(ids) > 1:
                    enr.notes.append(f"Customer Master alias '{name}' matches several customers; not used.")
                    break

        # --- Remittance lines for this receipt --------------------------------
        lines = pd.DataFrame()
        if not remit.empty:
            by_txn = remit[remit["bank_transaction_id"].map(normalize_key) == key]
            ref = normalize_key(bank.get("Bank Reference", ""))
            by_ref = remit[(remit["payment_reference"].map(normalize_key) == ref) & (ref != "")] if ref else remit.iloc[0:0]
            lines = pd.concat([by_txn, by_ref]).drop_duplicates("_dup_key")
            if lines.empty and customer_key:
                value_date = _date(bank.get("Value Date"))
                candidates = []
                for rid, group in remit[remit["bank_transaction_id"] == ""].groupby("remittance_id"):
                    payer_ids = {normalize_key(c) for c in group["customer_id"] if c}
                    for name in group["payer_name"]:
                        payer_ids |= {normalize_key(c) for c in aliases.get(normalize_key(name), set())}
                    if customer_key not in payer_ids:
                        continue
                    if int(group["amount_paid_cents"].sum()) != gross:
                        continue
                    dates = [d for d in (_date(v) for v in group["payment_date"]) if d is not None]
                    if value_date is not None and dates and min(abs((d - value_date).days) for d in dates) > DATE_WINDOW_DAYS:
                        continue
                    candidates.append(group)
                if len(candidates) == 1:
                    lines = candidates[0]
                    enr.notes.append("Remittance linked by payer, amount and date (unique).")
                elif len(candidates) > 1:
                    enr.conflicts.append(f"{len(candidates)} remittances fit this receipt by payer, amount and date; "
                                         "none is applied automatically.")

        if not lines.empty:
            enr.source_type = "Remittance Advice"
            enr.sources = sorted(set(lines["remittance_id"]))
            dup_here = duplicated[duplicated["_dup_key"].isin(lines["_dup_key"])] if not duplicated.empty else duplicated
            if len(dup_here):
                enr.notes.append(f"{len(dup_here)} duplicate remittance line(s) ignored.")
            # Conflicting instructions: the same invoice with different amounts,
            # or several remittance documents with different invoice sets.
            per_doc = {rid: tuple(sorted((normalize_invoice(r["invoice_number"]), int(r["amount_paid_cents"]))
                                         for _, r in g.iterrows()))
                       for rid, g in lines.groupby("remittance_id")}
            if len(set(per_doc.values())) > 1:
                described = "; ".join(f"{rid}: " + ", ".join(f"{i} {format_money(a)}" for i, a in v)
                                      for rid, v in per_doc.items())
                enr.conflicts.append(f"Conflicting remittance instructions ({described}).")
            else:
                chosen = lines[lines["remittance_id"] == next(iter(per_doc))]
                totals: dict[str, int] = {}
                for _, r in chosen.iterrows():
                    inv = normalize_invoice(r["invoice_number"]).upper()
                    totals[inv] = totals.get(inv, 0) + int(r["amount_paid_cents"])
                enr.allocations = list(totals.items())
                total = sum(totals.values())
                if total != gross:
                    enr.conflicts.append(f"Remittance total {format_money(total)} does not equal the receipt "
                                         f"{format_money(gross)}.")
                for _, r in chosen.iterrows():
                    if int(r["deduction_amount_cents"]) > 0:
                        enr.deductions.append(_authorize(r, contracts, invoice_owner))
        elif not contracts.empty and customer_key and gross > 0:
            # --- Contract instalment that explains the receipt ------------------
            mine = contracts[contracts["customer_id"].map(normalize_key) == customer_key]
            fits = []
            for _, c in mine.iterrows():
                inv = normalize_invoice(c["invoice_number"])
                owner = invoice_owner.get(inv)
                if owner is None or int(c["installment_amount_cents"]) != gross:
                    continue
                if c.get("currency") and normalize_key(c["currency"]) != currency:
                    continue
                fits.append((inv.upper(), c))
            invoices = {inv for inv, _ in fits}
            if len(invoices) == 1:
                inv, c = sorted(fits, key=lambda x: str(x[1].get("installment_number", "")))[0]
                enr.allocations = [(inv, gross)]
                enr.source_type = "Contract Schedule"
                enr.sources = [f"{c['contract_id']} instalment {c.get('installment_number') or '?'}"
                               f"{'/' + c['installment_count'] if c.get('installment_count') else ''}"]
            elif len(invoices) > 1:
                enr.notes.append("Several contract instalments match this amount; none is applied automatically.")

        # --- Third-party invoices (E2) -------------------------------------------
        for inv, _ in enr.allocations:
            owner = invoice_owner.get(normalize_invoice(inv))
            if owner is not None and customer_key and str(owner["__customer_key"]) != customer_key:
                enr.conflicts.append(f"{inv} belongs to {owner['ERP Customer ID']}, not the paying customer: "
                                     "a third-party payment requires review.")
            if owner is not None and normalize_key(owner.get("__currency_key", currency)) != currency:
                enr.conflicts.append(f"{inv} is not in {currency.upper()}.")

        if enr.resolved_customer_key or enr.allocations or enr.conflicts or enr.notes:
            result[key] = enr
    return result


def _authorize(row: pd.Series, contracts: pd.DataFrame, invoice_owner: dict) -> Deduction:
    invoice = normalize_invoice(row["invoice_number"]).upper()
    amount = int(row["deduction_amount_cents"])
    code = normalize_text(row.get("deduction_code", "")).upper()
    reason = normalize_text(row.get("deduction_reason", ""))
    source = normalize_text(row["remittance_id"])
    terms = contracts[contracts["invoice_number"].map(normalize_invoice).str.upper() == invoice] if not contracts.empty else contracts
    if terms is None or terms.empty:
        return Deduction(invoice, amount, code, reason, "Unauthorized",
                         "No contract terms authorize a deduction on this invoice.", source)
    term = terms.iloc[0]
    allowed = {c.upper() for c in _split(term.get("allowed_deduction_codes", ""))}
    if code not in allowed:
        return Deduction(invoice, amount, code, reason, "Unauthorized",
                         f"Code '{code or 'none'}' is not allowed by contract {term['contract_id']} "
                         f"(allowed: {', '.join(sorted(allowed)) or 'none'}).", source)
    limit_pct = _pct(term.get("max_deduction_pct", ""))
    owner = invoice_owner.get(normalize_invoice(invoice))
    if limit_pct is not None and owner is not None:
        limit = int((Decimal(int(owner["__ar_original_open_cents"])) * limit_pct / 100).to_integral_value())
        if amount > limit:
            return Deduction(invoice, amount, code, reason, "Unauthorized",
                             f"{format_money(amount)} exceeds the contract limit of {limit_pct}% "
                             f"({format_money(limit)}) in {term['contract_id']}.", source)
    return Deduction(invoice, amount, code, reason, "Authorized",
                     f"Allowed by contract {term['contract_id']} (code {code}"
                     f"{f', limit {limit_pct}%' if limit_pct is not None else ''}).", source)
