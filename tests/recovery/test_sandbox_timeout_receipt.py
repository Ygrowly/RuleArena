from datetime import UTC, datetime

import httpx
import pytest
from rulearena_attack_runtime import ActionUnknownError, SandboxReplayRunner
from rulearena_domain_contracts import ActionType
from rulearena_oracle import InvariantId
from rulearena_policy_schema import ScenarioType
from rulearena_reference_simulator import ReferenceSimulator, SimAction

from tests.phase2_factories import rule_spec


@pytest.mark.asyncio
async def test_timed_out_sandbox_write_queries_authoritative_receipt() -> None:
    spec = rule_spec(ScenarioType.PROMOTION)
    simulator = ReferenceSimulator(spec)
    initial = simulator.initial_state()
    action = SimAction.build(ActionType.CREATE_USER, initial_balance="500.00")
    applied = simulator.transition(initial, action).state
    action_calls = 0
    receipt_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal action_calls, receipt_calls
        if request.method == "POST" and request.url.path == "/internal/runs":
            return httpx.Response(
                200,
                json={
                    "run_id": "sandbox-1",
                    "snapshot": {"state": initial.normalized(), "state_hash": initial.state_hash()},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/actions"):
            action_calls += 1
            raise httpx.ReadTimeout("response lost", request=request)
        if request.method == "GET" and "/receipts/" in request.url.path:
            receipt_calls += 1
            return httpx.Response(
                200,
                json={
                    "receipt_id": "receipt-1",
                    "status": "SUCCEEDED",
                    "result": {"user_id": "user-1"},
                    "occurred_at": datetime.now(UTC).isoformat(),
                },
            )
        if request.method == "GET" and request.url.path.endswith("/snapshot"):
            return httpx.Response(
                200, json={"state": applied.normalized(), "state_hash": applied.state_hash()}
            )
        if request.method == "GET" and request.url.path.endswith("/events"):
            return httpx.Response(200, json={"events": []})
        raise AssertionError(request.url)

    runner = SandboxReplayRunner(
        "http://sandbox",
        "x" * 32,
        transport=httpx.MockTransport(handler),
    )
    await runner.replay(spec, [action], InvariantId.NET_PAID_NON_NEGATIVE)
    assert action_calls == 1
    assert receipt_calls == 1


@pytest.mark.asyncio
async def test_timed_out_write_without_receipt_is_action_unknown() -> None:
    spec = rule_spec(ScenarioType.PROMOTION)
    initial = ReferenceSimulator(spec).initial_state()
    action = SimAction.build(ActionType.CREATE_USER, initial_balance="500.00")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/internal/runs":
            return httpx.Response(
                200,
                json={
                    "run_id": "sandbox-1",
                    "snapshot": {"state": initial.normalized(), "state_hash": initial.state_hash()},
                },
            )
        if request.method == "POST":
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(404)

    runner = SandboxReplayRunner(
        "http://sandbox", "x" * 32, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ActionUnknownError, match="ACTION_UNKNOWN"):
        await runner.replay(spec, [action], InvariantId.NET_PAID_NON_NEGATIVE)
