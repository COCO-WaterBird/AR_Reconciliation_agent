"""
Run the AI Exception Resolution Agent against a local Ollama model.

.. warning::

   This path is **resource heavy** and is kept only as an offline option.
   A 7B model keeps 4-5 GB resident in RAM and will use every CPU core unless
   it is told otherwise; on a 16 GB laptop that is enough to make the desktop
   unusable.

   For normal use prefer the paid API, which runs on OpenAI's servers and
   leaves this machine alone:

       export OPENAI_API_KEY=sk-...
       python run_openai.py

   Or the free deterministic backend, which needs neither an API key nor a
   local model:

       python ai_exception_agent.py

When you do want a local model - for example because the data must not leave
the machine - this wrapper caps the resources it uses:

  * ``--concurrency 1`` so only one receipt is in flight at a time;
  * ``--num-ctx 2048`` so the KV cache stays small;
  * ``--num-thread 4`` so the model leaves CPU for the rest of the machine;
  * ``--keep-alive 0`` so the model is unloaded from RAM as soon as the run
    finishes and the memory is given back.

Usage
-----
    python run_local_model.py                                    # light profile
    python run_local_model.py --model qwen2.5:3b                 # smallest model
    python run_local_model.py --model qwen2.5:7b --limit 10      # smoke test
    python run_local_model.py --list                             # show installed models

    # Faster, but heavier on the machine:
    python run_local_model.py --concurrency 2 --num-thread 8 --keep-alive 5m
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import ai_exception_agent as A

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5:3b"

# Light defaults.  A local model on a laptop otherwise keeps 4-5 GB resident
# and saturates every core, which makes the machine unusable for anything else.
DEFAULT_CONCURRENCY = 1
DEFAULT_NUM_CTX = 2048
DEFAULT_NUM_THREAD = 4
DEFAULT_KEEP_ALIVE = "0"


def list_local_models(base_url: str) -> list[dict]:
    """List the models installed in a local Ollama server."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        with urllib.request.urlopen(f"{root}/api/tags", timeout=10) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(f"Could not reach the Ollama server at {root}: {exc}")
        print("Start it with:  ollama serve")
        return []
    return payload.get("models", [])


def check_server(base_url: str) -> bool:
    """Return True when the local server answers."""
    models = list_local_models(base_url)
    if not models:
        return False
    print(f"Local server is up with {len(models)} model(s):")
    for model in models:
        size_gb = model.get("size", 0) / 1e9
        print(f"  {model['name']:<28} {size_gb:5.1f} GB")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the exception agent against a local Ollama model.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name (default: %(default)s)")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_MODEL and DEFAULT_BASE_URL,
        help="OpenAI-compatible base URL (default: %(default)s)",
    )
    parser.add_argument("--api-key", default="ollama", help="Placeholder key (default: %(default)s)")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Receipts analysed in parallel (default: %(default)s). Keep this at 1 on a "
        "laptop; more workers means more CPU contention, not more throughput.",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=DEFAULT_NUM_CTX,
        help="Context window in tokens. A smaller window keeps the KV cache and the "
        "memory footprint down (default: %(default)s)",
    )
    parser.add_argument(
        "--num-thread",
        type=int,
        default=DEFAULT_NUM_THREAD,
        help="CPU threads the model may use. Capping it leaves the machine usable "
        "while the model runs (default: %(default)s)",
    )
    parser.add_argument(
        "--keep-alive",
        default=DEFAULT_KEEP_ALIVE,
        help="How long the model stays in RAM after the run, e.g. '5m'. '0' unloads it "
        "immediately and gives the memory back (default: %(default)s)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Analyse only the first N receipts (0 = all). Useful for a smoke test.",
    )
    parser.add_argument("--list", action="store_true", help="List the installed models and exit.")
    args = parser.parse_args(argv)

    if args.list:
        return 0 if check_server(args.base_url) else 1

    if not check_server(args.base_url):
        print("\nStart Ollama first:  ollama serve")
        return 1

    print(f"\nModel     : {args.model}")
    print(f"Base URL  : {args.base_url}")
    print(f"Parallel  : {args.concurrency}")
    print(f"Context   : {args.num_ctx} tokens")
    print(f"Threads   : {args.num_thread}")
    print(f"Keep alive: {args.keep_alive}")
    print(
        "\nNote: a local model loads its weights into this machine's RAM and uses\n"
        "      its CPU.  The limits above keep that in check.  For a run that\n"
        "      leaves the machine alone, use the paid API instead:\n"
        "          python run_openai.py\n"
    )

    started = time.time()

    if args.limit > 0:
        # Smoke test: analyse only the first N receipts, on a copy of the
        # workbook, so the real result is not touched.
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / A.RESULT_FILE.name
            shutil.copy(A.RESULT_FILE, scratch)
            result = run_agent(scratch, args, limit=args.limit)
    else:
        result = run_agent(A.RESULT_FILE, args, limit=0)

    elapsed = time.time() - started
    print(f"\nTotal elapsed: {elapsed:.0f}s")
    return result


def run_agent(result_path: Path, args: argparse.Namespace, limit: int) -> int:
    """Call the agent's CLI with the local model settings."""
    argv = [
        "--result", str(result_path),
        "--backend", "llm",
        "--model", args.model,
        "--base-url", args.base_url,
        "--api-key", args.api_key,
        "--concurrency", str(args.concurrency),
    ]
    if args.num_ctx:
        argv.extend(["--num-ctx", str(args.num_ctx)])
    if args.num_thread:
        argv.extend(["--num-thread", str(args.num_thread)])
    if args.keep_alive is not None:
        argv.extend(["--keep-alive", str(args.keep_alive)])
    if limit > 0:
        argv.extend(["--limit", str(limit)])
    return A.main(argv)


if __name__ == "__main__":
    sys.exit(main())
