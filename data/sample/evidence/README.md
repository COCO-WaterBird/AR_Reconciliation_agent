# Synthetic supporting documents

Invented documents for the evidence-based exception resolution demo.
Regenerate with `python scripts/make_sample_evidence.py`.

| File | Bank receipt | Scenario |
|---|---|---|
| `granite_remittance_advice.csv` | BTRX-000178 | Multi-invoice payment with a partial payment on the second invoice |
| `horizon_deduction_email.eml` | BTRX-000155 | Customer deduction (short payment with a stated reason) |
| `pinnacle_po_payment_letter.pdf` | BTRX-000141 | Missing invoice reference (the bank memo only names the PO) |
| `silverline_installment_contract.pdf` | BTRX-000171 | Instalment payment under a contract schedule |
| `brightpath_remittance_a.txt` | BTRX-000142 | Conflicting evidence (document A) |
| `brightpath_email_b.eml` | BTRX-000142 | Conflicting evidence (document B) |
| `cloudnine_payment_email.eml` | BTRX-000143 | Prompt injection inside a customer email |
| `cascade_third_party_letter.txt` | BTRX-000191 | Third-party payment (a related company pays the invoice) |
