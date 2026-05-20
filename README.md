# agentleash

[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/agentleash.svg)](https://pypi.org/project/agentleash/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

![demo](docs/demo.gif)

**Token + dollar caps, vendor allowlist, tool-arg validation, and an audit trail for money-making AI agents.**

Drop in two lines and your agent can't blow up your bank account.

```python
from agentleash import Leash

leash = Leash(
    usd_cap=10.0,
    call_cap=50,
    allowed_hosts={"api.locus.io", "api.stripe.com"},
    audit_path="runs/agent.jsonl",
)

with leash.session() as session:
    result = session.tool(
        name="charge_customer",
        args={"customer_id": "cus_123", "amount_usd": 4.99},
        schema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "amount_usd": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": ["customer_id", "amount_usd"],
        },
        handler=lambda **a: locus.payments.charge(**a),
        usd=4.99,
    )
```

If the agent tries to spend past `usd_cap`, hit more than `call_cap` tools, reach a host outside `allowed_hosts`, or call a tool with args that fail the schema — the session raises a typed exception, the agent stops, and the audit log captures the denial.

## Why

Every "agentic payments" demo eventually goes to prod and blows up the founder's bank account. The reason is always the same: there is no leash. The agent has the full API key, no per-call budget, no allowlist, no audit. `agentleash` is the leash.

Each safety primitive lives behind a single context manager. None of them try to be clever — they just refuse to let the agent do something stupid, and they write down what happened.

## Install

```bash
pip install agentleash             # core, MIT, zero runtime deps beyond token-budget-py
pip install "agentleash[jsonschema]"  # to enable tool-arg validation
pip install "agentleash[httpx]"       # to use the egress check from an httpx transport
```

## What it does

| Primitive | Configured via | Failure mode |
|---|---|---|
| **USD cap** | `Leash(usd_cap=10.0)` | `BudgetExceededError` |
| **Call cap** | `Leash(call_cap=50)` | `BudgetExceededError` |
| **Egress allowlist** | `Leash(allowed_hosts={"api.locus.io", "*.stripe.com"})` | `EgressDeniedError` |
| **Tool-arg schema** | per-call `schema={...}` | `ToolArgsInvalidError` |
| **Audit trail** | `Leash(audit_path="runs/agent.jsonl")` | JSONL appended; one line per event |

All four are independent. Use whichever subset you need.

## What it doesn't do

`agentleash` is not a model wrapper, not a payment library, not a vendor SDK. It assumes the agent already has a way to call payments, scraping, search, etc. — it just leashes those calls.

If you also want LLM-call cost tracking (per-token USD with cached-input pricing), pair with [GeminiLens](https://github.com/MukundaKatta/geminilens) (Gemini-side) or compute cost in your handler and pass it as `usd=` to `session.tool(...)`.

## Audit log shape

One event per line:

```json
{"ts": 1779311234.12, "session_id": "abc12345...", "kind": "tool_ok", "tool": "charge_customer", "args_hash": "a1b2c3d4e5f6...", "usd": 4.99}
{"ts": 1779311235.55, "session_id": "abc12345...", "kind": "tool_denied", "tool": "charge_customer", "args_hash": "deadbeef...", "error": "amount_usd: 1500 > 100"}
{"ts": 1779311236.99, "session_id": "abc12345...", "kind": "budget_denied", "usd": 4.99, "error": "usd_cap of 10.0 would be exceeded"}
{"ts": 1779311237.10, "session_id": "abc12345...", "kind": "session_close", "usd": 9.98, "extra": {"calls": 4, "error": null}}
```

Args hashes (not raw args) are written by default so the log is safe to share with a reviewer without leaking PII. Run with `LEASH_AUDIT_RAW_ARGS=1` to log args verbatim (not recommended for production).

## Companion libs

`agentleash` is the Python-native composition of:

- [agentguard](https://github.com/MukundaKatta/agentguard) + [agentguard-rs](https://github.com/MukundaKatta/agentguard-rs) — egress allowlist, TS / Rust
- [agentvet](https://github.com/MukundaKatta/agentvet) + [agentvet-rs](https://github.com/MukundaKatta/agentvet-rs) — tool-arg validation
- [agenttrace](https://github.com/MukundaKatta/agenttrace) + [agenttrace-rs](https://github.com/MukundaKatta/agenttrace-rs) — cost + latency aggregation
- [agentsnap](https://github.com/MukundaKatta/agentsnap) — run snapshots
- [token-budget-py](https://github.com/MukundaKatta/token-budget-py) — used directly under the hood

Use `agentleash` in Python; use the language-native libs above when you're not.

## Examples

- [`examples/locus_payments_demo.py`](examples/locus_payments_demo.py) — leash a Locus-powered shopping agent that can charge a card, with caps + allowlist + audit.

## Built for

The Locus Paygentic Hackathon #4 (Devfolio), the "agents that can make you money" track. Premise: the most valuable picks-and-shovels in agentic payments is the safety + audit layer.

## License

MIT. See [LICENSE](LICENSE).
