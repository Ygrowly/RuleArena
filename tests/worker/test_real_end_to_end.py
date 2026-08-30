import json

import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackWorker,
    Budget,
    CompileStatus,
    FakeLLMAdapter,
    InMemoryRuntimeStore,
    RuleCompiler,
    RuleVersionStore,
    SandboxReplayRunner,
    StrategyAgent,
    StrategyType,
)
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


def _proposal(
    proposal_type: str,
    *,
    action_type: str | None = None,
    target_id: str | None = None,
    arguments: dict[str, object] | None = None,
    candidate_invariant: str | None = None,
) -> str:
    value: dict[str, object] = {"proposal_type": proposal_type, "reason": "e2e fake model"}
    if action_type:
        value["action_type"] = action_type
        value["arguments"] = arguments or {}
    if target_id:
        value["target_id"] = target_id
    if candidate_invariant:
        value["candidate_invariant"] = candidate_invariant
    return json.dumps(value)


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_chinese_rule_to_real_confirmed_minimal_counterexample(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    spec = rule_spec(ScenarioType.REFUND_POINTS)
    compiled = await RuleCompiler(FakeLLMAdapter([spec.model_dump_json()])).compile(
        "refund-points", "每消费 1 元获得 1 积分，退款时按退款金额撤销积分。"
    )
    assert compiled.status is CompileStatus.COMPILED
    version = RuleVersionStore().confirm("policy-e2e", compiled)
    store = InMemoryRuntimeStore()
    run = store.create_run(
        job_key="worker-e2e",
        rule_version_id=version.version_id,
        scenario_version_id="refund-points-v1",
        sandbox_version="vulnerable",
        oracle_version="1.0",
        budget=Budget(max_steps=8, max_tokens=1000, max_cost=1, max_time_seconds=30),
        random_seed=20260830,
    )
    value_flow = [
        _proposal(
            "ACTION", action_type="CREATE_USER", arguments={"initial_balance": "500.00"}
        ),
        _proposal(
            "ACTION",
            action_type="CREATE_ORDER",
            target_id="user-1",
            arguments={"amount": "100.00"},
        ),
        _proposal("ACTION", action_type="PAY_ORDER", target_id="order-1"),
        _proposal(
            "ACTION",
            action_type="REFUND_ORDER",
            target_id="order-1",
            arguments={"amount": "50.00"},
        ),
        _proposal(
            "STOP",
            candidate_invariant="POINTS_VALUE_CONSERVATION",
        ),
    ]
    stop = [_proposal("STOP")]
    agents = {
        StrategyType.VALUE_FLOW: StrategyAgent(
            StrategyType.VALUE_FLOW, FakeLLMAdapter(value_flow)
        ),
        StrategyType.LIFECYCLE: StrategyAgent(
            StrategyType.LIFECYCLE, FakeLLMAdapter(stop.copy())
        ),
        StrategyType.BOUNDARY: StrategyAgent(
            StrategyType.BOUNDARY, FakeLLMAdapter(stop.copy())
        ),
    }
    await AttackWorker(
        store,
        SandboxReplayRunner(sandbox_http_url, sandbox_token),
        agents,
    ).run(run.run_id, version.rule_spec)

    completed = store.get_run(run.run_id)
    assert completed.outcome is AttackOutcome.CONFIRMED_VIOLATION
    counterexamples = store.counterexamples(run.run_id)
    assert len(counterexamples) == 1
    assert counterexamples[0].invariant_id == "POINTS_VALUE_CONSERVATION"
    assert len(counterexamples[0].original_actions) == 4
    assert len(counterexamples[0].minimized_actions) == 4
