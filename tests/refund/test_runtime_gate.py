"""D2: the three stages, each with a pass and a refusal.

Every check runs against a mocked sandbox that behaves like the real one -- including
raising a client-side timeout for a refund whose acknowledgement was lost -- so the
gate's own behaviour is what is under test, not an integration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from rulearena_domain_contracts import ActionType
from rulearena_reference_simulator import SimAction
from rulearena_runtime_gate import GateDecisionType, GateReason, RuntimeGate

RUN_ID = "run-1"


def snapshot(
    orders: list[dict[str, Any]], *, state_hash: str = "h0", version: int = 0
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "snapshot_version": version,
        "state_hash": state_hash,
        "state": {"users": [], "orders": orders},
        "captured_at": datetime.now(UTC).isoformat(),
    }


def order(
    order_id: str = "order-1",
    *,
    paid: str = "100.00",
    refunded: str = "0.00",
    status: str = "PAID",
) -> dict[str, Any]:
    return {
        "id": order_id,
        "user_id": "user-1",
        "paid_amount": paid,
        "refunded_amount": refunded,
        "status": status,
    }


def refund(order_id: str = "order-1", amount: str = "100.00") -> SimAction:
    return SimAction.build(
        ActionType.REFUND_ORDER,
        target_id=order_id,
        amount=amount,
        idempotency_key="refund-key",
    )


def gate(handler: Any) -> RuntimeGate:
    return RuntimeGate(
        "http://sandbox", "x" * 32, transport=httpx.MockTransport(handler)
    )


def serving(payload: dict[str, Any], *, status_code: int = 200) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return handler


# --- pre_check ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_check_allows_a_refund_within_the_paid_budget() -> None:
    result = await gate(serving(snapshot([order()]))).pre_check(RUN_ID, refund("order-1", "40.00"))
    assert result.decision is GateDecisionType.ALLOW
    assert result.reason_code is GateReason.WITHIN_REFUND_BUDGET


@pytest.mark.asyncio
async def test_pre_check_allows_a_refund_that_exactly_exhausts_the_budget() -> None:
    """Equality is not an over-refund: the sandbox allows it, so the gate must too."""
    result = await gate(serving(snapshot([order(refunded="60.00")]))).pre_check(
        RUN_ID, refund("order-1", "40.00")
    )
    assert result.decision is GateDecisionType.ALLOW


@pytest.mark.asyncio
async def test_pre_check_blocks_a_refund_that_would_exceed_what_was_paid() -> None:
    result = await gate(serving(snapshot([order(refunded="100.00")]))).pre_check(
        RUN_ID, refund("order-1", "100.00")
    )
    assert result.decision is GateDecisionType.BLOCK
    assert result.reason_code is GateReason.REFUND_BUDGET_EXCEEDED
    assert result.detail["projected_refunded_total"] == "200.00"


@pytest.mark.asyncio
async def test_pre_check_blocks_a_refund_on_an_order_that_is_no_longer_refundable() -> None:
    result = await gate(
        serving(snapshot([order(status="REFUNDED", refunded="100.00")]))
    ).pre_check(RUN_ID, refund("order-1", "10.00"))
    assert result.decision is GateDecisionType.BLOCK
    assert result.reason_code is GateReason.ORDER_NOT_REFUNDABLE


@pytest.mark.asyncio
async def test_pre_check_blocks_a_refund_on_an_order_the_run_does_not_have() -> None:
    result = await gate(serving(snapshot([]))).pre_check(RUN_ID, refund("order-9", "10.00"))
    assert result.decision is GateDecisionType.BLOCK
    assert result.reason_code is GateReason.ORDER_NOT_FOUND


@pytest.mark.asyncio
async def test_pre_check_is_unknown_when_the_snapshot_cannot_be_read() -> None:
    """Not a pass. An unreadable budget is exactly the state a gate must not wave through."""
    result = await gate(serving({"code": "SERVICE_STARTING"}, status_code=503)).pre_check(
        RUN_ID, refund()
    )
    assert result.decision is GateDecisionType.UNKNOWN
    assert result.reason_code is GateReason.SNAPSHOT_UNAVAILABLE


@pytest.mark.asyncio
async def test_pre_check_is_unknown_when_the_amount_is_not_a_decimal_string() -> None:
    result = await gate(serving(snapshot([order()]))).pre_check(
        RUN_ID, refund("order-1", "一百元")
    )
    assert result.decision is GateDecisionType.UNKNOWN
    assert result.reason_code is GateReason.REFUND_AMOUNT_UNREADABLE


@pytest.mark.asyncio
async def test_pre_check_rounds_the_amount_exactly_as_the_sandbox_will() -> None:
    """The sandbox quantizes to cents before comparing; a raw comparison would block a
    refund the sandbox is about to accept."""
    handler = serving(snapshot([order()]))
    accepted = await gate(handler).pre_check(RUN_ID, refund("order-1", "100.004"))
    assert accepted.decision is GateDecisionType.ALLOW
    blocked = await gate(handler).pre_check(RUN_ID, refund("order-1", "100.006"))
    assert blocked.decision is GateDecisionType.BLOCK


@pytest.mark.asyncio
async def test_pre_check_passes_through_actions_that_are_not_refunds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a non-refund action must not be read at all")

    result = await gate(handler).pre_check(
        RUN_ID, SimAction.build(ActionType.PAY_ORDER, target_id="order-1", idempotency_key="k")
    )
    assert result.decision is GateDecisionType.ALLOW
    assert result.reason_code is GateReason.NOT_A_REFUND_WRITE


# --- retry_check -------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_check_returns_the_durable_receipt_instead_of_replaying() -> None:
    receipt = {"receipt_id": "r-1", "status": "SUCCEEDED", "action_type": "REFUND_ORDER"}
    result = await gate(serving(receipt)).retry_check(RUN_ID, "refund-key")
    assert result.decision is GateDecisionType.ALLOW
    assert result.reason_code is GateReason.CACHED_RECEIPT
    assert result.cached_receipt == receipt


@pytest.mark.asyncio
async def test_retry_check_allows_a_retry_when_no_receipt_was_ever_written() -> None:
    missing = serving({"detail": {"code": "RECEIPT_NOT_FOUND"}}, status_code=404)
    result = await gate(missing).retry_check(RUN_ID, "refund-key")
    assert result.decision is GateDecisionType.ALLOW
    assert result.reason_code is GateReason.RETRY_SAFE
    assert result.cached_receipt is None


@pytest.mark.asyncio
async def test_retry_check_is_unknown_when_the_lookup_itself_fails() -> None:
    result = await gate(serving({"code": "BOOM"}, status_code=500)).retry_check(
        RUN_ID, "refund-key"
    )
    assert result.decision is GateDecisionType.UNKNOWN
    assert result.reason_code is GateReason.RECEIPT_LOOKUP_FAILED


@pytest.mark.asyncio
async def test_retry_check_is_unknown_when_the_transport_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    result = await gate(handler).retry_check(RUN_ID, "refund-key")
    assert result.decision is GateDecisionType.UNKNOWN
    assert result.reason_code is GateReason.RECEIPT_LOOKUP_FAILED
    assert result.detail["error"] == "ConnectError"


# --- post_check --------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_check_confirms_a_write_that_moved_the_authoritative_state() -> None:
    before = snapshot([order()], state_hash="h0", version=1)
    after = snapshot([order(refunded="100.00", status="REFUNDED")], state_hash="h1", version=2)
    result = await gate(serving(after)).post_check(RUN_ID, before, after)
    assert result.decision is GateDecisionType.ALLOW
    assert result.reason_code is GateReason.EFFECT_OBSERVED


@pytest.mark.asyncio
async def test_post_check_reports_a_write_that_left_no_trace() -> None:
    before = snapshot([order()], state_hash="h0", version=1)
    result = await gate(serving(before)).post_check(RUN_ID, before, before)
    assert result.decision is GateDecisionType.BLOCK
    assert result.reason_code is GateReason.EFFECT_NOT_OBSERVED


@pytest.mark.asyncio
async def test_post_check_is_unknown_when_the_caller_holds_a_view_the_authority_rejects() -> None:
    before = snapshot([order()], state_hash="h0", version=1)
    stale = snapshot([order(refunded="100.00")], state_hash="h-stale", version=2)
    authoritative = snapshot([order(refunded="40.00")], state_hash="h-real", version=3)
    result = await gate(serving(authoritative)).post_check(RUN_ID, before, stale)
    assert result.decision is GateDecisionType.UNKNOWN
    assert result.reason_code is GateReason.STALE_VIEW


@pytest.mark.asyncio
async def test_post_check_is_unknown_when_the_snapshot_cannot_be_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("gone", request=request)

    result = await gate(handler).post_check(RUN_ID, snapshot([order()]), snapshot([order()]))
    assert result.decision is GateDecisionType.UNKNOWN
    assert result.reason_code is GateReason.SNAPSHOT_UNAVAILABLE
