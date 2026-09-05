import json
from typing import Any

import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackStatus,
    AttackWorker,
    Budget,
    FakeLLMAdapter,
    FaultPoint,
    InjectedWorkerCrash,
    InMemoryRuntimeStore,
    MinimizationResult,
    ReplayClassification,
    ReplayResult,
    StrategyAgent,
    StrategyType,
)
from rulearena_oracle import InvariantId, OracleFinding, OracleReport, OracleStatus
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


class ReplayMustNotRun:
    async def replay(self, *args: Any, **kwargs: Any) -> ReplayResult:
        raise AssertionError("a candidate was not produced")

    async def minimize(self, *args: Any, **kwargs: Any) -> MinimizationResult:
        raise AssertionError("a candidate was not produced")


class AlwaysViolationOracle:
    def evaluate(self, *args: object, **kwargs: object) -> OracleReport:
        return OracleReport(
            findings=(
                OracleFinding(
                    invariant_id=InvariantId.NET_PAID_NON_NEGATIVE,
                    status=OracleStatus.VIOLATED,
                    explanation="fault-injection candidate",
                ),
            )
        )


class ConfirmingReplay:
    def __init__(self) -> None:
        self.replay_calls = 0

    async def replay(
        self, rule_spec: Any, actions: Any, target_invariant: Any, *, sandbox_version: str = "fixed"
    ) -> ReplayResult:
        self.replay_calls += 1
        report = AlwaysViolationOracle().evaluate()
        return ReplayResult(
            classification=ReplayClassification.CONFIRMED_VIOLATION,
            target_invariant=target_invariant,
            run_id=f"sandbox-{self.replay_calls}",
            actions=tuple(actions),
            report=report,
            snapshots=(),
            receipts=(),
            events=(),
        )

    async def minimize(
        self, rule_spec: Any, actions: Any, target_invariant: Any, *, sandbox_version: str = "fixed"
    ) -> MinimizationResult:
        values: tuple[Any, ...] = tuple(actions)
        return MinimizationResult(
            invariant_id=target_invariant,
            original_length=len(values),
            minimized_actions=values,
            trials=1,
            one_minimal=True,
        )

def _action(action_type: str, **arguments: str) -> str:
    return json.dumps(
        {
            "proposal_type": "ACTION",
            "action_type": action_type,
            "arguments": arguments,
            "reason": "bounded search",
        }
    )


def _stop() -> str:
    return json.dumps({"proposal_type": "STOP", "reason": "done"})


@pytest.mark.asyncio
async def test_worker_recovers_when_crash_happens_before_checkpoint() -> None:
    store = InMemoryRuntimeStore()
    run = store.create_run(
        job_key="job-before-checkpoint",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=Budget(max_steps=4, max_tokens=100, max_cost=1, max_time_seconds=10),
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
        if point is FaultPoint.BEFORE_CHECKPOINT and not fired:
            fired = True
            raise InjectedWorkerCrash("simulated process death before checkpoint")

    crashing_worker = AttackWorker(
        store, ReplayMustNotRun(), agents(), fault_injector=crash_once
    )
    with pytest.raises(InjectedWorkerCrash):
        await crashing_worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))

    value_run = store.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, run.budget)
    assert store.load_checkpoint(value_run.strategy_run_id) is None

    resumed_worker = AttackWorker(store, ReplayMustNotRun(), agents())
    await resumed_worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))

    checkpoint = store.load_checkpoint(value_run.strategy_run_id)
    assert checkpoint is not None
    assert len(checkpoint.state["actions"]) == 1
    assert store.get_run(run.run_id).outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET


@pytest.mark.asyncio
async def test_worker_resumes_after_checkpoint_without_repeating_step() -> None:
    store = InMemoryRuntimeStore()
    run = store.create_run(
        job_key="job-recovery",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=Budget(max_steps=4, max_tokens=100, max_cost=1, max_time_seconds=10),
        random_seed=1,
    )
    agents = {
        StrategyType.VALUE_FLOW: StrategyAgent(
            StrategyType.VALUE_FLOW,
            FakeLLMAdapter([_action("CREATE_USER", initial_balance="500.00"), _stop()]),
        ),
        StrategyType.LIFECYCLE: StrategyAgent(
            StrategyType.LIFECYCLE, FakeLLMAdapter([_stop()])
        ),
        StrategyType.BOUNDARY: StrategyAgent(
            StrategyType.BOUNDARY, FakeLLMAdapter([_stop()])
        ),
    }
    fired = False

    def crash_once(point: FaultPoint) -> None:
        nonlocal fired
        if point is FaultPoint.AFTER_CHECKPOINT and not fired:
            fired = True
            raise InjectedWorkerCrash("simulated process death")

    worker = AttackWorker(store, ReplayMustNotRun(), agents, fault_injector=crash_once)
    with pytest.raises(InjectedWorkerCrash):
        await worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))

    value_run = store.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, run.budget)
    checkpoint = store.load_checkpoint(value_run.strategy_run_id)
    assert checkpoint is not None
    assert len(checkpoint.state["actions"]) == 1

    await worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))
    completed = store.get_run(run.run_id)
    assert completed.outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET
    resumed = store.load_checkpoint(value_run.strategy_run_id)
    assert resumed is not None
    assert len(resumed.state["actions"]) == 1

    await worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))
    assert store.get_run(run.run_id) == completed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault_point", "expected_replays"),
    [
        (FaultPoint.BEFORE_ORACLE_PERSIST, 2),
        (FaultPoint.AFTER_ORACLE_PERSIST, 1),
    ],
)
async def test_oracle_persistence_crashes_resume_without_duplicate_counterexample(
    fault_point: FaultPoint, expected_replays: int
) -> None:
    store = InMemoryRuntimeStore()
    run = store.create_run(
        job_key=f"job-{fault_point.value}",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10),
        random_seed=1,
    )
    agents = {
        strategy: StrategyAgent(
            strategy,
            FakeLLMAdapter(
                [_action("CREATE_USER", initial_balance="500.00")]
                if strategy is StrategyType.VALUE_FLOW
                else [_stop()]
            ),
        )
        for strategy in StrategyType
    }
    fired = False

    def crash_once(point: FaultPoint) -> None:
        nonlocal fired
        if point is fault_point and not fired:
            fired = True
            raise InjectedWorkerCrash(point.value)

    replay = ConfirmingReplay()
    worker = AttackWorker(store, replay, agents, fault_injector=crash_once)
    worker.oracle = AlwaysViolationOracle()  # type: ignore[assignment]
    with pytest.raises(InjectedWorkerCrash):
        await worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))
    await worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))

    assert len(store.counterexamples(run.run_id)) == 1
    assert replay.replay_calls == expected_replays
    assert store.get_run(run.run_id).outcome is AttackOutcome.CONFIRMED_VIOLATION


@pytest.mark.asyncio
async def test_failed_recovery_replays_durable_candidate_instead_of_dropping_it() -> None:
    store = InMemoryRuntimeStore()
    run = store.create_run(
        job_key="job-failed-candidate-recovery",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10),
        random_seed=1,
    )
    agents = {
        strategy: StrategyAgent(
            strategy,
            FakeLLMAdapter(
                [_action("CREATE_USER", initial_balance="500.00")]
                if strategy is StrategyType.VALUE_FLOW
                else [_stop()]
            ),
        )
        for strategy in StrategyType
    }
    fired = False

    def crash_once(point: FaultPoint) -> None:
        nonlocal fired
        if point is FaultPoint.BEFORE_ORACLE_PERSIST and not fired:
            fired = True
            raise InjectedWorkerCrash(point.value)

    replay = ConfirmingReplay()
    worker = AttackWorker(store, replay, agents, fault_injector=crash_once)
    worker.oracle = AlwaysViolationOracle()  # type: ignore[assignment]
    with pytest.raises(InjectedWorkerCrash):
        await worker.run(run.run_id, rule_spec(ScenarioType.PROMOTION))

    # Simulate a monitoring process marking the interrupted run as FAILED.
    assert store.compare_and_set_status(
        run.run_id,
        AttackStatus.REPLAYING,
        AttackStatus.FAILED,
        outcome=AttackOutcome.INFRA_FAILED,
    )
    value_run = store.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, run.budget)
    checkpoint = store.load_checkpoint(value_run.strategy_run_id)
    assert checkpoint is not None
    assert isinstance(checkpoint.state.get("candidate_invariant"), str)

    resumed = AttackWorker(store, replay, agents)
    resumed.oracle = AlwaysViolationOracle()  # type: ignore[assignment]
    await resumed.run(run.run_id, rule_spec(ScenarioType.PROMOTION))

    completed = store.get_run(run.run_id)
    assert completed.status is AttackStatus.COMPLETED
    assert completed.outcome is AttackOutcome.CONFIRMED_VIOLATION
    assert len(store.counterexamples(run.run_id)) == 1
    # One replay before the injected crash plus exactly one recovery replay.
    assert replay.replay_calls == 2
