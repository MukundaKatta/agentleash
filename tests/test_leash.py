"""Tests for agentleash.Leash."""

from __future__ import annotations

import json

import pytest

from agentleash import (
    BudgetExceededError,
    EgressDeniedError,
    Leash,
    ToolArgsInvalidError,
)


# ---- handler factory -------------------------------------------------------


def echo_handler(**kwargs):
    return dict(kwargs)


# ---- happy path ------------------------------------------------------------


def test_tool_ok_records_audit(tmp_path):
    audit = tmp_path / "audit.jsonl"
    leash = Leash(usd_cap=10.0, audit_path=str(audit))

    with leash.session("s1") as s:
        out = s.tool(
            name="charge",
            args={"customer_id": "cus_1", "amount_usd": 1.00},
            handler=echo_handler,
            usd=1.00,
        )
        assert out["customer_id"] == "cus_1"
        assert s.usd_used == 1.00
        assert s.calls_used == 1

    events = [json.loads(line) for line in audit.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert "session_open" in kinds
    assert "tool_ok" in kinds
    assert "session_close" in kinds


def test_no_audit_when_path_is_none():
    leash = Leash(usd_cap=10.0)
    with leash.session() as s:
        s.tool(name="x", args={}, handler=echo_handler, usd=0.5)
        assert s.usd_used == 0.5


# ---- USD cap ---------------------------------------------------------------


def test_usd_cap_fires(tmp_path):
    leash = Leash(usd_cap=2.0, audit_path=str(tmp_path / "a.jsonl"))
    with leash.session() as s:
        s.tool(name="t", args={}, handler=echo_handler, usd=1.5)
        with pytest.raises(BudgetExceededError):
            s.tool(name="t", args={}, handler=echo_handler, usd=1.5)


def test_record_post_call_fires_cap(tmp_path):
    leash = Leash(usd_cap=2.0, audit_path=str(tmp_path / "a.jsonl"))
    with leash.session() as s:
        s.tool(name="t", args={}, handler=echo_handler, usd=1.0)
        with pytest.raises(BudgetExceededError):
            s.record(usd=1.5)


# ---- call cap --------------------------------------------------------------


def test_call_cap_fires(tmp_path):
    leash = Leash(call_cap=2, audit_path=str(tmp_path / "a.jsonl"))
    with leash.session() as s:
        s.tool(name="t", args={}, handler=echo_handler)
        s.tool(name="t", args={}, handler=echo_handler)
        with pytest.raises(BudgetExceededError):
            s.tool(name="t", args={}, handler=echo_handler)


# ---- egress allowlist ------------------------------------------------------


def test_egress_allows_listed_host(tmp_path):
    leash = Leash(
        allowed_hosts={"api.locus.io", "*.stripe.com"},
        audit_path=str(tmp_path / "a.jsonl"),
    )
    with leash.session() as s:
        s.check_url("https://api.locus.io/charges")
        s.check_url("https://hooks.stripe.com/v1")


def test_egress_denies_unlisted_host(tmp_path):
    leash = Leash(
        allowed_hosts={"api.locus.io"},
        audit_path=str(tmp_path / "a.jsonl"),
    )
    with leash.session() as s:
        with pytest.raises(EgressDeniedError):
            s.check_url("https://evil.example/exfil")


def test_egress_denies_with_empty_allowlist():
    leash = Leash()  # empty allowlist == deny all
    with leash.session() as s:
        with pytest.raises(EgressDeniedError):
            s.check_url("https://api.locus.io/")


# ---- tool-arg validation ---------------------------------------------------


def test_args_schema_passes_valid():
    schema = {
        "type": "object",
        "properties": {
            "amount_usd": {"type": "number", "minimum": 0, "maximum": 100},
        },
        "required": ["amount_usd"],
    }
    leash = Leash()
    with leash.session() as s:
        s.tool(
            name="charge",
            args={"amount_usd": 50},
            schema=schema,
            handler=echo_handler,
        )


def test_args_schema_rejects_out_of_range():
    pytest.importorskip("jsonschema")
    schema = {
        "type": "object",
        "properties": {"amount_usd": {"type": "number", "maximum": 100}},
        "required": ["amount_usd"],
    }
    leash = Leash()
    with leash.session() as s:
        with pytest.raises(ToolArgsInvalidError):
            s.tool(
                name="charge",
                args={"amount_usd": 9999},
                schema=schema,
                handler=echo_handler,
            )


# ---- audit content ---------------------------------------------------------


def test_audit_captures_denials(tmp_path):
    audit = tmp_path / "a.jsonl"
    leash = Leash(usd_cap=1.0, audit_path=str(audit))
    with leash.session("sX") as s:
        with pytest.raises(BudgetExceededError):
            s.tool(name="t", args={}, handler=echo_handler, usd=5.0)

    events = [json.loads(line) for line in audit.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert "budget_denied" in kinds
    assert "session_close" in kinds
