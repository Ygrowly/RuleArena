"""Recovery on the durable store: the deployment path (PostgreSQL) must resume
from checkpoints exactly like the in-memory store does."""

import json
import os
from typing import Any
from uuid import uuid4

import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackStatus,
    AttackWorker,
    Budget,
    CompileResult,
    CompileStatus,
    FakeLLMAdapter,
    FaultPoint,
    InjectedWorkerCrash,
    MinimizationResult,
    PostgresRuleVersionStore,
    PostgresRuntimeStore,
    ReplayResult,
    StrategyAgent,
    StrategyType,
)
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


def _action(action_type: str, **arguments: str) -> str:
    return json.dumps(
        {
            "proposal_type": "ACTION",
            "action_type": action_type,
            "arguments": arguments,
            "reason": "postgres recovery",
        }
    )


def _stop() -> str:
    return json.dumps({"proposal_type": "STOP", "reason": "done"})


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_worker_resumes_from_postgres_checkpoint_after_crash() -> None:
    database_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL after applying control migrations")
    versions = PostgresRuleVersionStore(database_url)
    runtime = PostgresRuntimeStore(database_url)
    compiled = CompileResult(
        status=CompileStatus.COMPILED,
        template_id="promotion",
        rule_spec=rule_spec(ScenarioType.PROMOTION),
    )
    policy_id = str(uuid4())
    versions.record_compile(policy_id, "postgres recovery", compiled)
    version = versions.confirm(policy_id, compiled)
    budget = Budget(max_steps=4, max_tokens=100, max_cost=1, max_time_seconds=10)
    run = runtime.create_run(
        job_key=f"pg-recovery:{uuid4()}",
        rule_version_id=version.version_id,
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=1,
    )

    def agents() -> dict[StrategyType, StrategyAgent]:
        return {
            strategy: StrategyAgent(
                strategy,
                FakeLLMAdapter(
                    [_action("CREATE_USER", initial_balance="500.00"), _stop()]
                    if strategy is StrategyType.VALUE_FLOW
                    else [_stop()]
                ),
            )
            for strategy in StrategyType
        }

    fired = False

    def crash_once(point: FaultPoint) -> None:
        nonlocal fired
        if point is FaultPoint.AFTER_CHECKPOINT and not fired:
            fired = True
            raise InjectedWorkerCrash("simulated process death on postgres path")

    crashing = AttackWorker(runtime, Replayless(), agents(), fault_injector=crash_once)
    with pytest.raises(InjectedWorkerCrash):
        await crashing.run(run.run_id, version.rule_spec)

    assert runtime.get_run(run.run_id).status in {
        AttackStatus.SEARCHING,
        AttackStatus.FAILED,
    }
    value_run = runtime.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, budget)
    checkpoint = runtime.load_checkpoint(value_run.strategy_run_id)
    assert checkpoint is not None and len(checkpoint.state["actions"]) == 1

    resumed = AttackWorker(runtime, Replayless(), agents())
    await resumed.run(run.run_id, version.rule_spec)
    completed = runtime.get_run(run.run_id)
    assert completed.status is AttackStatus.COMPLETED
    assert completed.outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET
    resumed_checkpoint = runtime.load_checkpoint(value_run.strategy_run_id)
    assert resumed_checkpoint is not None
    # Resume must continue from the durable checkpoint, not duplicate the step.
    assert len(resumed_checkpoint.state["actions"]) == 1
    runtime.close()
    versions.close()


class Replayless:
    """The scripted path never reaches the replay boundary."""

    async def replay(
        self, rule_spec: Any, actions: Any, target_invariant: Any, *, sandbox_version: str = "fixed"
    ) -> ReplayResult:
        raise AssertionError("replay must not run in this scenario")

    async def minimize(
        self, rule_spec: Any, actions: Any, target_invariant: Any, *, sandbox_version: str = "fixed"
    ) -> MinimizationResult:
        raise AssertionError("minimize must not run in this scenario")
