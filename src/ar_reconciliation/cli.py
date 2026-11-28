"""
Console entry points.

These are thin wrappers so that the package can be installed and run with a
command, while the scripts in ``scripts/`` remain the documented way to run
the project from a checkout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ar_reconciliation.config import AR_FILE, BANK_FILE, RESULT_FILE

__all__ = ["reconcile_main", "agent_main", "evidence_main"]


def reconcile_main(argv: list[str] | None = None) -> int:
    """Run the deterministic reconciliation engine."""
    parser = argparse.ArgumentParser(
        prog="ar-reconcile",
        description="Reconcile bank cash receipts against open AR.",
    )
    parser.add_argument("--bank", type=Path, default=BANK_FILE)
    parser.add_argument("--ar", type=Path, default=AR_FILE)
    parser.add_argument("--output", type=Path, default=RESULT_FILE)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from ar_reconciliation.services import run_reconciliation

    result = run_reconciliation(
        bank_path=args.bank,
        ar_path=args.ar,
        output_path=args.output,
        verbose=not args.quiet,
    )
    if not args.quiet:
        print()
        print(
            f"Reconciled AR applied amount  : "
            f"{result.reconciled_ar_total_cents / 100:,.2f}"
        )
        print(
            f"Reconciled bank applied amount: "
            f"{result.reconciled_bank_total_cents / 100:,.2f}"
        )
        print(f"Control difference            : {result.control_difference:,.2f}")
    return 0


def agent_main(argv: list[str] | None = None) -> int:
    """Run the AI Exception Resolution Agent."""
    from ar_reconciliation.agent import exception_agent

    return exception_agent.main(argv)


def evidence_main(argv: list[str] | None = None) -> int:
    """
    Manage supporting documents for receipts in Needs Review.

        ar-evidence add FILE [FILE ...] --txn BTRX-000178 [--txn ...] [--by NAME]
        ar-evidence link DOC-ID --txn BTRX-000178
        ar-evidence unlink DOC-ID --txn BTRX-000178
        ar-evidence list
        ar-evidence extract [DOC-ID ...]
        ar-evidence suggest [--result RESULT.xlsx]
        ar-evidence load-samples        # the synthetic demo documents
        ar-evidence decide BTRX-000178 APPROVE --reviewer NAME [--notes TEXT]
    """
    from ar_reconciliation.config import EVIDENCE_DIR, SAMPLE_EVIDENCE_DIR

    parser = argparse.ArgumentParser(
        prog="ar-evidence",
        description="Store, link and read supporting documents (contracts, emails, remittances).",
    )
    parser.add_argument("--store", type=Path, default=EVIDENCE_DIR,
                        help="Document store folder (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Store documents and link them to receipts")
    add.add_argument("files", nargs="+", type=Path)
    add.add_argument("--txn", action="append", default=[], help="Bank Transaction ID (repeatable)")
    add.add_argument("--by", default="", help="Who uploaded the documents")

    link = sub.add_parser("link", help="Link a stored document to more receipts")
    link.add_argument("doc_id")
    link.add_argument("--txn", action="append", required=True)

    unlink = sub.add_parser("unlink", help="Remove a document's link to a receipt")
    unlink.add_argument("doc_id")
    unlink.add_argument("--txn", required=True)

    sub.add_parser("list", help="List stored documents")

    extract = sub.add_parser("extract", help="Extract and show the facts of documents")
    extract.add_argument("doc_ids", nargs="*")

    suggest = sub.add_parser("suggest", help="Suggest receipts for unlinked documents")
    suggest.add_argument("--result", type=Path, default=RESULT_FILE)

    samples = sub.add_parser("load-samples", help="Store the synthetic demo documents")
    samples.add_argument("--folder", type=Path, default=SAMPLE_EVIDENCE_DIR)

    decide = sub.add_parser("decide", help="Record a reviewer decision in the review sheet")
    decide.add_argument("txn")
    decide.add_argument("decision", help="APPROVE, REJECT or MORE_EVIDENCE")
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--notes", default="")
    decide.add_argument("--result", type=Path, default=RESULT_FILE)

    args = parser.parse_args(argv)

    from ar_reconciliation.evidence import DocumentRejected, DocumentStore, extract_document

    store = DocumentStore(args.store)

    if args.command == "add":
        status = 0
        for path in args.files:
            try:
                document, created = store.add(path, args.txn, uploaded_by=args.by)
            except (DocumentRejected, OSError) as exc:
                print(f"REJECTED {path.name}: {exc}")
                status = 1
                continue
            state = "stored" if created else "already stored; links updated"
            print(f"{document.doc_id}  {document.filename}  {state}  -> {', '.join(document.linked_txns) or '(no receipt)'}")
        return status
    if args.command == "link":
        document = store.link(args.doc_id, args.txn)
        print(f"{document.doc_id} -> {', '.join(document.linked_txns)}")
        return 0
    if args.command == "unlink":
        document = store.unlink(args.doc_id, args.txn)
        print(f"{document.doc_id} -> {', '.join(document.linked_txns) or '(no receipt)'}")
        return 0
    if args.command == "list":
        for document in store.documents():
            intact = "ok" if store.verify(document.doc_id) else "HASH MISMATCH"
            print(
                f"{document.doc_id}  {document.filename:<40} {document.kind:<11} "
                f"{document.uploaded_at}  {intact}  -> {', '.join(document.linked_txns) or '(none)'}"
            )
        return 0
    if args.command == "extract":
        ids = args.doc_ids or [d.doc_id for d in store.documents()]
        for doc_id in ids:
            extracted = extract_document(store, doc_id, refresh=True)
            print(f"== {doc_id} ({store.get(doc_id).filename}): {extracted.status} {extracted.error}")
            for fact in extracted.facts:
                amount = f" {fact.amount_cents / 100:,.2f}" if fact.amount_cents is not None else ""
                print(f"   {fact.field:<20} {fact.value}{amount} [{fact.location}]")
        return 0
    if args.command == "suggest":
        import pandas as pd

        from ar_reconciliation.evidence import suggest_links

        unmatched = pd.read_excel(args.result, sheet_name="Unmatched Bank Receipts")
        for item in suggest_links(store, unmatched):
            print(f"{item['Document ID']}  {item['File Name']} -> {item['Bank Transaction ID']}: {item['Reason']}")
        return 0
    if args.command == "load-samples":
        from ar_reconciliation.evidence.samples import SAMPLE_DOCUMENTS, write_sample_documents

        if not args.folder.exists():
            write_sample_documents(args.folder)
        for name, (_content, txns, scenario) in SAMPLE_DOCUMENTS.items():
            document, created = store.add(args.folder / name, txns, uploaded_by="demo")
            print(f"{document.doc_id}  {name:<40} -> {', '.join(txns)}  ({scenario})")
        return 0
    if args.command == "decide":
        from ar_reconciliation.agent.workflow import record_decision

        record_decision(args.result, args.txn, args.decision, args.reviewer, args.notes)
        print(f"Recorded {args.decision.upper()} for {args.txn} by {args.reviewer}.")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(reconcile_main())
