"""
Offline test for the LLM backend of the AI Exception Resolution Agent.

A tiny HTTP server mimics an OpenAI-compatible chat-completions endpoint and
returns a canned answer, so the LLM code path (request build, JSON parsing,
validation) is exercised without any network access or API key.

Run:
    python test_llm_backend.py
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import ai_exception_agent as A


class FakeHandler(BaseHTTPRequestHandler):
    """Returns a fixed JSON answer and records the requests it received."""

    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeHandler.received.append(body)

        # The canned answer deliberately contains a field the validator must
        # reject (an invoice that is not in the candidate pool) and an amount
        # that is too large, so the test proves the validator repairs the
        # model rather than trusting it.
        answer = {
            "exception_type": "Short Payment",
            "root_cause": "The payer withheld a freight claim deduction.",
            "recommended_action": "Auto Match",
            "confidence": "High",
            "proposed_invoices": ["INV-999999", "INV-900175"],
            "proposed_amount": 999999.99,
            "proposed_residual": 10.0,
            "accounting_treatment": "Apply the net cash and raise a deduction case.",
            "gl_posting": "Dr Bank / Cr AR",
            "explanation": "Test answer.",
        }

        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "test-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(answer),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # silence the default stderr logging
        return


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), FakeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    backend = A.LLMBackend(
        api_key="test-key",
        model="test-model",
        base_url=f"http://127.0.0.1:{port}/v1",
    )

    context = A.ExceptionContext(
        bank_transaction_id="BTRX-999999",
        value_date="2026-06-01",
        posting_date="2026-06-01",
        customer_name="Test Customer",
        remitter_name="Test Customer",
        customer_id="CUST-0001",
        currency="USD",
        gross_amount_cents=812375,
        bank_fee_cents=0,
        net_amount_cents=812375,
        memo="Payment less freight claim",
        memo_scenario="Duplicate Review",
        extracted_invoices="INV-900175",
        engine_reason="test",
        candidates=[
            A.Candidate(
                ar_index=0,
                invoice_number="INV-900175",
                customer_id="CUST-0001",
                customer_name="Test Customer",
                currency="USD",
                open_amount_cents=837500,
                due_date="2026-07-01",
                invoice_date="2026-05-01",
                dispute_reason="",
                residual_cents=25125,
            )
        ],
    )

    raw = backend.analyse(context)
    print("Model answered:", json.dumps(raw, indent=2))

    recommendation = A.validate_recommendation(raw, context)

    print("\n--- Validated recommendation ---")
    print(f"exception type     : {recommendation.exception_type}")
    print(f"recommended action : {recommendation.recommended_action}")
    print(f"confidence         : {recommendation.confidence}")
    print(f"proposed invoices  : {recommendation.proposed_invoices}")
    print(f"proposed amount    : {recommendation.proposed_amount_cents / 100:.2f}")
    print(f"proposed residual  : {recommendation.proposed_residual_cents / 100:.2f}")

    print("\n--- Validation notes ---")
    for note in recommendation.validation_notes:
        print(f"  * {note}")

    failures: list[str] = []

    if "INV-999999" in recommendation.proposed_invoices:
        failures.append("an invoice outside the candidate pool was accepted")
    if recommendation.proposed_amount_cents > context.net_amount_cents:
        failures.append("the proposed amount was not capped at the cash received")
    if recommendation.proposed_amount_cents > 837500:
        failures.append("the proposed amount exceeds the open amount of the invoice")
    if recommendation.recommended_action == "Auto Match":
        failures.append("a scenario the engine marked review-only was auto-matched")

    # The request that was sent must contain the receipt and the candidates.
    if not FakeHandler.received:
        failures.append("no request reached the fake endpoint")
    else:
        sent = FakeHandler.received[0]
        messages = sent.get("messages", [])
        user_content = " ".join(str(m.get("content", "")) for m in messages)
        if "BTRX-999999" not in user_content:
            failures.append("the receipt id was not sent to the model")
        if "INV-900175" not in user_content:
            failures.append("the candidate invoice was not sent to the model")
        if sent.get("response_format", {}).get("type") != "json_object":
            failures.append("the request did not ask for a JSON object response")

    server.shutdown()

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  x {failure}")
        return 1

    print("\nAll LLM backend checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
