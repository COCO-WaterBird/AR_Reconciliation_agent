"""
Offline tests for the LLM backend of the AI Exception Resolution Agent.

A tiny HTTP server mimics an OpenAI-compatible chat-completions endpoint and
returns a canned answer, so the LLM code path (request build, JSON parsing,
validation) is exercised without any network access and no API key.

Run:
    pytest tests/test_llm_backend.py
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ar_reconciliation.agent import exception_agent as A


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
        self._respond(
            {
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
        )

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


@pytest.fixture(scope="module")
def server():
    """A fake OpenAI-compatible endpoint on a random local port."""
    httpd = HTTPServer(("127.0.0.1", 0), FakeHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        httpd.shutdown()


@pytest.fixture(autouse=True)
def _clear_received():
    FakeHandler.received.clear()
    yield
    FakeHandler.received.clear()


@pytest.fixture
def context() -> A.ExceptionContext:
    """One receipt with a single candidate invoice."""
    return A.ExceptionContext(
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


def _analysis_requests() -> list[dict]:
    """The requests that carried the receipt, i.e. not the schema probe."""
    return [
        request
        for request in FakeHandler.received
        if "BTRX-999999"
        in " ".join(str(m.get("content", "")) for m in request.get("messages", []))
    ]


def test_the_model_answer_is_parsed(server, context):
    """The backend returns the model's JSON as a dict."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    raw = backend.analyse(context)

    assert isinstance(raw, dict)
    assert raw["exception_type"] == "Short Payment"


def test_the_validator_rejects_an_invoice_outside_the_candidate_pool(server, context):
    """An invented invoice number must never survive validation."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    recommendation = A.validate_recommendation(backend.analyse(context), context)

    assert "INV-999999" not in recommendation.proposed_invoices
    assert recommendation.proposed_invoices == ["INV-900175"]


def test_the_proposed_amount_is_capped_at_the_cash_received(server, context):
    """The agent cannot recommend more cash than was received."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    recommendation = A.validate_recommendation(backend.analyse(context), context)

    assert recommendation.proposed_amount_cents <= context.net_amount_cents
    assert recommendation.proposed_amount_cents <= 837500


def test_a_review_only_scenario_is_never_auto_matched(server, context):
    """The engine's review-only decision is not overturned by the model."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    recommendation = A.validate_recommendation(backend.analyse(context), context)

    assert recommendation.recommended_action != "Auto Match"


def test_the_receipt_and_candidates_reach_the_model(server, context):
    """The request carries the receipt and the candidate invoices."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    backend.analyse(context)

    requests = _analysis_requests()
    assert requests, "no analysis request reached the fake endpoint"

    user_content = " ".join(
        str(m.get("content", "")) for m in requests[0].get("messages", [])
    )
    assert "INV-900175" in user_content
    assert "BTRX-999999" in user_content


def test_the_endpoint_is_asked_for_a_structured_response(server, context):
    """The request asks for JSON, so the answer can be parsed."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    backend.analyse(context)

    requests = _analysis_requests()
    response_format = requests[0].get("response_format", {})
    assert response_format.get("type") in {"json_object", "json_schema"}


def test_the_schema_probe_fires_exactly_once_under_concurrency(server, context):
    """
    The probe is a full model round trip.

    When the receipts are analysed in parallel, every worker thread would
    otherwise fire its own probe on the first batch, which wastes time and
    can confuse the endpoint.
    """
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(backend.analyse, [context] * 4))

    probes = [
        request
        for request in FakeHandler.received
        if request.get("response_format", {}).get("type") == "json_schema"
        and request.get("response_format", {}).get("json_schema", {}).get("name")
        == "probe"
    ]
    assert len(probes) == 1


def test_the_local_model_resource_limits_reach_the_endpoint(server, context):
    """
    The resource limits must actually be sent, not silently dropped.

    A local model on a laptop will otherwise keep its weights resident and
    use every core, which is enough to make the machine unusable.
    """
    backend = A.LLMBackend(
        api_key="test-key",
        model="test-model",
        base_url=server,
        num_ctx=2048,
        num_thread=4,
        keep_alive="0",
    )
    backend.analyse(context)

    assert FakeHandler.received, "the limited backend sent no request"
    sent = FakeHandler.received[-1]
    options = sent.get("options", {})
    assert options.get("num_ctx") == 2048
    assert options.get("num_thread") == 4
    assert sent.get("keep_alive") == "0"


def test_no_resource_limits_are_sent_when_none_are_configured(server, context):
    """A hosted endpoint is not given keys it does not understand."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    backend.analyse(context)

    assert FakeHandler.received
    sent = FakeHandler.received[-1]
    assert "options" not in sent
    assert "keep_alive" not in sent


def test_the_token_usage_is_accumulated(server, context):
    """The backend reports what it spent, so a paid run can show the cost."""
    backend = A.LLMBackend(api_key="test-key", model="test-model", base_url=server)
    backend.analyse(context)

    # The fake endpoint reports one token in and one token out.
    assert backend.prompt_tokens == 1
    assert backend.completion_tokens == 1
