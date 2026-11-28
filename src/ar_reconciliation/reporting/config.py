"""
Reporting configuration and FX rates.

Thresholds live in ``reporting_config.json`` next to this module, so finance
can change them without touching code.  A different file can be supplied
with the ``AR_REPORTING_CONFIG`` environment variable.

FX rates are optional.  They are read only from an explicit CSV file
(``fx_rates_file`` in the configuration, or ``AR_FX_RATES_FILE``) with the
columns ``currency, rate_to_reporting, rate_date, source``.  Without that
file no amount is converted and no consolidated total is produced.  A rate
is never assumed, and never defaulted to 1.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = ["ReportingConfig", "FxRate", "load_config", "load_fx_rates"]

_DEFAULT_FILE = Path(__file__).with_name("reporting_config.json")


@dataclass(frozen=True)
class FxRate:
    currency: str
    rate_to_reporting: Decimal
    rate_date: str
    source: str


@dataclass
class ReportingConfig:
    raw: dict[str, Any]
    as_of_date: date | None = None
    reporting_currency: str | None = None
    fx_rates: dict[str, FxRate] = field(default_factory=dict)
    fx_status: str = "No FX rates supplied"

    def materiality(self, currency: str, key: str) -> int:
        """A threshold in integer cents for one currency."""
        table = self.raw.get("materiality", {})
        values = table.get(str(currency).upper()) or table.get("default", {})
        return int(Decimal(str(values.get(key, 0))) * 100)

    @property
    def aging_buckets(self) -> list[dict[str, Any]]:
        return list(self.raw["aging_buckets"])

    def team(self, key: str) -> str:
        return self.raw.get("teams", {}).get(key, "")

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


def load_fx_rates(path: Path | None) -> tuple[dict[str, FxRate], str]:
    """Read the FX-rate file, or report why there are no rates."""
    if path is None:
        return {}, "No FX rates supplied"
    path = Path(path)
    if not path.exists():
        return {}, f"FX rate file {path.name} not found"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"currency", "rate_to_reporting", "rate_date", "source"}
    if not required <= set(frame.columns):
        return {}, f"FX rate file {path.name} is missing columns {sorted(required - set(frame.columns))}"
    rates: dict[str, FxRate] = {}
    for _, row in frame.iterrows():
        try:
            rate = Decimal(str(row["rate_to_reporting"]).strip())
        except InvalidOperation:
            continue
        if rate <= 0 or not str(row["rate_date"]).strip() or not str(row["source"]).strip():
            continue
        currency = str(row["currency"]).strip().upper()
        rates[currency] = FxRate(currency, rate, str(row["rate_date"]).strip(), str(row["source"]).strip())
    return rates, f"FX rates loaded from {path.name}" if rates else f"FX rate file {path.name} has no usable rates"


def load_config(path: Path | None = None, as_of: date | str | None = None) -> ReportingConfig:
    path = Path(path or os.environ.get("AR_REPORTING_CONFIG") or _DEFAULT_FILE)
    raw = json.loads(path.read_text(encoding="utf-8"))
    config = ReportingConfig(raw=raw)
    chosen = as_of or raw.get("as_of_date")
    if chosen:
        config.as_of_date = pd.Timestamp(chosen).date()
    config.reporting_currency = (raw.get("reporting_currency") or None)
    fx_file = os.environ.get("AR_FX_RATES_FILE") or raw.get("fx_rates_file")
    if config.reporting_currency or fx_file:
        config.fx_rates, config.fx_status = load_fx_rates(Path(fx_file) if fx_file else None)
    return config
