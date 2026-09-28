# LLM Guardrails Gateway

[![ci](https://github.com/abdihakim-said/llm-guardrails-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/abdihakim-said/llm-guardrails-gateway/actions/workflows/ci.yml)

A small, tested Python library that puts deterministic controls around LLM features and AI agents: per-team budgets, cost per call, redaction, tool allow-lists, human approval for risky actions, and a tamper-evident audit log. It is the pattern I implement in an [AI Spend & Guardrails engagement](https://abdihakim-said.github.io/#work-with-me).

**The idea: the model can ask for anything, but deterministic code decides what actually happens.**

Every model call and every agent tool call goes through one `Gateway`:

```
model call:  price check → reserve worst-case cost from the team budget → provider
             → settle the actual cost → redacted, hash-chained audit entry → OpenTelemetry

tool call:   allow-list for this agent → (park for human approval) → execute → audit entry
```

## What it does

| Control | How | Why it matters |
|---|---|---|
| **Per-team monthly budgets** | Reserves the *worst-case* cost before the call and settles the real cost after. Teams with no budget are denied. | A runaway loop stops at the limit instead of at the invoice. Concurrent calls can't overshoot. |
| **Cost per call** | Usage × a price table, including prompt-cache reads and writes. Unpriced models are refused, never guessed. | You can see spend per team, per model, per call. |
| **Redaction before logging** | Emails, UK phone and NI numbers, NHS numbers (mod-11 check), payment cards (Luhn check), API keys. | Logs and traces don't become a second copy of your customers' personal data (UK GDPR). |
| **Tool allow-list per agent** | Deny by default. Anything not listed for that agent is refused. | An agent can't call a tool it was never meant to have, whatever the prompt says. |
| **Human approval for risky tools** | The call is parked. A named person approves or rejects it (CLI here; a Slack/Teams button would call the same two methods). Approvals are single-use, expire after 1 hour, and only cover the exact arguments that were approved. | "Refund £40" can't be approved and then run as "refund £4,000". |
| **Tamper-evident audit log** | Append-only JSON Lines, each entry carrying the SHA-256 of the previous one. `guardrails verify` detects edits and deletions. | You can show who approved what, and prove the log wasn't changed afterwards. |
| **OpenTelemetry** | Spans and metrics using the GenAI semantic conventions (`gen_ai.usage.input_tokens`, …) plus `llm.cost_usd`. No-op without the `otel` extra. | Cost and latency per call appear in the tracing stack you already run. |
| **Claude adapter** | Official Anthropic SDK; checks `stop_reason` for refusals before reading content; opts into server-side refusal fallback; prices the call by the model that actually served it. | Refusals are handled explicitly, and a fallback doesn't skew the cost figures. |

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,otel]"
pytest -q                 # 20 tests, no network or API key needed
python examples/demo.py   # budgets, a denied tool, a human approval, audit verification
```

Demo output:

```
1. Model calls until the team budget ($0.02/month) runs out
   6 calls ran, then blocked before reaching the provider: team 'support' budget exceeded ...
2. A tool the agent was never given
   denied: agent 'support-bot' may not call 'delete_customer'
3. A refund needs a human
   parked as a06abc80c191; approving as 'alice'
   result: {'refunded': 40}
4. Audit log
   9 entries, hash chain intact; emails are stored as [EMAIL]
```

With Claude (`pip install -e ".[anthropic]"`, then an API key or `ant auth login`):

```python
from decimal import Decimal
from guardrails_gateway import AnthropicProvider, AuditLog, BudgetLedger, Gateway

gw = Gateway(AnthropicProvider(), BudgetLedger("budgets.db", {"search": Decimal("50")}),
             AuditLog("audit.jsonl"))
reply = gw.complete("search", "claude-opus-5", [{"role": "user", "content": "Summarise ..."}])
```

People in the loop:

```bash
guardrails pending --db approvals.db
guardrails approve --db approvals.db <id> --by alice
guardrails verify audit.jsonl
```

## Design limits

- **It's a library, not a proxy.** Controls only apply to code that calls the gateway. A service calling the provider SDK directly bypasses it. In production, pair it with network egress rules or keys that only the gateway holds.
- **Redaction is pattern-based.** It catches structured identifiers, not names, addresses or free-text health details. For clinical text, add an NER-based redactor.
- **Single-process storage.** SQLite is fine for one service. Several replicas need a shared store (Postgres or DynamoDB with conditional writes).
- **No streaming.** `complete()` is request/response. Streaming would need budget settlement at the end of the stream.
- **The price table is configuration.** Check your provider's current prices, and pass your own `PriceTable` for negotiated rates or other providers.
- **One provider adapter.** Claude is included; other providers implement the same two-method `Provider` interface.

## Layout

```
src/guardrails_gateway/
  gateway.py     the two entry points: complete() and call_tool()
  budget.py      reserve / settle / release ledger
  pricing.py     price table and cost maths
  redact.py      redaction rules with checksums
  policy.py      per-agent allow-list and approval list
  approvals.py   approval queue: single use, expiring, bound to the exact arguments
  audit.py       hash-chained audit log + verify()
  telemetry.py   OpenTelemetry spans and metrics
  providers.py   Provider interface, FakeProvider, AnthropicProvider
  cli.py         pending / approve / reject / verify
```

---

Built by [Abdihakim Said](https://abdihakim-said.github.io), HumanLayer AI Ltd. MIT licensed.
