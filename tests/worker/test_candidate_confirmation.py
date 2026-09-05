import json
from typing import Any

import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackWorker,
    Budget,
    FakeLLMAdapter,
    InMemoryRuntimeStore,
    MinimizationResult,
    ReplayClassification,
    ReplayResult,
    StrategyAgent,
    StrategyType,
)
from rulearena_oracle import OracleFinding, OracleReport, OracleStatus
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


def _response(payload: dict[str, object]) -> str:
    return json.dumps(payload)


class ClassifiedReplay:
    def __init__(self, classification: ReplayClassification) -> None:
        self.classification = classification
        self.calls = 0

    async def replay(
        self, rule_spec: Any, actions: Any, target_invariant: Any, *, sandbox_version: str = "fixed"
    ) -> ReplayResult:
        self.calls += 1
        status = (
            OracleStatus.VIOLATED
            if self.classification is ReplayClassification.CONFIRMED_VIOLATION
            else OracleStatus.SATISFIED
        )
        report = OracleReport(
            findings=(
                OracleFinding(
                    invariant_id=target_invariant,
                    status=status,
                    explanation="sandbox replay verdict",
                ),
            )
        )
        return ReplayResult(
            classification=self.classification,
            target_invariant=target_invariant,
            run_id=f"sandbox-{self.calls}",
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


def _agents() -> dict[StrategyType, StrategyAgent]:
    candidate = [
        _response(
            {
                "proposal_type": "ACTION",
                "action_type": "CREATE_USER",
                "arguments": {"initial_balance": "500.00"},
                "reason": "start path",
            }
        ),
        _response(
            {
                "proposal_type": "STOP",
                "candidate_invariant": "NET_PAID_NON_NEGATIVE",
                "reason": "submit suspicion for replay",
            }
        ),
    ]
    stop = [_response({"proposal_type": "STOP", "reason": "done"})]
    return {
        StrategyType.VALUE_FLOW: StrategyAgent(
            StrategyType.VALUE_FLOW, FakeLLMAdapter(candidate)
        ),
        StrategyType.LIFECYCLE: StrategyAgent(
            StrategyType.LIFECYCLE, FakeLLMAdapter(stop.copy())
        ),
        StrategyType.BOUNDARY: StrategyAgent(
            StrategyType.BOUNDARY, FakeLLMAdapter(stop.copy())
        ),
    }


def _run(store: InMemoryRuntimeStore) -> str:
    return store.create_run(
        job_key="candidate-job",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10),
        random_seed=1,
    ).run_id


@pytest.mark.asyncio
async def test_agent_candidate_only_confirms_after_replay_oracle() -> None:
    store = InMemoryRuntimeStore()
    replay = ClassifiedReplay(ReplayClassification.CONFIRMED_VIOLATION)
    run_id = _run(store)
    await AttackWorker(store, replay, _agents()).run(
        run_id, rule_spec(ScenarioType.PROMOTION)
    )
    assert replay.calls == 1
    assert store.get_run(run_id).outcome is AttackOutcome.CONFIRMED_VIOLATION
    assert len(store.counterexamples(run_id)) == 1


@pytest.mark.asyncio
async def test_agent_candidate_cannot_self_confirm_when_replay_is_clean() -> None:
    store = InMemoryRuntimeStore()
    replay = ClassifiedReplay(ReplayClassification.MODEL_DIVERGENCE)
    run_id = _run(store)
    await AttackWorker(store, replay, _agents()).run(
        run_id, rule_spec(ScenarioType.PROMOTION)
    )
    assert replay.calls == 1
    assert store.get_run(run_id).outcome is AttackOutcome.UNCONFIRMED_CANDIDATE
    assert store.counterexamples(run_id) == ()
