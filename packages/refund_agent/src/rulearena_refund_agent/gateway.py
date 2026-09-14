"""The agent's only way out: one HTTP gateway, and an optional guard behind it.

`ToolGateway` knows how to speak to the Commerce Sandbox and nothing else. Whether a
gate stands between it and the sandbox is decided by whoever constructed it, and it is
never named here -- the guard arrives as a structural type, so this package can hold one
without importing it (`INV-A`).

What the agent sees back is `ToolResult`, whose vocabulary is deliberately about
*observability*, not about who refused what: `REFUSED` means the call was not made,
`UNKNOWN` means its outcome could not be determined. A caller cannot tell a gate from a
rate limiter from a circuit breaker, which is the property that keeps the agent's
behaviour a function of the tools rather than of the gate.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import ConfigDict
from rulearena_domain_contracts import ActionType
from rulearena_reference_simulator import SimAction

from .models import StrictModel

__all__ = [
    "GuardVerdict",
    "ToolCallRecord",
    "ToolGateway",
    "ToolOutcome",
    "ToolResult",
    "ToolUnavailable",
    "WriteGuard",
]


class ToolOutcome(StrEnum):
    """What the agent may learn about a call. Nothing here is gate-specific on purpose."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    # The call was not made. The agent is told *that*, not *why*.
    REFUSED = "REFUSED"
    # The call may or may not have happened and nothing could settle it. Fail closed.
    UNKNOWN = "UNKNOWN"


class ToolUnavailable(RuntimeError):
    """The sandbox could not be reached, or answered with something that is not a snapshot."""


class ToolResult(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ToolOutcome
    action_type: ActionType
    idempotency_key: str | None = None
    receipt: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is ToolOutcome.SUCCEEDED

    @property
    def retryable(self) -> bool:
        """Only a definite failure or an unanswered call may be tried again.

        `UNKNOWN` is excluded on purpose: retrying something that may already have
        happened is exactly how one refund becomes two.
        """
        return self.outcome in {ToolOutcome.FAILED, ToolOutcome.TIMEOUT}


class GuardVerdict(StrictModel):
    """This package's own reading of whatever a guard answered.

    Two fields, both plain strings. The gateway records them for the benchmark and
    decides from them; it never holds the guard's own types.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: str
    reason_code: str
    cached_receipt: dict[str, Any] | None = None


@runtime_checkable
class WriteGuard(Protocol):
    """The seam a runtime gate plugs into.

    Structural on purpose: naming the gate's classes here would make the agent package
    depend on it, and `INV-A` says the agent must not be able to tell whether a gate
    exists. The return type is `object` because the gateway only reads two string fields
    off it -- it has no business knowing the shape.
    """

    async def pre_check(self, run_id: str, action: SimAction) -> object: ...

    async def retry_check(self, run_id: str, key: str) -> object: ...

    async def post_check(
        self, run_id: str, before: dict[str, Any], after: dict[str, Any]
    ) -> object: ...


class ToolCallRecord(StrictModel):
    """One tool call, as the benchmark needs to account for it.

    The agent holds the gateway and could read these; it does not. `guard_checks` counts
    every extra round trip the guard costs -- its own checks *and* the before/after
    snapshot reads it makes the gateway take -- so "门禁开销" is recomputable from the
    run rather than estimated from the gate's own bookkeeping.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_type: ActionType
    idempotency_key: str | None = None
    outcome: ToolOutcome
    guard_decision: str | None = None
    guard_reason: str | None = None
    guard_checks: int = 0
    elapsed_seconds: float = 0.0


_ALLOW = "ALLOW"
_BLOCK = "BLOCK"
_UNKNOWN = "UNKNOWN"

# What a guard-decided outcome is allowed to tell the agent. Generic on purpose: see
# `_refusal`.
TOOL_REFUSED = "CALL_NOT_MADE"
TOOL_OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


@dataclass
class _GuardTrace:
    decision: str | None = None
    reason: str | None = None
    checks: int = 0


def _read_verdict(verdict: object) -> GuardVerdict | None:
    """Normalise a guard's answer, or `None` when it is not an answer this gateway can use.

    A malformed verdict is not a pass: the caller turns it into `UNKNOWN`.
    """
    decision = getattr(verdict, "decision", None)
    reason = getattr(verdict, "reason_code", None)
    if decision is None or reason is None:
        return None
    cached = getattr(verdict, "cached_receipt", None)
    return GuardVerdict(
        decision=str(decision).upper(),
        reason_code=str(reason),
        cached_receipt=cached if isinstance(cached, dict) else None,
    )


def _error_from(response: httpx.Response) -> tuple[str | None, str | None]:
    try:
        payload = response.json()
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    detail = payload.get("detail", payload)
    if not isinstance(detail, dict):
        return None, None
    code = detail.get("code")
    message = detail.get("message")
    return (
        code if isinstance(code, str) else None,
        message if isinstance(message, str) else None,
    )


def _from_receipt(receipt: dict[str, Any], action: SimAction) -> ToolResult:
    """Read the outcome off the receipt, which is the only thing that states it.

    The three statuses map to three different situations for the caller: a rejection may
    be retried, a success may not be repeated, and anything else is a question the tool
    could not answer -- which the agent must never treat as a pass.
    """
    status = receipt.get("status")
    error = receipt.get("error")
    code = error.get("code") if isinstance(error, Mapping) else None
    message = error.get("message") if isinstance(error, Mapping) else None
    if status == "SUCCEEDED":
        outcome = ToolOutcome.SUCCEEDED
    elif status == "REJECTED":
        outcome = ToolOutcome.FAILED
    else:
        outcome = ToolOutcome.UNKNOWN
    return ToolResult(
        outcome=outcome,
        action_type=action.action_type,
        idempotency_key=action.idempotency_key,
        receipt=receipt if outcome is ToolOutcome.SUCCEEDED else None,
        error_code=(
            str(code) if code else (None if outcome is ToolOutcome.SUCCEEDED else str(status))
        ),
        error_message=str(message) if message else None,
    )


class ToolGateway:
    """The only outbound channel the agent has. It performs HTTP and nothing else.

    One gateway is bound to one sandbox run. `timeout` must stay below the sandbox's
    `SANDBOX_ACK_LOST_DELAY_SECONDS`, or a refund whose acknowledgement was lost would
    come back as a 504 instead of a client-side timeout -- both are handled, but the
    timeout is the shape the agent is written against.
    """

    def __init__(
        self,
        base_url: str,
        internal_token: str,
        *,
        run_id: str,
        guard: WriteGuard | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Internal-Service-Token": internal_token}
        self.run_id = run_id
        self.guard = guard
        self.timeout = timeout
        self.transport = transport
        self._calls: list[ToolCallRecord] = []

    @property
    def calls(self) -> tuple[ToolCallRecord, ...]:
        return tuple(self._calls)

    def _client(self) -> httpx.AsyncClient:
        # `trust_env=False`: these calls address an internal service on a host that is
        # already known. A proxy configured for the operator's own browsing must not be
        # able to sit between the agent and the ledger.
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
            transport=self.transport,
            trust_env=False,
        )

    async def inspect(self, run_id: str) -> dict[str, Any]:
        """Read a run's authoritative snapshot, or raise `ToolUnavailable`."""
        try:
            async with self._client() as client:
                response = await client.get(f"/internal/runs/{run_id}/snapshot")
        except httpx.HTTPError as error:
            raise ToolUnavailable(f"snapshot request failed: {type(error).__name__}") from error
        if response.status_code != httpx.codes.OK:
            raise ToolUnavailable(f"snapshot request returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise ToolUnavailable("snapshot body was not JSON") from error
        if not isinstance(payload, dict):
            raise ToolUnavailable("snapshot body was not an object")
        return payload

    async def call(self, action: SimAction) -> ToolResult:
        """Perform one tool call and report what could be established about it.

        Guards are consulted for writes only: a read cannot move the ledger, and holding
        one to the same three checks would make an unreadable snapshot stop an
        inspection the caller needs in order to decide anything at all.
        """
        write = action.action_type is not ActionType.INSPECT_STATE
        if write and not action.idempotency_key:
            # A write without a key cannot be recovered from a lost acknowledgement, so
            # letting it through would manufacture exactly the defect being measured.
            raise ValueError("a write action must carry an idempotency key")
        started = time.monotonic()
        trace = _GuardTrace()
        result = await self._dispatch(action, write, trace)
        self._calls.append(
            ToolCallRecord(
                action_type=action.action_type,
                idempotency_key=action.idempotency_key,
                outcome=result.outcome,
                guard_decision=trace.decision,
                guard_reason=trace.reason,
                guard_checks=trace.checks,
                elapsed_seconds=time.monotonic() - started,
            )
        )
        return result

    async def _dispatch(
        self, action: SimAction, write: bool, trace: _GuardTrace
    ) -> ToolResult:
        guard = self.guard
        if guard is None or not write:
            return await self._send(action)
        assert action.idempotency_key is not None

        # A write that already has a durable receipt is not a new write. This is asked
        # first, before the budget check, because it is the only question whose answer
        # cannot be got wrong by asking too early -- whereas asking it late is how the
        # *correct* recovery (re-send the same key to learn what happened) gets refused
        # for exceeding a budget the first attempt already spent.
        existing = _read_verdict(
            await guard.retry_check(self.run_id, action.idempotency_key)
        )
        trace.checks += 1
        if existing is None:
            return self._unknown(action, trace, "VERDICT_UNREADABLE")
        trace.decision, trace.reason = existing.decision, existing.reason_code
        if existing.decision != _ALLOW:
            return self._unknown(action, trace, existing.reason_code)
        if existing.cached_receipt is not None:
            # Short circuit, and deliberately without the post-check: the state this
            # write produced is not something to re-confirm from a later pair of
            # snapshots -- the durable receipt *is* the authority's own record of it,
            # and comparing snapshots taken either side of a replay would report the
            # absence of a movement that already happened.
            return _from_receipt(existing.cached_receipt, action)

        verdict = _read_verdict(await guard.pre_check(self.run_id, action))
        trace.checks += 1
        refused = self._refusal(verdict, action, trace)
        if refused is not None:
            return refused

        before = await self._snapshot_or_none()
        trace.checks += 1
        if before is None:
            return self._unknown(action, trace, "SNAPSHOT_UNAVAILABLE")

        result = await self._send(action)
        if result.outcome is ToolOutcome.TIMEOUT:
            # A timed-out write is the one case where the answer already exists and
            # simply did not come back. Asking the guard for it is what turns "unknown"
            # into "here is the receipt" -- without replaying anything.
            recovered = await self._recover(action, trace)
            if recovered is not None:
                result = recovered
        if not result.succeeded:
            return result

        after = await self._snapshot_or_none()
        trace.checks += 1
        if after is None:
            return self._unknown(action, trace, "SNAPSHOT_UNAVAILABLE")
        post = _read_verdict(await guard.post_check(self.run_id, before, after))
        trace.checks += 1
        if post is None or post.decision != _ALLOW:
            # The call reported success and the authority does not show it. Believing the
            # receipt here is what "自述成功但实际失败" measures; refusing to believe it is
            # the whole job of this stage.
            return self._unknown(
                action, trace, post.reason_code if post else "VERDICT_UNREADABLE"
            )
        return result

    def _refusal(
        self, verdict: GuardVerdict | None, action: SimAction, trace: _GuardTrace
    ) -> ToolResult | None:
        """`None` means "allowed to proceed"; anything else is the answer."""
        if verdict is None:
            return self._unknown(action, trace, "VERDICT_UNREADABLE")
        trace.decision, trace.reason = verdict.decision, verdict.reason_code
        if verdict.decision == _ALLOW:
            return None
        # The guard's reason code stays in the trace, not in the answer: it names which
        # check refused, and a caller that could read it would learn there is a gate and
        # which part of it spoke (`INV-A`). What the agent may know is the *shape* of the
        # outcome -- the call was not made -- which is the same thing a rate limiter or a
        # circuit breaker would have told it.
        if verdict.decision == _BLOCK:
            return ToolResult(
                outcome=ToolOutcome.REFUSED,
                action_type=action.action_type,
                idempotency_key=action.idempotency_key,
                error_code=TOOL_REFUSED,
            )
        # Anything that is neither ALLOW nor BLOCK is a question that could not be
        # answered, and the caller must treat it as a stop rather than a pass.
        return ToolResult(
            outcome=ToolOutcome.UNKNOWN,
            action_type=action.action_type,
            idempotency_key=action.idempotency_key,
            error_code=TOOL_OUTCOME_UNKNOWN,
        )

    async def _recover(self, action: SimAction, trace: _GuardTrace) -> ToolResult | None:
        guard = self.guard
        assert guard is not None
        assert action.idempotency_key is not None
        verdict = _read_verdict(await guard.retry_check(self.run_id, action.idempotency_key))
        trace.checks += 1
        if verdict is None or verdict.decision != _ALLOW:
            return self._unknown(
                action, trace, verdict.reason_code if verdict else "VERDICT_UNREADABLE"
            )
        trace.decision, trace.reason = verdict.decision, verdict.reason_code
        if verdict.cached_receipt is None:
            # Nothing was committed under this key, so a retry under it is safe. The
            # timeout stands and the caller decides what to do next.
            return None
        # The recovered receipt is read the same way a live one is: what a *previous*
        # attempt concluded is not automatically a success.
        return _from_receipt(verdict.cached_receipt, action)

    def _unknown(self, action: SimAction, trace: _GuardTrace, reason: str) -> ToolResult:
        trace.decision, trace.reason = _UNKNOWN, reason
        return ToolResult(
            outcome=ToolOutcome.UNKNOWN,
            action_type=action.action_type,
            idempotency_key=action.idempotency_key,
            error_code=TOOL_OUTCOME_UNKNOWN,
        )

    async def _snapshot_or_none(self) -> dict[str, Any] | None:
        try:
            return await self.inspect(self.run_id)
        except ToolUnavailable:
            return None

    async def _send(self, action: SimAction) -> ToolResult:
        payload = action.to_http_payload()
        try:
            async with self._client() as client:
                response = await client.post(
                    f"/internal/runs/{self.run_id}/actions", json=payload
                )
        except httpx.TimeoutException:
            return ToolResult(
                outcome=ToolOutcome.TIMEOUT,
                action_type=action.action_type,
                idempotency_key=action.idempotency_key,
                error_code="TIMEOUT",
            )
        except httpx.HTTPError as error:
            return ToolResult(
                outcome=ToolOutcome.UNKNOWN,
                action_type=action.action_type,
                idempotency_key=action.idempotency_key,
                error_code=type(error).__name__,
            )
        return self._interpret(response, action)

    @staticmethod
    def _interpret(response: httpx.Response, action: SimAction) -> ToolResult:
        if response.status_code == httpx.codes.OK:
            try:
                receipt = response.json()
            except ValueError:
                receipt = None
            if not isinstance(receipt, dict):
                return ToolResult(
                    outcome=ToolOutcome.UNKNOWN,
                    action_type=action.action_type,
                    idempotency_key=action.idempotency_key,
                    error_code="UNPARSEABLE_RECEIPT",
                )
            # The HTTP status says the request was *processed*; the receipt says what
            # happened. A business rejection arrives as a 200 with `status: REJECTED`,
            # and reading only the transport would report it to the agent as a success.
            return _from_receipt(receipt, action)
        code, message = _error_from(response)
        if response.status_code in {
            httpx.codes.BAD_GATEWAY,
            httpx.codes.SERVICE_UNAVAILABLE,
            httpx.codes.GATEWAY_TIMEOUT,
        }:
            # The service answered that it could not answer. From here that is
            # indistinguishable from a lost acknowledgement, so it is not a rejection.
            return ToolResult(
                outcome=ToolOutcome.TIMEOUT,
                action_type=action.action_type,
                idempotency_key=action.idempotency_key,
                error_code=code or str(response.status_code),
                error_message=message,
            )
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            return ToolResult(
                outcome=ToolOutcome.UNKNOWN,
                action_type=action.action_type,
                idempotency_key=action.idempotency_key,
                error_code=code or str(response.status_code),
                error_message=message,
            )
        return ToolResult(
            outcome=ToolOutcome.FAILED,
            action_type=action.action_type,
            idempotency_key=action.idempotency_key,
            error_code=code or str(response.status_code),
            error_message=message,
        )
