"""
Run the AI Exception Resolution Agent against the OpenAI API.

This is the recommended way to run the agent on a laptop: the work happens on
OpenAI's servers, so the local machine stays responsive and no model weights
are loaded into RAM.

Cost
----
The agent sends one small structured request per unmatched receipt.  Each
request carries the receipt details and a bounded candidate list, so the
per-request token count is small.  The defaults below are deliberately the
cheap models, because this task is extraction and classification, not deep
reasoning:

  * ``gpt-4o-mini``  - cheapest, and accurate enough for this task.

Run it with a limit first to see the cost before analysing everything:

    export OPENAI_API_KEY=sk-...
    python scripts/run_openai.py --limit 5      # smoke test
    python scripts/run_openai.py                # all unmatched receipts

The API key is read from the ``OPENAI_API_KEY`` environment variable and is
never written to disk.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Allow the script to be run from a checkout without installing the package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ar_reconciliation.agent import exception_agent as A  # noqa: E402

# Cheap by default: this is an extraction task, not a reasoning task.
DEFAULT_MODEL = "gpt-4o-mini"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the exception agent against the OpenAI API.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AR_AGENT_MODEL", DEFAULT_MODEL),
        help="OpenAI model to use (default: %(default)s)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Analyse only the first N unmatched receipts (0 = all). Start with a "
        "small limit to check the cost (default: %(default)s)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Requests sent in parallel (default: %(default)s). The API is remote, "
        "so this does not load the local machine.",
    )
    parser.add_argument(
        "--apply-approved",
        action="store_true",
        help="Apply the recommendations approved in the review sheet (Approval = "
        "APPROVE) back to AR, then re-run the control totals.",
    )
    args = parser.parse_args(argv)

    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set.\n"
            "\n"
            "Get a key at https://platform.openai.com/api-keys and then:\n"
            "    export OPENAI_API_KEY=sk-...\n"
            "\n"
            "To add it permanently, put that line in ~/.zshrc.\n"
            "\n"
            "For a free, offline run that uses no API key and no local model, use:\n"
            "    python scripts/run_agent.py                 # deterministic rules backend\n"
        )
        return 1

    print(f"\nModel     : {args.model}")
    print(f"Parallel  : {args.concurrency}")
    print(f"Receipts  : {args.limit if args.limit > 0 else 'all unmatched'}\n")

    started = time.time()
    result = A.main([
        "--backend", "llm",
        "--model", args.model,
        "--concurrency", str(max(1, args.concurrency)),
        *(["--limit", str(args.limit)] if args.limit > 0 else []),
        *(["--apply-approved"] if args.apply_approved else []),
    ])
    elapsed = time.time() - started
    print(f"\nTotal elapsed: {elapsed:.0f}s")
    return result


if __name__ == "__main__":
    sys.exit(main())
