import asyncio
import json

import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackWorker,
    Budget,
    InMemoryRuntimeStore,
    LLMResponse,
    LLMUsage,
    RecordedLLMAdapter,
    StrategyAgent,
    StrategyType,
)
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


class NoReplay:
    async def replay(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("budget-rejected actions must not replay")

    async def minimize(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("budget-rejected actions must not minimize")


def _adapter(content: str, *, tokens: int = 0, cost: float = 0) -> RecordedLLMAdapter:
    async def call(_: str, __: str) -> LLMResponse:
        return LLMResponse(
            content=content,
            usage=LLMUsage(input_tokens=tokens, output_tokens=0, cost=cost),
        )

    return RecordedLLMAdapter(call, provider="fake", model="budget-test")


def _action() -> str:
    return json.dumps(
        {
            "proposal_type": "ACTION",
            "action_type": "CREATE_USER",
            "arguments": {"initial_balance": "500.00"},
            "reason": "try action",
        }
    )


def _stop() -> str:
    return json.dumps({"proposal_type": "STOP", "reason": "done"})


@pytest.mark.asyncio
@pytest.mark.parametrize(("tokens", "cost"), [(11, 0), (0, 1.01)])
async def test_token_and_cost_budget_stop_before_simulator_side_effect(
    tokens: int, cost: float
) -> None:
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=3, max_tokens=10, max_cost=1, max_time_seconds=10)
    run = store.create_run(
        job_key=f"budget-{tokens}-{cost}",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    agents = {
        StrategyType.VALUE_FLOW: StrategyAgent(
            StrategyType.VALUE_FLOW, _adapter(_action(), tokens=tokens, cost=cost)
        ),
        StrategyType.LIFECYCLE: StrategyAgent(
            StrategyType.LIFECYCLE, _adapter(_stop())
        ),
        StrategyType.BOUNDARY: StrategyAgent(
            StrategyType.BOUNDARY, _adapter(_stop())
        ),
    }
    await AttackWorker(store, NoReplay(), agents).run(  # type: ignore[arg-type]
        run.run_id, rule_spec(ScenarioType.PROMOTION)
    )
    strategy = store.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, budget)
    assert store.load_checkpoint(strategy.strategy_run_id) is None
    assert strategy.usage.tokens == tokens
    assert strategy.usage.cost == cost
    assert store.get_run(run.run_id).outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET


@pytest.mark.asyncio
async def test_total_time_budget_is_enforced_before_model_call() -> None:
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=3, max_tokens=10, max_cost=1, max_time_seconds=0.000001)
    run = store.create_run(
        job_key="time-budget",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    agents = {
        strategy: StrategyAgent(strategy, _adapter(_action())) for strategy in StrategyType
    }
    await asyncio.sleep(0.01)
    await AttackWorker(store, NoReplay(), agents).run(  # type: ignore[arg-type]
        run.run_id, rule_spec(ScenarioType.PROMOTION)
    )
    assert store.get_run(run.run_id).outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET
    for strategy in StrategyType:
        item = store.ensure_strategy(run.run_id, strategy, budget)
        assert item.usage.steps == 0


@pytest.mark.asyncio
async def test_worker_finalizes_cancel_requested_before_start() -> None:
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=3, max_tokens=10, max_cost=1, max_time_seconds=10)
    run = store.create_run(
        job_key="cancel-before-start",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    assert store.request_cancel(run.run_id)
    agents = {
        strategy: StrategyAgent(strategy, _adapter(_action())) for strategy in StrategyType
    }

    await AttackWorker(store, NoReplay(), agents).run(  # type: ignore[arg-type]
        run.run_id, rule_spec(ScenarioType.PROMOTION)
    )

    cancelled = store.get_run(run.run_id)
    assert cancelled.status.value == "CANCELLED"
    assert cancelled.outcome is AttackOutcome.CANCELLED
    for strategy in StrategyType:
        assert store.load_checkpoint(
            store.ensure_strategy(run.run_id, strategy, budget).strategy_run_id
        ) is None
