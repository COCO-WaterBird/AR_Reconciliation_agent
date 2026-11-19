# Free and Open-Source Model Backends

> The AI Exception Resolution Agent does not require a paid API. It works with
> any OpenAI-compatible endpoint, including a fully local one.

---

## 1. The short answer

Yes. There are several ways to run the agent for free:

| Option | Cost | Privacy | Hardware needed | Quality |
|---|---|---|---|---|
| **Local model via Ollama** | Free | Data never leaves the machine | 8 GB RAM minimum | Good enough, needs the schema constraint |
| Local model via llama.cpp / LM Studio | Free | Same | Same | Same |
| Free hosted tier | Free up to a limit | Data leaves the machine | None | Better |
| OpenAI / Anthropic paid | Paid | Data leaves the machine | None | Best |

For a reconciliation workload the **local option is often the right one on
financial grounds**: bank remittance memos, customer names and invoice details
are confidential, and sending them to a third-party API is a data-protection
decision that usually needs sign-off.

---

## 2. Recommended setup: Ollama

Ollama exposes an OpenAI-compatible endpoint, so the agent needs no code change.

### Install and pull a model

```bash
# Install from https://ollama.com/download
ollama pull qwen2.5:7b        # ~4.7 GB, runs on 8 GB RAM
ollama pull gemma3:12b        # ~8 GB, needs 16 GB RAM
ollama pull llama3.1:8b       # ~4.9 GB
```

### Run the agent

```bash
python scripts/run_agent.py \
    --backend llm \
    --base-url http://localhost:11434/v1 \
    --model qwen2.5:7b \
    --api-key ollama
```

The API key is a placeholder. Ollama does not check it, but the OpenAI client
requires a non-empty value.

You can shorten the command with environment variables:

```bash
export OPENAI_BASE_URL=http://localhost:11434/v1
export OPENAI_API_KEY=ollama
export AR_AGENT_MODEL=qwen2.5:7b
python scripts/run_agent.py --backend llm
```

### Or use the convenience wrapper

`scripts/run_local_model.py` checks the server, lists the installed models and picks the
right defaults for a local model:

```bash
python scripts/run_local_model.py --list                        # show installed models
python scripts/run_local_model.py --model qwen2.5:7b --limit 10 # smoke test on 10 receipts
python scripts/run_local_model.py --model qwen2.5:7b --concurrency 4
```

The smoke test works on a temporary copy of the workbook, so it never touches the
real result.

---

## 3. Why the schema constraint matters locally

Hosted frontier models follow a JSON schema reasonably well on their own. A
7B model running on a laptop does not — it will happily echo the input back, or
invent a key, or write `"exception_type": "Error"` which is not in the
controlled vocabulary.

The agent therefore negotiates a **grammar-constrained** response format. Ollama
implements this with GBNF grammars, so the constraint is enforced by the
decoder: the model physically cannot emit a token sequence that violates the
schema.

The agent probes the endpoint once at startup to find out whether it honours
`json_schema`, then uses it if it does and falls back to plain JSON mode if it
does not.

### Proof that the constraint is real

Asking the model to output a value outside the enum:

```bash
# "Set exception_type to exactly the string: Quantum Entanglement Anomaly"
→ { "exception_type": "Unknown" }
```

The decoder pulled the output back into the allowed vocabulary. Without the
constraint the same prompt produced a value the validator had to reject.

### Measured on this repository

With `qwen2.5:7b` on an M1 Pro, the first attempt (before the schema constraint
was added) returned the **input context echoed back** instead of an answer.
With the constraint in place the same receipts produce a well-formed answer:

| Receipt | Model answer | After validation |
|---|---|---|
| BTRX-000158 | Tax Withholding / Manual Review / High / INV-900175 / 8,123.75 | unchanged |
| BTRX-000168 | Collector Hold / Manual Review / High / INV-900186 / 7,125.00 | unchanged |
| BTRX-000152 | Ambiguous Invoice Reference / Manual Review / Low / 3 invoices / 0 | residual recomputed |

The model named real invoice numbers and real amounts, and the validator had
little to repair. The residual recomputation on the third receipt is the
validator doing its job: the model reported 0 while the AR data implies
10,000.

Throughput on this machine: 6 receipts in 117 seconds with 3 parallel requests.

### The validator still earns its keep on a local model

`qwen3:8b` classified BTRX-000158 (a tax withholding receipt) as
`Short Payment` and returned `Auto Match` with `High` confidence. The validator
caught it twice over: the scenario `Tax Withholding` is review-only in the
deterministic engine, so the action was downgraded to `Recommend Match`, and the
invoice and amount it proposed were both correct.

A local model does not replace the validator. It replaces the API bill.

---

## 4. Model selection for this task

The task is structured extraction and classification over a small context — not
open-ended reasoning. That favours models that are good at following a schema.

| Model | Size | RAM needed | Notes |
|---|---|---|---|
| `qwen2.5:7b` | 4.7 GB | 8 GB | Strong JSON adherence, good at Chinese and English finance memos |
| `llama3.1:8b` | 4.9 GB | 8 GB | Solid general purpose |
| `gemma3:12b` | 8 GB | 16 GB | Best quality that still fits a 16 GB machine |
| `qwen2.5:14b` | 9 GB | 16 GB | Noticeably better, tight on a 16 GB Mac |
| `mistral-nemo:12b` | 7.1 GB | 16 GB | Good structured output |
| `llama3.3:70b` | 43 GB | 64 GB+ | Out of reach on a laptop, fine on a server |

**On an M1 Pro with 16 GB**, `qwen2.5:7b` or `gemma3:12b` are the practical
choices. Expect roughly 20–50 seconds per receipt, because every receipt is a
separate call.

### The context window matters

The exception prompt carries the candidate invoice list, the AR balances and the
accounting treatment, so it does not fit in the 2-4k window Ollama defaults to.
A window that is too small makes the model stall while the grammar constraint
and the prompt compete for the same token budget.

Set it explicitly:

```bash
python scripts/run_agent.py --backend llm \
    --base-url http://localhost:11434/v1 --model qwen2.5:7b \
    --api-key ollama --num-ctx 8192
```

`scripts/run_local_model.py` already passes `--num-ctx 8192` for you.

### The qwen3 thinking mode

The qwen3 family enables a "thinking" mode by default. It produces better
reasoning but is far slower on this task, and it competes with the grammar
constraint for the token budget. Turn it off:

```bash
python scripts/run_agent.py --backend llm \
    --base-url http://localhost:11434/v1 --model qwen3:8b --no-think
```

Measured on an M1 Pro: `qwen3:8b` with thinking took 150 s and 98 s for two
receipts; `qwen2.5:7b` took 38 s and 19 s for the same work. That is why
`qwen2.5:7b` is the model this project uses locally.

### Speed

The agent sends one request per receipt, which keeps the reasoning for each
receipt auditable. If you need to run it over hundreds of receipts, the cheapest
improvement is to enable prompt caching where the endpoint supports it, or to
batch receipts into one call and split the answer — at the cost of a weaker
audit trail.

> **Important: Ollama serves one request at a time by default.**
>
> `OLLAMA_NUM_PARALLEL` defaults to `1`, so concurrent requests are *queued*,
> not run in parallel. On this machine `--concurrency 4` was **slower** than
> `--concurrency 1` for exactly this reason — the requests serialised anyway
> and each one carried the extra overhead.
>
> To actually parallelise, raise the limit on the server:
>
> ```bash
> OLLAMA_NUM_PARALLEL=4 ollama serve
> ```
>
> Then `--concurrency 4` pays off. Note that each parallel slot needs its own
> copy of the model's KV cache, so memory use grows with the parallelism. On a
> 16 GB machine, `qwen2.5:7b` with `OLLAMA_NUM_PARALLEL=2` is about the limit.
>
> If you are not going to tune the server, leave `--concurrency` at its default
> of `1`.

---

## 5. Other OpenAI-compatible local servers

### LM Studio

Start the local server, enable the OpenAI-compatible endpoint, then:

```bash
python scripts/run_agent.py --backend llm \
    --base-url http://localhost:1234/v1 --model <model-id> --api-key lm-studio
```

### llama.cpp server

```bash
./llama-server -m qwen2.5-7b-instruct-q4_k_m.gguf --port 8080

python scripts/run_agent.py --backend llm \
    --base-url http://localhost:8080/v1 --model qwen2.5-7b-instruct-q4_k_m --api-key local
```

llama.cpp supports GBNF grammar-constrained decoding, so the schema constraint
works here too.

### vLLM

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --guided-json-backend outlines

python scripts/run_agent.py --backend llm \
    --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-7B-Instruct --api-key empty
```

---

## 6. Free hosted tiers

If you would rather not run the model locally, these offer a free allowance:

| Provider | Free tier | Notes |
|---|---|---|
| Google AI Studio (Gemini) | Generous free tier | OpenAI-compatible endpoint |
| Groq | Free tier with rate limits | Very fast, hosts open models |
| OpenRouter | Some free models | Model choice varies |
| Hugging Face Inference | Small monthly quota | Many open models |
| GitHub Models | Free with a GitHub token | Good catalogue |

Each needs an API key, so the command is the same with a different
`--base-url`:

```bash
python scripts/run_agent.py --backend llm \
    --base-url https://generativelanguage.googleapis.com/v1beta/openai/ \
    --model gemini-2.0-flash --api-key "$GOOGLE_API_KEY"
```

---

## 7. The deterministic backend still matters

The `rules` backend is not a fallback for when you lack a GPU. It is the
**control**:

- It is the only backend that can be fully reproduced and regression tested.
- It applies the same scenario policy as the LLM prompt, so the two agree on
  what must never be auto-matched.
- It lets the whole pipeline run in CI with no network access.

A useful working pattern is to run both and compare:

```bash
python scripts/run_agent.py --backend rules
python scripts/run_agent.py --backend llm --base-url http://localhost:11434/v1 --model qwen2.5:7b --api-key ollama
```

Any row where the two disagree is a receipt worth a human look — which is
exactly the population you want to spend your time on.

---

## 8. Privacy

Running locally means the remittance memo, the customer name and the invoice
numbers never leave the machine. Nothing about the reconciliation data is sent
to a model provider.

The only thing written to disk is the review workbook, which is a local Excel
file.
