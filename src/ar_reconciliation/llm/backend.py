"""
The LLM backend.

An OpenAI-compatible chat-completions client.  It works with OpenAI, Azure
OpenAI, Ollama, LM Studio, vLLM, llama.cpp's server and any other endpoint
that speaks the OpenAI chat API.

Two levels of structured output are used, because local models drift off a
schema much more often than hosted ones:

  1. ``response_format={"type": "json_schema", ...}`` is used when the
     endpoint advertises support for it.  The model is then constrained by
     the grammar, so it cannot emit a value outside the controlled
     vocabulary or a key that is not in the schema.
  2. Otherwise ``response_format={"type": "json_object"}`` is used, which at
     least guarantees a parsable JSON object.

A local endpoint that silently ignores ``json_schema`` is detected by probing
it once, and the backend falls back to the plain JSON mode.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

__all__ = ["LLMBackend", "parse_json_object"]


def parse_json_object(content: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Model response was not valid JSON: {text[:300]!r}")


class LLMBackend:
    """
    OpenAI-compatible chat-completions backend.

    Works with OpenAI, Azure OpenAI, Ollama, LM Studio, vLLM, llama.cpp's
    server and any other endpoint that speaks the OpenAI chat API.

    Two levels of structured output are used, because local models drift off a
    schema much more often than hosted ones:

      1. ``response_format={"type": "json_schema", ...}`` is used when the
         endpoint advertises support for it.  The model is then constrained by
         the grammar, so it cannot emit a value outside the controlled
         vocabulary or a key that is not in the schema.
      2. Otherwise ``response_format={"type": "json_object"}`` is used, which
         at least guarantees a parsable JSON object.

    A local endpoint that silently ignores ``json_schema`` is detected by
    probing it once, and the backend falls back to the plain JSON mode.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 300.0,
        max_retries: int = 3,
        disable_thinking: bool = False,
        num_ctx: int | None = None,
        num_thread: int | None = None,
        keep_alive: str | int | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "The 'openai' package is required for the LLM backend. "
                "Install it with: python -m pip install openai"
            ) from exc

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.model = model
        self._supports_json_schema: bool | None = None
        # Running token totals, so a paid run can report what it spent.
        self.prompt_tokens = 0
        self.completion_tokens = 0
        # The probe is a one-off request that costs a full model round trip, so
        # it must run exactly once even when the receipts are analysed in
        # parallel.  Without the lock, every worker thread would fire its own
        # probe on the first batch.
        self._probe_lock = threading.Lock()
        # The qwen3 family enables a "thinking" mode by default.  It is slower
        # and, with a grammar-constrained decoder, it competes with the
        # constraint for the token budget.  Turning it off is worthwhile for a
        # structured extraction task like this one.
        self._extra_body: dict[str, Any] = {"think": False} if disable_thinking else {}

        # --- Resource controls for a local model -------------------------
        #
        # A local 7B model on a laptop is the resource-hungry case: it keeps
        # its weights and KV cache resident between requests and it will
        # happily use every CPU core.  On a 16 GB machine that is enough to
        # make the desktop unusable, so the caller can cap the context
        # length, the number of inference threads and the time the model stays
        # loaded after the last request.
        #
        # These are Ollama/LM Studio options.  A hosted OpenAI endpoint
        # ignores unknown keys, so passing them is harmless there.
        options: dict[str, Any] = {}
        if num_ctx:
            options["num_ctx"] = num_ctx
        if num_thread:
            options["num_thread"] = num_thread
        if options:
            self._extra_body["options"] = options
        if keep_alive is not None:
            self._extra_body["keep_alive"] = keep_alive

    # -- structured output negotiation -------------------------------------

    def _probe_json_schema_support(self) -> bool:
        """
        Find out whether the endpoint honours a json_schema response format.

        The probe is a throwaway request with a tiny schema.  A local endpoint
        that does not understand the format either errors out or returns
        something that does not match the schema.

        The result is cached, and the probe is guarded by a lock so that
        parallel analysis does not fire one probe per worker thread.
        """
        if self._supports_json_schema is not None:
            return self._supports_json_schema

        with self._probe_lock:
            # Another thread may have completed the probe while this one
            # waited for the lock.
            if self._supports_json_schema is not None:
                return self._supports_json_schema

            probe_schema = {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            }
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_tokens=16,
                    messages=[{"role": "user", "content": "Return {\"ok\": true}."}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "probe",
                            "strict": True,
                            "schema": probe_schema,
                        },
                    },
                )
                content = (response.choices[0].message.content or "").strip()
                parsed = parse_json_object(content)
                self._supports_json_schema = parsed.get("ok") is True
            except Exception:
                self._supports_json_schema = False

        return self._supports_json_schema

    def _response_format(self) -> dict[str, Any]:
        from ar_reconciliation.agent.prompt import RESPONSE_SCHEMA

        if self._probe_json_schema_support():
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "ar_exception_analysis",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            }
        return {"type": "json_object"}

    # -- analysis ----------------------------------------------------------

    def analyse(self, context: Any) -> dict[str, Any]:
        """Send one receipt to the model and return the parsed JSON object."""
        from ar_reconciliation.agent.prompt import SYSTEM_PROMPT, build_user_prompt

        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(context)},
            ],
            "response_format": self._response_format(),
        }
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or "{}"

        # Keep a running total of the tokens spent.  On a paid API this is the
        # number that turns into the bill, so it is worth showing.
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0

        return parse_json_object(content)

