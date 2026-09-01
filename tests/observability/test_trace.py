import json

import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackWorker,
    Budget,
    FakeLLMAdapter,
    InMemoryRuntimeStore,
    StrategyAgent,
    StrategyType,
)
from rulearena_evaluation import public_metric_summary
from rulearena_observability import InMemoryTraceStore, TraceKind, TraceRecord, trace_payload
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


class NoReplay:
    async def replay(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("no candidate expected")

    async def minimize(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("no candidate expected")


def _action() -> str:
    return json.dumps(
        {
            "proposal_type": "ACTION",
            "action_type": "CREATE_USER",
            "arguments": {"initial_balance": "500.00"},
            "reason": "start",
        }
    )


def _stop() -> str:
    return json.dumps({"proposal_type": "STOP", "reason": "done"})


@pytest.mark.asyncio
async def test_worker_trace_is_linked_redacted_and_reproducible() -> None:
    runtime = InMemoryRuntimeStore()
    trace = InMemoryTraceStore()
    budget = Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10)
    run = runtime.create_run(
        job_key="trace-run",
        rule_version_id="00000000-0000-0000-0000-000000000101",
        scenario_version_id="promotion-v1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=1,
    )
    agents = {
        strategy: StrategyAgent(
            strategy,
            FakeLLMAdapter([_action(), _stop()])
            if strategy is StrategyType.VALUE_FLOW
            else FakeLLMAdapter([_stop()]),
        )
        for strategy in StrategyType
    }
    await AttackWorker(
        runtime, NoReplay(), agents, trace_sink=trace  # type: ignore[arg-type]
    ).run(run.run_id, rule_spec(ScenarioType.PROMOTION))
    records = trace.traces_for_run(run.run_id)
    assert {item.kind for item in records} == {
        TraceKind.LLM_CALL,
        TraceKind.ACTION_PROPOSAL,
        TraceKind.SIMULATION,
    }
    llm, proposal, simulation = records[:3]
    assert proposal.parent_trace_id == llm.trace_id
    assert simulation.parent_trace_id == proposal.trace_id
    assert simulation.before_state_hash != simulation.after_state_hash
    assert runtime.get_run(run.run_id).outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET
    exported = "".join(trace_payload(item).casefold() for item in records)
    assert "500.00" not in exported
    assert "ground_truth" not in exported


def test_trace_and_public_metrics_fail_closed_on_sensitive_detail() -> None:
    with pytest.raises(ValueError, match="sensitive trace field"):
        TraceRecord(
            run_id="run",
            step_id=1,
            kind=TraceKind.LLM_CALL,
            rule_version_id="rule",
            tool_result_summary={"api_key": "secret"},
            status="OK",
        )
    public = public_metric_summary(
        {
            "vulnerability_discovery_rate": {
                "value": 0.8,
                "source_run_ids": ["run-1"],
            },
            "discovered_case_ids": ["hidden-01"],
        }
    )
    assert public == {"vulnerability_discovery_rate": {"value": 0.8}}
