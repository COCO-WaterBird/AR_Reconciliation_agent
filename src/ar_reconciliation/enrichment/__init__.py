"""
Optional structured inputs that enrich the bank-to-AR reconciliation.

Customer Master, Contract and Payment Schedule and Remittance Advice are
loaded and validated (:mod:`loaders`), then turned into facts per bank
receipt (:mod:`resolve`) before the engine runs.  Without them the engine
behaves exactly as with the two required workbooks.
"""

from ar_reconciliation.enrichment.loaders import SCHEMAS, EnrichmentInputs, load_inputs, read_table
from ar_reconciliation.enrichment.resolve import Deduction, ReceiptEnrichment, build_enrichment

__all__ = ["SCHEMAS", "EnrichmentInputs", "load_inputs", "read_table", "Deduction", "ReceiptEnrichment",
           "build_enrichment"]
