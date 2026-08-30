import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest


@pytest.fixture
async def client(sandbox_http_url: str, sandbox_token: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=sandbox_http_url,
        headers={"X-Internal-Service-Token": sandbox_token},
        timeout=10,
    ) as http:
        yield http


async def create_run(
    client: httpx.AsyncClient,
    scenario_type: str,
    sandbox_version: str = "fixed",
) -> dict[str, Any]:
    response = await client.post(
        "/internal/runs",
        json={"scenario_type": scenario_type, "sandbox_version": sandbox_version},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "sandbox_version" not in body
    return cast(dict[str, Any], body)


async def action(
    client: httpx.AsyncClient,
    run_id: str,
    action_name: str,
    key: str,
    *,
    actor_id: str = "user-1",
    target_id: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        f"/internal/runs/{run_id}/actions",
        json={
            "action": action_name,
            "actor_id": actor_id,
            "target_id": target_id,
            "arguments": arguments or {},
            "idempotency_key": key,
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_promotion_fixed_lifecycle_events_and_reset(
    client: httpx.AsyncClient,
) -> None:
    run = await create_run(client, "PROMOTION")
    run_id = run["run_id"]
    initial_hash = run["snapshot"]["state_hash"]

    user = await action(
        client, run_id, "create_user", "create-user", arguments={"initial_balance": "200.00"}
    )
    assert user["status"] == "SUCCEEDED"
    coupon = await action(
        client,
        run_id,
        "issue_coupon",
        "issue-coupon",
        target_id="user-1",
        arguments={"value": "50.00", "threshold": "100.00"},
    )
    coupon_id = coupon["result"]["coupon_id"]
    order = await action(
        client,
        run_id,
        "create_order",
        "create-order",
        target_id="user-1",
        arguments={"amount": "150.00"},
    )
    order_id = order["result"]["order_id"]
    await action(
        client,
        run_id,
        "apply_coupon",
        "apply-coupon",
        target_id=order_id,
        arguments={"coupon_id": coupon_id},
    )
    paid = await action(client, run_id, "pay_order", "pay-order", target_id=order_id)
    assert paid["result"]["paid_amount"] == "100.00"
    refunded = await action(
        client,
        run_id,
        "refund_order",
        "refund-order",
        target_id=order_id,
        arguments={"amount": "100.00"},
    )
    assert refunded["status"] == "SUCCEEDED"

    events = (await client.get(f"/internal/runs/{run_id}/events")).json()["events"]
    assert [event["event_type"] for event in events] == [
        "USER_CREATED",
        "COUPON_ISSUED",
        "ORDER_CREATED",
        "COUPON_RESERVED",
        "COUPON_USED",
        "PAYMENT_CAPTURED",
        "REFUND_ISSUED",
    ]
    assert all("sandbox_version" not in event and "ground_truth" not in event for event in events)

    snapshot = (await client.get(f"/internal/runs/{run_id}/snapshot")).json()
    assert snapshot["state"]["orders"][0]["refunded_amount"] == "100.00"
    assert "created_at" not in snapshot["state"]
    reset = await client.post(f"/internal/runs/{run_id}/reset")
    assert reset.status_code == 200
    assert reset.json()["state_hash"] == initial_hash
    assert (await client.get(f"/internal/runs/{run_id}/events")).json()["events"] == []


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_idempotency_is_one_receipt_and_one_effect(client: httpx.AsyncClient) -> None:
    run_id = (await create_run(client, "REFUND_POINTS"))["run_id"]
    first = await action(
        client, run_id, "create_user", "same-key", arguments={"initial_balance": "100.00"}
    )
    second = await action(
        client, run_id, "create_user", "same-key", arguments={"initial_balance": "999.00"}
    )
    assert second["receipt_id"] == first["receipt_id"]
    state = (await client.get(f"/internal/runs/{run_id}/snapshot")).json()["state"]
    assert state["users"][0]["balance"] == "100.00"
    assert len((await client.get(f"/internal/runs/{run_id}/events")).json()["events"]) == 1


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_invalid_transition_is_rejected_without_event(client: httpx.AsyncClient) -> None:
    run_id = (await create_run(client, "PROMOTION"))["run_id"]
    rejected = await action(client, run_id, "pay_order", "invalid-pay", target_id="order-1")
    assert rejected["status"] == "REJECTED"
    assert rejected["error"]["code"] == "ORDER_NOT_FOUND"
    assert (await client.get(f"/internal/runs/{run_id}/events")).json()["events"] == []


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_cross_scenario_action_is_rejected_without_state_change(
    client: httpx.AsyncClient,
) -> None:
    run_id = (await create_run(client, "PROMOTION"))["run_id"]
    before = (await client.get(f"/internal/runs/{run_id}/snapshot")).json()
    rejected = await action(
        client,
        run_id,
        "activate_membership",
        "wrong-scenario",
        target_id="user-1",
        arguments={"paid_amount": "50.00", "quantity": 2},
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["error"]["code"] == "ACTION_NOT_SUPPORTED"
    after = (await client.get(f"/internal/runs/{run_id}/snapshot")).json()
    assert after["state"] == before["state"]
    assert (await client.get(f"/internal/runs/{run_id}/events")).json()["events"] == []


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_run_spaces_are_isolated(client: httpx.AsyncClient) -> None:
    first = (await create_run(client, "PROMOTION"))["run_id"]
    second = (await create_run(client, "PROMOTION"))["run_id"]
    await action(client, first, "create_user", "first-user", arguments={"initial_balance": "10.00"})
    first_state = (await client.get(f"/internal/runs/{first}/snapshot")).json()["state"]
    second_state = (await client.get(f"/internal/runs/{second}/snapshot")).json()["state"]
    assert len(first_state["users"]) == 1
    assert second_state["users"] == []


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_fixed_points_and_membership_consistency(client: httpx.AsyncClient) -> None:
    points_run = (await create_run(client, "REFUND_POINTS"))["run_id"]
    await action(client, points_run, "create_user", "u", arguments={"initial_balance": "200.00"})
    order = await action(
        client,
        points_run,
        "create_order",
        "o",
        target_id="user-1",
        arguments={"amount": "100.00"},
    )
    order_id = order["result"]["order_id"]
    await action(client, points_run, "pay_order", "p", target_id=order_id)
    await action(
        client,
        points_run,
        "refund_order",
        "r",
        target_id=order_id,
        arguments={"amount": "100.00"},
    )
    points_state = (await client.get(f"/internal/runs/{points_run}/snapshot")).json()["state"]
    assert points_state["users"][0]["points_balance"] == 0

    membership_run = (await create_run(client, "MEMBERSHIP_ENTITLEMENT"))["run_id"]
    await action(
        client, membership_run, "create_user", "mu", arguments={"initial_balance": "100.00"}
    )
    await action(
        client,
        membership_run,
        "activate_membership",
        "membership",
        target_id="user-1",
        arguments={"paid_amount": "50.00", "quantity": 2},
    )
    membership = await action(
        client,
        membership_run,
        "consume_entitlement",
        "consume",
        target_id="entitlement-1",
        arguments={"quantity": 1},
    )
    assert membership["status"] == "SUCCEEDED"
    cancelled = await action(
        client,
        membership_run,
        "cancel_membership",
        "cancel",
        target_id="membership-1",
        arguments={"refund_requested": True},
    )
    assert cancelled["status"] == "REJECTED"
    assert cancelled["error"]["code"] == "ENTITLEMENT_CONSUMED"


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_fixed_refund_does_not_make_redeemed_points_negative(
    client: httpx.AsyncClient,
) -> None:
    run_id = (await create_run(client, "REFUND_POINTS"))["run_id"]
    await action(
        client, run_id, "create_user", "redeem-user", arguments={"initial_balance": "200.00"}
    )
    order = await action(
        client,
        run_id,
        "create_order",
        "redeem-order",
        target_id="user-1",
        arguments={"amount": "100.00"},
    )
    order_id = order["result"]["order_id"]
    await action(client, run_id, "pay_order", "redeem-pay", target_id=order_id)
    await action(
        client,
        run_id,
        "redeem_points",
        "redeem-points",
        target_id="user-1",
        arguments={"amount": 100},
    )
    rejected = await action(
        client,
        run_id,
        "refund_order",
        "redeem-refund",
        target_id=order_id,
        arguments={"amount": "100.00"},
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["error"]["code"] == "POINTS_ALREADY_REDEEMED"
    state = (await client.get(f"/internal/runs/{run_id}/snapshot")).json()["state"]
    assert state["users"][0]["points_balance"] == 0
    assert state["orders"][0]["refunded_amount"] == "0.00"


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_same_key_concurrent_requests_return_one_receipt(client: httpx.AsyncClient) -> None:
    run_id = (await create_run(client, "PROMOTION"))["run_id"]
    requests = [
        action(
            client,
            run_id,
            "create_user",
            "concurrent-user",
            arguments={"initial_balance": "100.00"},
        )
        for _ in range(8)
    ]
    receipts = await asyncio.gather(*requests)
    assert len({receipt["receipt_id"] for receipt in receipts}) == 1
    assert len((await client.get(f"/internal/runs/{run_id}/events")).json()["events"]) == 1


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_internal_boundary_rejects_missing_token_unknown_action_and_large_body(
    sandbox_http_url: str,
    client: httpx.AsyncClient,
) -> None:
    async with httpx.AsyncClient(base_url=sandbox_http_url, timeout=10) as unauthenticated:
        missing_token = await unauthenticated.post(
            "/internal/runs", json={"scenario_type": "PROMOTION"}
        )
    assert missing_token.status_code == 401
    assert missing_token.headers.get("x-request-id")

    unknown_action = await client.post(
        "/internal/runs/not-a-run/actions",
        json={"action": "not_an_action", "actor_id": "user-1", "idempotency_key": "bad"},
    )
    assert unknown_action.status_code == 422

    oversized = await client.post(
        "/internal/runs",
        content=(b"{" + b'"scenario_type":"PROMOTION","padding":"' + b"x" * (256 * 1024) + b'"}'),
        headers={"content-type": "application/json", "x-request-id": "oversized-test"},
    )
    assert oversized.status_code == 413
    assert oversized.headers["x-request-id"] == "oversized-test"

    async def chunked_body() -> AsyncIterator[bytes]:
        for _ in range(257):
            yield b"x" * 1024

    chunked = await client.post(
        "/internal/runs",
        content=chunked_body(),
        headers={"content-type": "application/json", "x-request-id": "chunked-test"},
    )
    assert chunked.status_code == 413
    assert chunked.headers["x-request-id"] == "chunked-test"


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_rejected_payment_rolls_back_state_and_is_queryable(
    client: httpx.AsyncClient,
) -> None:
    run_id = (await create_run(client, "PROMOTION"))["run_id"]
    await action(
        client, run_id, "create_user", "rollback-user", arguments={"initial_balance": "10.00"}
    )
    order = await action(
        client,
        run_id,
        "create_order",
        "rollback-order",
        target_id="user-1",
        arguments={"amount": "20.00"},
    )
    order_id = order["result"]["order_id"]
    before = (await client.get(f"/internal/runs/{run_id}/snapshot")).json()
    rejected = await action(client, run_id, "pay_order", "rollback-pay", target_id=order_id)
    assert rejected["status"] == "REJECTED"
    assert rejected["error"]["code"] == "INSUFFICIENT_BALANCE"
    after = (await client.get(f"/internal/runs/{run_id}/snapshot")).json()
    assert after["state"] == before["state"]
    receipt = (await client.get(f"/internal/runs/{run_id}/receipts/rollback-pay")).json()
    assert receipt["receipt_id"] == rejected["receipt_id"]
    assert len((await client.get(f"/internal/runs/{run_id}/events")).json()["events"]) == 2


async def _promotion_coupon_run(
    client: httpx.AsyncClient, sandbox_version: str
) -> tuple[str, str, str]:
    run_id = (await create_run(client, "PROMOTION", sandbox_version))["run_id"]
    await action(
        client,
        run_id,
        "create_user",
        f"{sandbox_version}-user",
        arguments={"initial_balance": "500.00"},
    )
    coupon = await action(
        client,
        run_id,
        "issue_coupon",
        f"{sandbox_version}-coupon",
        target_id="user-1",
        arguments={"value": "50.00", "threshold": "100.00"},
    )
    order = await action(
        client,
        run_id,
        "create_order",
        f"{sandbox_version}-order",
        target_id="user-1",
        arguments={"amount": "150.00"},
    )
    order_id = order["result"]["order_id"]
    coupon_id = coupon["result"]["coupon_id"]
    await action(
        client,
        run_id,
        "apply_coupon",
        f"{sandbox_version}-apply",
        target_id=order_id,
        arguments={"coupon_id": coupon_id},
    )
    await action(client, run_id, "pay_order", f"{sandbox_version}-pay", target_id=order_id)
    return run_id, order_id, coupon_id


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_vulnerable_promotion_reproduces_refund_and_coupon_defects(
    client: httpx.AsyncClient,
) -> None:
    vulnerable_run, order_id, coupon_id = await _promotion_coupon_run(client, "vulnerable")
    first = await action(
        client,
        vulnerable_run,
        "refund_order",
        "vulnerable-refund-1",
        target_id=order_id,
        arguments={"amount": "60.00"},
    )
    second = await action(
        client,
        vulnerable_run,
        "refund_order",
        "vulnerable-refund-2",
        target_id=order_id,
        arguments={"amount": "100.00"},
    )
    assert first["status"] == second["status"] == "SUCCEEDED"
    assert second["result"]["order_refunded_total"] == "160.00"
    new_order = await action(
        client,
        vulnerable_run,
        "create_order",
        "vulnerable-order-2",
        target_id="user-1",
        arguments={"amount": "150.00"},
    )
    reused = await action(
        client,
        vulnerable_run,
        "apply_coupon",
        "vulnerable-reuse",
        target_id=new_order["result"]["order_id"],
        arguments={"coupon_id": coupon_id},
    )
    assert reused["status"] == "SUCCEEDED"


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_fixed_promotion_rejects_over_refund_and_coupon_reuse(
    client: httpx.AsyncClient,
) -> None:
    fixed_run, order_id, coupon_id = await _promotion_coupon_run(client, "fixed")
    await action(
        client,
        fixed_run,
        "refund_order",
        "fixed-refund-1",
        target_id=order_id,
        arguments={"amount": "60.00"},
    )
    rejected = await action(
        client,
        fixed_run,
        "refund_order",
        "fixed-refund-2",
        target_id=order_id,
        arguments={"amount": "100.00"},
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["error"]["code"] == "REFUND_EXCEEDS_PAID"
    new_order = await action(
        client,
        fixed_run,
        "create_order",
        "fixed-order-2",
        target_id="user-1",
        arguments={"amount": "150.00"},
    )
    reused = await action(
        client,
        fixed_run,
        "apply_coupon",
        "fixed-reuse",
        target_id=new_order["result"]["order_id"],
        arguments={"coupon_id": coupon_id},
    )
    assert reused["status"] == "REJECTED"
    assert reused["error"]["code"] == "COUPON_NOT_AVAILABLE"


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_vulnerable_points_and_membership_profiles_are_reproducible(
    client: httpx.AsyncClient,
) -> None:
    points_run = (await create_run(client, "REFUND_POINTS", "vulnerable"))["run_id"]
    await action(
        client, points_run, "create_user", "vp-user", arguments={"initial_balance": "200.00"}
    )
    order = await action(
        client,
        points_run,
        "create_order",
        "vp-order",
        target_id="user-1",
        arguments={"amount": "100.00"},
    )
    order_id = order["result"]["order_id"]
    await action(client, points_run, "pay_order", "vp-pay", target_id=order_id)
    await action(
        client,
        points_run,
        "refund_order",
        "vp-refund",
        target_id=order_id,
        arguments={"amount": "50.00"},
    )
    points_state = (await client.get(f"/internal/runs/{points_run}/snapshot")).json()["state"]
    assert points_state["users"][0]["points_balance"] == 150
    overredeemed = await action(
        client,
        points_run,
        "redeem_points",
        "vp-overredeem",
        target_id="user-1",
        arguments={"amount": 151},
    )
    assert overredeemed["status"] == "SUCCEEDED"
    negative_points = (await client.get(f"/internal/runs/{points_run}/snapshot")).json()["state"]
    assert negative_points["users"][0]["points_balance"] == -1

    membership_run = (await create_run(client, "MEMBERSHIP_ENTITLEMENT", "vulnerable"))["run_id"]
    await action(
        client, membership_run, "create_user", "vm-user", arguments={"initial_balance": "100.00"}
    )
    activated = await action(
        client,
        membership_run,
        "activate_membership",
        "vm-activate",
        target_id="user-1",
        arguments={"paid_amount": "50.00", "quantity": 2},
    )
    entitlement_id = activated["result"]["entitlement_id"]
    membership_id = activated["result"]["membership_id"]
    await action(
        client,
        membership_run,
        "consume_entitlement",
        "vm-consume",
        target_id=entitlement_id,
        arguments={"quantity": 1},
    )
    cancelled = await action(
        client,
        membership_run,
        "cancel_membership",
        "vm-cancel",
        target_id=membership_id,
        arguments={"refund_requested": True},
    )
    assert cancelled["status"] == "SUCCEEDED"
    still_usable = await action(
        client,
        membership_run,
        "consume_entitlement",
        "vm-consume-after-refund",
        target_id=entitlement_id,
        arguments={"quantity": 1},
    )
    assert still_usable["status"] == "SUCCEEDED"


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_vulnerable_membership_defect_is_scoped_to_refunds(
    client: httpx.AsyncClient,
) -> None:
    run_id = (await create_run(client, "MEMBERSHIP_ENTITLEMENT", "vulnerable"))["run_id"]
    await action(client, run_id, "create_user", "user", arguments={"initial_balance": "100.00"})
    activated = await action(
        client,
        run_id,
        "activate_membership",
        "activate",
        target_id="user-1",
        arguments={"paid_amount": "50.00", "quantity": 2},
    )
    cancelled = await action(
        client,
        run_id,
        "cancel_membership",
        "cancel-without-refund",
        target_id=activated["result"]["membership_id"],
        arguments={"refund_requested": False},
    )
    assert cancelled["status"] == "SUCCEEDED"
    consume = await action(
        client,
        run_id,
        "consume_entitlement",
        "consume-after-cancel",
        target_id=activated["result"]["entitlement_id"],
        arguments={"quantity": 1},
    )
    assert consume["status"] == "REJECTED"
    assert consume["error"]["code"] == "ENTITLEMENT_NOT_AVAILABLE"


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_fixed_points_profile_rejects_overredemption(client: httpx.AsyncClient) -> None:
    run_id = (await create_run(client, "REFUND_POINTS", "fixed"))["run_id"]
    await action(client, run_id, "create_user", "user", arguments={"initial_balance": "10.00"})
    rejected = await action(
        client,
        run_id,
        "redeem_points",
        "overredeem",
        target_id="user-1",
        arguments={"amount": 1},
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["error"]["code"] == "INSUFFICIENT_POINTS"
