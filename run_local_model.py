"""
Run the AI Exception Resolution Agent against a local Ollama model.

This is a convenience wrapper that runs the agent with the LLM backend against
a local (free, offline) model.  It exists because a local model needs a few
different settings than a hosted API:

  * a longer timeout, because a 7B model on a laptop takes 20-50 seconds per
    receipt;
  * a concurrency setting, because the requests are I/O bound while the model
    generates and the wall clock drops sharply with a small thread pool.

Usage
-----
    python run_local_model.py --model qwen2.5:7b --concurrency 4
    python run_local_model.py --model qwen2.5:7b --limit 10      # smoke test
    python run_local_model.py --list                              # show installed models

The model is not required: without --model the agent runs its deterministic
offline backend, which needs no model at all.
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
DEFAULT_MODEL = "qwen2.5:7b"


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
        default=4,
        help="Receipts analysed in parallel (default: %(default)s)",
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
    print(f"Parallel  : {args.concurrency}\n")

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
    if limit > 0:
        argv.extend(["--limit", str(limit)])
    return A.main(argv)


if __name__ == "__main__":
    sys.exit(main())
