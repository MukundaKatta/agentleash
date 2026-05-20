"""End-to-end demo: a Locus-style shopping agent under an AgentLeash.

This file intentionally avoids the real Locus SDK so the demo runs offline
and reviewers can exercise the leash deterministically. The handler shape
matches what a real `locus.payments.charge(...)` call would look like.

Run:

    python examples/locus_payments_demo.py

You'll see four scenarios:
  1. happy path — agent makes a small allowed charge
  2. usd-cap denial — agent tries to spend more than the leash allows
  3. tool-args denial — agent passes a bad amount (negative)
  4. egress denial — agent tries to call a host outside the allowlist

Each scenario writes one or more JSONL lines to `runs/locus_demo.jsonl` so
you can `tail -f` it during a screen-recorded demo.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from agentleash import (
    BudgetExceededError,
    EgressDeniedError,
    Leash,
    ToolArgsInvalidError,
)


# ---- fake Locus payments handler --------------------------------------------


@dataclass
class FakeChargeReceipt:
    id: str
    amount_usd: float
    fee_usd: float


def fake_locus_charge(*, customer_id: str, amount_usd: float) -> FakeChargeReceipt:
    """Stand-in for `locus.payments.charge(...)`."""
    if amount_usd <= 0:
        raise ValueError("amount_usd must be positive (handler-side guard)")
    fee = round(0.029 * amount_usd + 0.30, 4)  # Stripe-ish
    return FakeChargeReceipt(
        id=f"ch_demo_{int(amount_usd * 100):06d}",
        amount_usd=amount_usd,
        fee_usd=fee,
    )


# ---- schema -----------------------------------------------------------------

CHARGE_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_id": {"type": "string", "minLength": 3},
        "amount_usd": {"type": "number", "minimum": 0.5, "maximum": 100.0},
    },
    "required": ["customer_id", "amount_usd"],
}


# ---- demo -------------------------------------------------------------------


def main() -> None:
    audit_dir = "runs"
    if os.path.exists(audit_dir):
        shutil.rmtree(audit_dir)

    leash = Leash(
        usd_cap=10.0,
        call_cap=20,
        allowed_hosts={"api.locus.io"},
        audit_path=f"{audit_dir}/locus_demo.jsonl",
    )

    with leash.session("locus-demo") as session:
        # 1. Happy path
        print("\n[1] Happy path — $4.99 charge")
        receipt = session.tool(
            name="locus.payments.charge",
            args={"customer_id": "cus_123", "amount_usd": 4.99},
            schema=CHARGE_SCHEMA,
            handler=fake_locus_charge,
            usd=4.99,
        )
        print(f"    receipt: {receipt}")

        # 2. USD-cap denial: trying to spend another $7 would push past the $10 cap.
        print("\n[2] USD-cap denial — $7.00 charge on top of $4.99 already spent")
        try:
            session.tool(
                name="locus.payments.charge",
                args={"customer_id": "cus_123", "amount_usd": 7.00},
                schema=CHARGE_SCHEMA,
                handler=fake_locus_charge,
                usd=7.00,
            )
        except BudgetExceededError as e:
            print(f"    denied: {e}")

        # 3. Tool-args denial: negative amount.
        print("\n[3] Tool-args denial — $-1.00 (negative) charge")
        try:
            session.tool(
                name="locus.payments.charge",
                args={"customer_id": "cus_123", "amount_usd": -1.00},
                schema=CHARGE_SCHEMA,
                handler=fake_locus_charge,
                usd=0.0,
            )
        except ToolArgsInvalidError as e:
            print(f"    denied: {e}")

        # 4. Egress denial: agent tries to call an unapproved host.
        print("\n[4] Egress denial — fetch from random C2")
        try:
            session.check_url("https://evil.attacker.example/exfil")
        except EgressDeniedError as e:
            print(f"    denied: {e}")

        print(f"\n[summary] usd_used=${session.usd_used:.2f}, calls_used={session.calls_used}")

    print(f"\nAudit log written to: {audit_dir}/locus_demo.jsonl")
    print("Inspect with:  cat runs/locus_demo.jsonl | jq '.'")


if __name__ == "__main__":
    main()
