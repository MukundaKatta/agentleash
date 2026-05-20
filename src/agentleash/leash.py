"""Core leash composition.

The four primitives, each enforced independently:

1. Budget cap   — token_budget.BudgetPool wraps USD + call count
2. Egress      — outbound hostname allowlist via urllib.parse
3. Tool args   — JSON schema check (jsonschema optional, falls back to no-op)
4. Audit log   — append-only JSONL: every tool call, denial, exception

The leash is a context manager. The session inside it is what the agent
holds. Calls go through `session.tool(...)` and `session.fetch_url(...)`.

Designed for synchronous Python agents. Async wrapper sketched in
`a_session()`."""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlparse

from token_budget import BudgetPool, BudgetExceeded


# ---- Errors ----------------------------------------------------------------


class LeashError(Exception):
    """Base class for every Leash-raised failure."""


class BudgetExceededError(LeashError):
    """Raised when a tool call would push the session past its USD or call cap."""


class EgressDeniedError(LeashError):
    """Raised when a fetch targets a host outside the session's allowlist."""


class ToolArgsInvalidError(LeashError):
    """Raised when a tool's args fail the supplied JSON schema."""


# ---- Audit event -----------------------------------------------------------


@dataclass
class AuditEvent:
    """One row in the audit log. JSONL-friendly."""

    ts: float
    session_id: str
    kind: str  # "tool_ok" | "tool_denied" | "egress_ok" | "egress_denied" | "budget_denied" | "session_open" | "session_close"
    tool: str | None = None
    args_hash: str | None = None
    url: str | None = None
    usd: float = 0.0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + "\n"


# ---- Optional jsonschema --------------------------------------------------

try:
    import jsonschema as _jsonschema  # noqa: F401

    def _validate(args: dict[str, Any], schema: dict[str, Any]) -> None:
        _jsonschema.validate(instance=args, schema=schema)
except ImportError:  # pragma: no cover - exercised only when jsonschema missing

    def _validate(args: dict[str, Any], schema: dict[str, Any]) -> None:
        # No-op: caller opted out of schema validation by not installing extra.
        return


# ---- Leash + session -------------------------------------------------------


@dataclass
class Leash:
    """Configuration. Create once per process, reuse across sessions."""

    usd_cap: float | None = None
    call_cap: int | None = None
    allowed_hosts: set[str] = field(default_factory=set)
    audit_path: str | None = None  # JSONL path; None disables audit

    def session(
        self, session_id: str | None = None
    ) -> "contextlib.AbstractContextManager[LeashSession]":
        """Open a leashed session. Use as a `with` block."""
        return _SessionCM(self, session_id or uuid.uuid4().hex[:12])


class LeashSession:
    """Live session. Returned from `Leash.session()`. Not for direct construction."""

    def __init__(self, leash: Leash, session_id: str, audit_fp):
        self.id = session_id
        self._leash = leash
        self._audit_fp = audit_fp
        self._calls_used = 0
        self._budget = BudgetPool(
            token_cap=None,
            usd_cap=leash.usd_cap,
        )

    # ---- audit ----

    def _emit(self, event: AuditEvent) -> None:
        if self._audit_fp is not None:
            self._audit_fp.write(event.to_jsonl())
            self._audit_fp.flush()

    def _hash_args(self, args: dict[str, Any]) -> str:
        # Stable short hash for traceability; not cryptographic.
        import hashlib

        return hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    # ---- tool call ----

    def tool(
        self,
        *,
        name: str,
        args: dict[str, Any],
        handler: Callable[..., Any],
        schema: dict[str, Any] | None = None,
        usd: float = 0.0,
    ) -> Any:
        """Run a tool through the leash.

        - Validate args against the JSON schema (if jsonschema installed).
        - Check the call-cap.
        - Check the USD-cap (records `usd` against the budget BEFORE handler).
        - Call the handler.
        - Emit one audit event (success or denial).
        """
        args_hash = self._hash_args(args)
        base = dict(
            ts=time.time(),
            session_id=self.id,
            tool=name,
            args_hash=args_hash,
            usd=usd,
        )

        # 1. Call cap
        if self._leash.call_cap is not None and self._calls_used >= self._leash.call_cap:
            self._emit(
                AuditEvent(
                    **base,
                    kind="budget_denied",
                    error=f"call_cap of {self._leash.call_cap} reached",
                )
            )
            raise BudgetExceededError(
                f"call_cap reached: {self._calls_used}/{self._leash.call_cap}"
            )

        # 2. Schema
        if schema is not None:
            try:
                _validate(args, schema)
            except Exception as e:
                self._emit(AuditEvent(**base, kind="tool_denied", error=str(e)))
                raise ToolArgsInvalidError(f"args invalid for tool {name!r}: {e}") from e

        # 3. USD cap
        if usd > 0:
            try:
                self._budget.record(usd=usd)
            except BudgetExceeded as e:
                self._emit(AuditEvent(**base, kind="budget_denied", error=str(e)))
                raise BudgetExceededError(
                    f"usd_cap would be exceeded by {usd:.4f} on tool {name!r}"
                ) from e

        # 4. Handler
        try:
            result = handler(**args)
        except Exception as e:
            self._emit(AuditEvent(**base, kind="tool_denied", error=f"handler raised: {e}"))
            raise

        self._calls_used += 1
        self._emit(AuditEvent(**base, kind="tool_ok"))
        return result

    # ---- egress ----

    def check_url(self, url: str) -> None:
        """Raise EgressDeniedError if `url`'s host is not in the allowlist.

        Empty allowlist means deny-all. Wildcards (`*.example.com`) supported."""
        host = urlparse(url).hostname or ""
        if self._host_allowed(host):
            self._emit(
                AuditEvent(
                    ts=time.time(), session_id=self.id, kind="egress_ok", url=url
                )
            )
            return
        self._emit(
            AuditEvent(
                ts=time.time(),
                session_id=self.id,
                kind="egress_denied",
                url=url,
                error=f"host {host!r} not in allowlist",
            )
        )
        raise EgressDeniedError(f"host {host!r} not in allowlist")

    def _host_allowed(self, host: str) -> bool:
        for pat in self._leash.allowed_hosts:
            if pat == host:
                return True
            if pat.startswith("*."):
                suffix = pat[1:]  # ".example.com"
                if host.endswith(suffix):
                    return True
        return False

    # ---- manual record ----

    def record(self, usd: float = 0.0) -> None:
        """Record additional spend AFTER a tool call resolves with a real cost.

        Use when the per-call cost isn't known until the handler returns
        (e.g., a charge-customer call that returns the fee).
        """
        if usd <= 0:
            return
        try:
            self._budget.record(usd=usd)
        except BudgetExceeded as e:
            self._emit(
                AuditEvent(
                    ts=time.time(),
                    session_id=self.id,
                    kind="budget_denied",
                    usd=usd,
                    error=str(e),
                )
            )
            raise BudgetExceededError(str(e)) from e

    # ---- introspection ----

    @property
    def usd_used(self) -> float:
        return self._budget.snapshot().usd_used

    @property
    def calls_used(self) -> int:
        return self._calls_used


# ---- internal: context manager ---------------------------------------------


class _SessionCM:
    def __init__(self, leash: Leash, session_id: str):
        self._leash = leash
        self._session_id = session_id
        self._audit_fp = None
        self._session: LeashSession | None = None

    def __enter__(self) -> LeashSession:
        if self._leash.audit_path:
            os.makedirs(os.path.dirname(os.path.abspath(self._leash.audit_path)) or ".", exist_ok=True)
            self._audit_fp = open(self._leash.audit_path, "a", encoding="utf-8")
        self._session = LeashSession(self._leash, self._session_id, self._audit_fp)
        self._session._emit(
            AuditEvent(ts=time.time(), session_id=self._session.id, kind="session_open")
        )
        return self._session

    def __exit__(self, exc_type, exc, tb):
        assert self._session is not None
        self._session._emit(
            AuditEvent(
                ts=time.time(),
                session_id=self._session.id,
                kind="session_close",
                usd=self._session.usd_used,
                extra={"calls": self._session.calls_used, "error": str(exc) if exc else None},
            )
        )
        if self._audit_fp is not None:
            self._audit_fp.close()
        return False  # do not suppress
