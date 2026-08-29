import asyncio
from typing import Any, cast

import httpx
import pytest


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_effect(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    headers = {"X-Internal-Service-Token": sandbox_token}
    async with httpx.AsyncClient(base_url=sandbox_http_url, headers=headers, timeout=10) as client:
        created = await client.post("/internal/runs", json={"scenario_type": "PROMOTION"})
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]

        async def write_once() -> dict[str, Any]:
            response = await client.post(
                f"/internal/runs/{run_id}/actions",
                json={
                    "action": "create_user",
                    "actor_id": "user-1",
                    "arguments": {"initial_balance": "10.00"},
                    "idempotency_key": "concurrency-smoke",
                },
            )
            assert response.status_code == 200, response.text
            return cast(dict[str, Any], response.json())

        receipts = await asyncio.gather(*(write_once() for _ in range(8)))
        assert len({receipt["receipt_id"] for receipt in receipts}) == 1
        events = await client.get(f"/internal/runs/{run_id}/events")
        assert len(events.json()["events"]) == 1
