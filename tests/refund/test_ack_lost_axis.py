"""D1 acceptance: a refund that really happened can still come back as a timeout.

The axis is worth nothing unless the write landed first. Every check below is one half
of that claim, and they are checked against the real Sandbox rather than a mock:

1. the caller's refund call times out;
2. the same idempotency key resolves to a durable SUCCEEDED receipt;
3. the refund is in the authoritative snapshot.

(2) and (3) are the ones that fail if the signal is raised before the commit -- the
defect would then mean "the write never happened, retry it", which is a different and
much easier problem than the one this axis exists to reproduce.

The delay the sandbox holds the response open for is `SANDBOX_ACK_LOST_DELAY_SECONDS`
(12s by default), so the client below times out deliberately early instead of waiting.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

pytestmark = pytest.mark.sandbox

REFUND_KEY = "ack-lost-refund"
CLIENT_TIMEOUT = 3.0


async def _action(
    client: httpx.AsyncClient,
    run_id: str,
    action_name: str,
    key: str,
    *,
    target_id: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        f"/internal/runs/{run_id}/actions",
        json={
            "action": action_name,
            "actor_id": "user-1",
            "target_id": target_id,
            "arguments": arguments or {},
            "idempotency_key": key,
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def _paid_order(client: httpx.AsyncClient, *, axes: list[str]) -> str:
    created = await client.post(
        "/internal/runs",
        json={
            "scenario_type": "PROMOTION",
            "sandbox_version": "fixed",
            "defect_axes": axes,
        },
    )
    assert created.status_code == 201, created.text
    run_id = cast(str, created.json()["run_id"])
    await _action(
        client, run_id, "create_user", "ack-user", arguments={"initial_balance": "500.00"}
    )
    order = await _action(
        client, run_id, "create_order", "ack-order", target_id="user-1",
        arguments={"amount": "100.00"},
    )
    await _action(client, run_id, "pay_order", "ack-pay", target_id=order["result"]["order_id"])
    return run_id


@pytest.mark.asyncio
async def test_committed_refund_answers_with_a_timeout_and_leaves_a_receipt(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    headers = {"X-Internal-Service-Token": sandbox_token}
    async with httpx.AsyncClient(base_url=sandbox_http_url, headers=headers, timeout=10) as client:
        run_id = await _paid_order(client, axes=["REFUND_ACK_LOST"])

        with pytest.raises(httpx.TimeoutException):
            await client.post(
                f"/internal/runs/{run_id}/actions",
                json={
                    "action": "refund_order",
                    "actor_id": "user-1",
                    "target_id": "order-1",
                    "arguments": {"amount": "100.00"},
                    "idempotency_key": REFUND_KEY,
                },
                timeout=CLIENT_TIMEOUT,
            )

        receipt = await client.get(f"/internal/runs/{run_id}/receipts/{REFUND_KEY}")
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["status"] == "SUCCEEDED"

        snapshot = await client.get(f"/internal/runs/{run_id}/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        orders = snapshot.json()["state"]["orders"]
        assert [order["refunded_amount"] for order in orders] == ["100.00"]
        assert [order["status"] for order in orders] == ["REFUNDED"]


@pytest.mark.asyncio
async def test_a_faithful_run_answers_the_refund_normally(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    """The axis has to be the only difference, or the comparison measures nothing."""
    headers = {"X-Internal-Service-Token": sandbox_token}
    async with httpx.AsyncClient(base_url=sandbox_http_url, headers=headers, timeout=10) as client:
        run_id = await _paid_order(client, axes=[])
        receipt = await _action(
            client,
            run_id,
            "refund_order",
            REFUND_KEY,
            target_id="order-1",
            arguments={"amount": "100.00"},
        )
        assert receipt["status"] == "SUCCEEDED"
