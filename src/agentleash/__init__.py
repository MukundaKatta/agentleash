"""agentleash - safety + audit harness for money-making AI agents.

Drop one context manager around any agent that touches money (or any
sensitive resource), and the agent runs inside a leash you can prove
to yourself, to your boss, and to a reviewer:

    from agentleash import Leash

    leash = Leash(
        usd_cap=10.0,
        call_cap=50,
        allowed_hosts={"api.locus.io", "api.stripe.com"},
        audit_path="runs/agent.jsonl",
    )

    with leash.session() as session:
        # any tool the agent calls goes through session.tool(...)
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
        )
        session.record(usd=result.fee_usd)  # so the cap actually fires

If the agent tries to spend past `usd_cap`, hit more than `call_cap`
tools, reach a host outside `allowed_hosts`, or call a tool with args
that fail the JSON schema, the session raises a typed exception. The
agent stops. The audit log captures every successful + denied call.

Composes with the rest of the @mukundakatta agent-stack:

- `agentguard` / `agentguard-rs` (TS / Rust) for cross-language egress
- `agentvet` / `agentvet-rs` for tool-arg validation in non-Python agents
- `agenttrace` / `agenttrace-rs` for cost+latency aggregation
- `agentsnap` / `agentsnap-rs` for run-level snapshots

`agentleash` is the Python-native composition of the above for the
specific case of money-making agents."""

from __future__ import annotations

from .leash import (
    AuditEvent,
    BudgetExceededError,
    EgressDeniedError,
    Leash,
    LeashSession,
    LeashError,
    ToolArgsInvalidError,
)

__all__ = [
    "AuditEvent",
    "BudgetExceededError",
    "EgressDeniedError",
    "Leash",
    "LeashSession",
    "LeashError",
    "ToolArgsInvalidError",
]

__version__ = "0.1.0"
