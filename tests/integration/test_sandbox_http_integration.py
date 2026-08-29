from typing import Any

import httpx
import pytest


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_sandbox_http_contract(sandbox_http_url: str, sandbox_token: str) -> None:
    """Keep the documented integration-test entry point as a small real HTTP smoke test."""

    headers = {"X-Internal-Service-Token": sandbox_token}
    async with httpx.AsyncClient(base_url=sandbox_http_url, headers=headers, timeout=10) as client:
        response = await client.post(
            "/internal/runs",
            json={"scenario_type": "PROMOTION", "sandbox_profile": "fixed"},
        )
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        assert body["scenario_type"] == "PROMOTION"
        assert body["snapshot"]["state"]["users"] == []
        assert "sandbox_version" not in body
