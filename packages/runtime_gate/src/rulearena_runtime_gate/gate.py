from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict
from rulearena_domain_contracts import ActionType
from rulearena_reference_simulator import SimAction

__all__ = ["GateDecision", "GateDecisionType", "GateReason", "RuntimeGate"]


class StrictModel(BaseModel):
    """The forbid-extra, frozen base every model in this workspace is built on."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# The states the sandbox's own refund handler accepts. Kept here as a closed set rather
# than re-derived from a RuleSpec: the gate mirrors the *implementation's* arithmetic
# boundary, it does not interpret policy.
REFUNDABLE_ORDER_STATES = frozenset({"PAID", "PARTIALLY_REFUNDED"})

# The sandbox quantizes every monetary argument to two decimal places with half-up
# rounding before it compares anything. A gate that compared the raw decimal would
# disagree with it at the boundary -- `100.0000001` would be blocked here and accepted
# there -- so this mirrors the *arithmetic*, which is the one thing the gate is allowed
# to mirror. Note it mirrors the faithful implementation: under `REFUND_AGAINST_ORIGINAL`
# the sandbox would let a refund through that this check still refuses, and that is the
# right direction for a gate to be wrong in.
CENT = Decimal("0.01")


class GateDecisionType(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


class GateReason(StrEnum):
    """Why a stage reached its decision. Closed on purpose: a caller that must stop a
    branch has to be able to switch on the cause, and a free-text reason cannot be.

    The `UNKNOWN` reasons are the fail-closed ones -- an unreadable snapshot is not a
    pass, and the caller must escalate rather than proceed.
    """

    NOT_A_REFUND_WRITE = "NOT_A_REFUND_WRITE"
    WITHIN_REFUND_BUDGET = "WITHIN_REFUND_BUDGET"
    REFUND_BUDGET_EXCEEDED = "REFUND_BUDGET_EXCEEDED"
    ORDER_NOT_REFUNDABLE = "ORDER_NOT_REFUNDABLE"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    REFUND_AMOUNT_UNREADABLE = "REFUND_AMOUNT_UNREADABLE"
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    SNAPSHOT_UNREADABLE = "SNAPSHOT_UNREADABLE"
    CACHED_RECEIPT = "CACHED_RECEIPT"
    RETRY_SAFE = "RETRY_SAFE"
    RECEIPT_LOOKUP_FAILED = "RECEIPT_LOOKUP_FAILED"
    EFFECT_OBSERVED = "EFFECT_OBSERVED"
    EFFECT_NOT_OBSERVED = "EFFECT_NOT_OBSERVED"
    STALE_VIEW = "STALE_VIEW"


class GateDecision(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: GateDecisionType
    reason_code: GateReason
    detail: dict[str, Any] = {}
    cached_receipt: dict[str, Any] | None = None

    @property
    def allows(self) -> bool:
        return self.decision is GateDecisionType.ALLOW


def _decision(
    decision: GateDecisionType,
    reason_code: GateReason,
    **detail: Any,
) -> GateDecision:
    return GateDecision(decision=decision, reason_code=reason_code, detail=detail)


def _money(value: Any) -> Decimal | None:
    """Parse an action argument the way the sandbox's own money parser does.

    A float is refused rather than converted: the sandbox rejects it too, and a gate that
    accepted `0.1 + 0.2` semantics would be comparing a different number than the one the
    write will use.
    """
    raw = value
    if isinstance(value, Mapping):
        raw = value.get("amount")
    if isinstance(raw, float | bool) or not isinstance(raw, str | int | Decimal):
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite():
        return None
    quantized = parsed.quantize(CENT, rounding=ROUND_HALF_UP)
    return quantized if quantized > 0 else None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, float | bool) or not isinstance(value, str | int | Decimal):
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _orders(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]] | None:
    state = snapshot.get("state")
    if not isinstance(state, Mapping):
        return None
    orders = state.get("orders")
    if not isinstance(orders, list) or not all(isinstance(item, Mapping) for item in orders):
        return None
    keyed: dict[str, Mapping[str, Any]] = {}
    for order in orders:
        order_id = order.get("id")
        if isinstance(order_id, str):
            keyed[order_id] = order
    return keyed


def _state_hash(snapshot: Mapping[str, Any]) -> str | None:
    value = snapshot.get("state_hash")
    return value if isinstance(value, str) and value else None


class RuntimeGate:
    """Three deterministic checks over the Commerce Sandbox's own state and receipts.

    The gate has no database, no model, and no policy: every input it reads comes back
    over the same internal HTTP surface the caller could have used itself. What it adds
    is that the caller cannot skip it, and that an unanswerable question stops the
    branch instead of being rounded to a pass.
    """

    def __init__(
        self,
        sandbox_base_url: str,
        internal_token: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = sandbox_base_url.rstrip("/")
        self.headers = {"X-Internal-Service-Token": internal_token}
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        # `trust_env=False`: the gate must read the same ledger the caller writes to, and
        # an operator's proxy configuration must not be able to redirect one of the two.
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
            transport=self.transport,
            trust_env=False,
        )

    async def _snapshot(self, run_id: str) -> dict[str, Any] | None:
        try:
            async with self._client() as client:
                response = await client.get(f"/internal/runs/{run_id}/snapshot")
        except httpx.HTTPError:
            return None
        if response.status_code != httpx.codes.OK:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def pre_check(self, run_id: str, action: SimAction) -> GateDecision:
        """May this write go ahead?

        Answered from the run's authoritative snapshot: the order must exist, still be in
        a state its own refund handler accepts, and its cumulative refunds must stay
        within what was actually paid. This is the budget arithmetic behind
        `REFUND_NOT_EXCEED_PAID` -- and it is only that. Whether a refund was *wrong* is
        an Oracle finding, not a gate decision.
        """
        if action.action_type is not ActionType.REFUND_ORDER:
            return _decision(
                GateDecisionType.ALLOW,
                GateReason.NOT_A_REFUND_WRITE,
                action_type=action.action_type.value,
            )
        amount = _money(action.argument("amount"))
        order_id = action.target_id
        if amount is None or order_id is None:
            return _decision(
                GateDecisionType.UNKNOWN,
                GateReason.REFUND_AMOUNT_UNREADABLE,
                target_id=order_id,
                amount=action.argument("amount"),
            )
        snapshot = await self._snapshot(run_id)
        if snapshot is None:
            return _decision(
                GateDecisionType.UNKNOWN, GateReason.SNAPSHOT_UNAVAILABLE, run_id=run_id
            )
        orders = _orders(snapshot)
        if orders is None:
            return _decision(
                GateDecisionType.UNKNOWN, GateReason.SNAPSHOT_UNREADABLE, run_id=run_id
            )
        order = orders.get(order_id)
        if order is None:
            return _decision(
                GateDecisionType.BLOCK, GateReason.ORDER_NOT_FOUND, order_id=order_id
            )
        status = order.get("status")
        if status not in REFUNDABLE_ORDER_STATES:
            return _decision(
                GateDecisionType.BLOCK,
                GateReason.ORDER_NOT_REFUNDABLE,
                order_id=order_id,
                status=status,
            )
        paid = _decimal(order.get("paid_amount"))
        refunded = _decimal(order.get("refunded_amount"))
        if paid is None or refunded is None:
            return _decision(
                GateDecisionType.UNKNOWN,
                GateReason.SNAPSHOT_UNREADABLE,
                order_id=order_id,
                paid_amount=order.get("paid_amount"),
                refunded_amount=order.get("refunded_amount"),
            )
        total = refunded + amount
        detail = {
            "order_id": order_id,
            "paid_amount": str(paid),
            "refunded_amount": str(refunded),
            "requested_amount": str(amount),
            "projected_refunded_total": str(total),
        }
        if total > paid:
            return _decision(GateDecisionType.BLOCK, GateReason.REFUND_BUDGET_EXCEEDED, **detail)
        return _decision(GateDecisionType.ALLOW, GateReason.WITHIN_REFUND_BUDGET, **detail)

    async def retry_check(self, run_id: str, key: str) -> GateDecision:
        """Did an earlier attempt with this key already land?

        A hit returns the durable receipt and the caller must use it rather than replay
        the write. A miss means the key was never committed, so retrying it under the
        *same* key is safe. Anything else -- a 5xx, a transport failure, an unparseable
        body -- is a question that could not be answered, and stays `UNKNOWN`.
        """
        try:
            async with self._client() as client:
                response = await client.get(
                    f"/internal/runs/{run_id}/receipts/{quote(key, safe='')}"
                )
        except httpx.HTTPError as error:
            return _decision(
                GateDecisionType.UNKNOWN,
                GateReason.RECEIPT_LOOKUP_FAILED,
                idempotency_key=key,
                error=type(error).__name__,
            )
        if response.status_code == httpx.codes.NOT_FOUND:
            return _decision(GateDecisionType.ALLOW, GateReason.RETRY_SAFE, idempotency_key=key)
        if response.status_code != httpx.codes.OK:
            return _decision(
                GateDecisionType.UNKNOWN,
                GateReason.RECEIPT_LOOKUP_FAILED,
                idempotency_key=key,
                status_code=response.status_code,
            )
        try:
            receipt = response.json()
        except ValueError:
            receipt = None
        if not isinstance(receipt, dict):
            return _decision(
                GateDecisionType.UNKNOWN,
                GateReason.RECEIPT_LOOKUP_FAILED,
                idempotency_key=key,
                error="unparseable receipt body",
            )
        return GateDecision(
            decision=GateDecisionType.ALLOW,
            reason_code=GateReason.CACHED_RECEIPT,
            detail={"idempotency_key": key},
            cached_receipt=receipt,
        )

    async def post_check(
        self, run_id: str, before: dict[str, Any], after: dict[str, Any]
    ) -> GateDecision:
        """Did the write the caller just made actually land?

        `before` and `after` are the caller's own snapshots. The gate re-reads the run's
        snapshot itself: if the authority does not agree with `after`, the caller is
        holding a view the authority does not have and no deterministic conclusion is
        available (`STALE_VIEW`). If it does agree and nothing moved since `before`, the
        write left no trace (`EFFECT_NOT_OBSERVED`).

        A write replayed under an already-used key moves nothing on purpose; that case is
        answered by `retry_check`, which returns the receipt instead of replaying.
        """
        authoritative = await self._snapshot(run_id)
        if authoritative is None:
            return _decision(
                GateDecisionType.UNKNOWN, GateReason.SNAPSHOT_UNAVAILABLE, run_id=run_id
            )
        authoritative_hash, after_hash, before_hash = (
            _state_hash(authoritative),
            _state_hash(after),
            _state_hash(before),
        )
        if authoritative_hash is None or after_hash is None or before_hash is None:
            return _decision(
                GateDecisionType.UNKNOWN, GateReason.SNAPSHOT_UNREADABLE, run_id=run_id
            )
        if authoritative_hash != after_hash:
            return _decision(
                GateDecisionType.UNKNOWN,
                GateReason.STALE_VIEW,
                run_id=run_id,
                observed_state_hash=after_hash,
                authoritative_state_hash=authoritative_hash,
            )
        if authoritative_hash == before_hash:
            return _decision(
                GateDecisionType.BLOCK,
                GateReason.EFFECT_NOT_OBSERVED,
                run_id=run_id,
                state_hash=authoritative_hash,
            )
        return _decision(
            GateDecisionType.ALLOW,
            GateReason.EFFECT_OBSERVED,
            run_id=run_id,
            before_state_hash=before_hash,
            after_state_hash=authoritative_hash,
        )
