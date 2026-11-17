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
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import ai_exception_agent as A


class FakeHandler(BaseHTTPRequestHandler):
    """Returns a fixed JSON answer and records the requests it received."""

    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeHandler.received.append(body)

        # The backend probes the endpoint once to find out whether it honours
        # a json_schema response format.  Answer the probe correctly so the
        # backend takes its schema-constrained path.
        response_format = body.get("response_format", {})
        if response_format.get("type") == "json_schema":
            schema = response_format.get("json_schema", {}).get("schema", {})
            if schema.get("properties", {}).get("ok") is not None:
                self._respond({"ok": True})
                return

        # The canned answer deliberately contains fields the validator must
        # reject (an invoice that is not in the candidate pool, an amount that
        # is too large, a residual that disagrees with the AR data) and an
        # action the scenario forbids, so the test proves the validator
        # repairs the model rather than trusting it.
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
        self._respond(answer)

    def _respond(self, answer: dict) -> None:
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
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
    # The first request is the schema probe, so the analysis request is the
    # one that mentions the receipt.
    analysis_requests = [
        request
        for request in FakeHandler.received
        if "BTRX-999999" in " ".join(str(m.get("content", "")) for m in request.get("messages", []))
    ]
    if not analysis_requests:
        failures.append("no analysis request reached the fake endpoint")
    else:
        sent = analysis_requests[0]
        messages = sent.get("messages", [])
        user_content = " ".join(str(m.get("content", "")) for m in messages)
        if "INV-900175" not in user_content:
            failures.append("the candidate invoice was not sent to the model")
        # The endpoint must be asked for a constrained, structured response.
        response_format = sent.get("response_format", {})
        if response_format.get("type") not in {"json_object", "json_schema"}:
            failures.append("the request did not ask for a structured response")

    # --- The schema probe must fire exactly once ---
    #
    # The probe is a full model round trip.  When the receipts are analysed in
    # parallel, every worker thread would otherwise fire its own probe on the
    # first batch, which wastes time and can confuse the endpoint.
    FakeHandler.received.clear()
    parallel_backend = A.LLMBackend(
        api_key="test-key",
        model="test-model",
        base_url=f"http://127.0.0.1:{port}/v1",
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(parallel_backend.analyse, [context] * 4))

    probes = [
        request
        for request in FakeHandler.received
        if request.get("response_format", {}).get("type") == "json_schema"
        and request.get("response_format", {}).get("json_schema", {}).get("name") == "probe"
    ]
    if len(probes) != 1:
        failures.append(
            f"the schema probe fired {len(probes)} times under concurrency, expected exactly 1"
        )

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
