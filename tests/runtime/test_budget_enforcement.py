import asyncio
import json

import pytest
from rulearena_attack_runtime import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    AttackOutcome,
    AttackStatus,
    AttackWorker,
    Budget,
    InMemoryRuntimeStore,
    LLMResponse,
    LLMUsage,
    RecordedLLMAdapter,
    StrategyAgent,
    StrategyDiagnostic,
    StrategyTerminalReason,
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
    async def call(_: str, __: str, ___: int | None) -> LLMResponse:
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
    budget = Budget(max_steps=6, max_tokens=10, max_cost=1, max_time_seconds=10)
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
    budget = Budget(max_steps=6, max_tokens=10, max_cost=1, max_time_seconds=0.000001)
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
async def test_unviable_run_budget_fails_closed_instead_of_reporting_no_violation() -> None:
    """A split that starves every strategy must not masquerade as an honest miss.

    Each strategy needs at least one accepted action plus a terminal proposal, so a
    run budget too small to fund that across the strategies cannot search at all.
    Reporting NO_VIOLATION_WITHIN_BUDGET for it would be indistinguishable from a
    real miss in the benchmark's denominators.
    """
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10)
    run = store.create_run(
        job_key="unviable-budget",
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
    with pytest.raises(ValueError, match="viable search budget"):
        await AttackWorker(store, NoReplay(), agents).run(  # type: ignore[arg-type]
            run.run_id, rule_spec(ScenarioType.PROMOTION)
        )
    current = store.get_run(run.run_id)
    assert current.status is AttackStatus.READY
    assert current.outcome is None


@pytest.mark.asyncio
async def test_single_searching_strategy_keeps_the_whole_run_budget() -> None:
    """A single-agent baseline runs one search, so it must not be split three ways.

    The strategy interface is always three-shaped, but counting the two inert
    placeholder strategies as budget shares gave the one real search a third of the
    run budget -- an unfair "normalised budget" in the single-vs-multi ablation.
    """
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10)
    run = store.create_run(
        job_key="single-searching-strategy",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    agents = {
        strategy: StrategyAgent(strategy, _adapter(_stop())) for strategy in StrategyType
    }
    await AttackWorker(
        store,
        NoReplay(),  # type: ignore[arg-type]
        agents,
        searching_strategies=(StrategyType.VALUE_FLOW,),
    ).run(run.run_id, rule_spec(ScenarioType.PROMOTION))
    for strategy in StrategyType:
        item = store.ensure_strategy(run.run_id, strategy, budget)
        assert item.budget.max_steps == budget.max_steps


def _illegal_action() -> str:
    return json.dumps(
        {
            "proposal_type": "ACTION",
            "action_type": "PAY_ORDER",
            "target_id": "order-99",
            "arguments": {},
            "reason": "illegal on purpose",
        }
    )


@pytest.mark.asyncio
async def test_strategy_diagnostic_records_reason_and_rejected_proposal_kinds() -> None:
    """A strategy that never submits a candidate must record *why*, per strategy.

    Without this, "stopped because the model kept proposing illegal actions" and
    "searched honestly and found nothing" both surface as the same aggregate budget
    number, and no search shortfall can be attributed.
    """
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=6, max_tokens=100, max_cost=1, max_time_seconds=10)
    run = store.create_run(
        job_key="illegal-proposals",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    agents = {
        strategy: StrategyAgent(strategy, _adapter(_illegal_action()))
        for strategy in StrategyType
    }
    await AttackWorker(store, NoReplay(), agents).run(  # type: ignore[arg-type]
        run.run_id, rule_spec(ScenarioType.PROMOTION)
    )

    events = [
        event
        for event in store.events_after(run.run_id)
        if event.event_type == "STRATEGY_TERMINATED"
    ]
    assert len(events) == len(StrategyType)
    for event in events:
        diagnostic = StrategyDiagnostic.model_validate(event.data)
        assert diagnostic.terminal_reason is StrategyTerminalReason.ILLEGAL_RETRY_EXHAUSTED
        assert diagnostic.rejected_proposals == {"ACTION_NOT_LEGAL": 3}
        assert diagnostic.candidates_submitted == 0
        assert diagnostic.usage.steps == 0


@pytest.mark.asyncio
async def test_unparsable_replies_end_the_strategy_without_failing_the_run() -> None:
    """A run of unusable model replies is a strategy outcome, not an infra failure.

    Letting the rejection escape failed the whole case, which removes it from the
    metric denominators instead of recording why the strategy produced nothing.
    """
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=6, max_tokens=100, max_cost=1, max_time_seconds=10)
    run = store.create_run(
        job_key="unparsable-output",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    agents = {
        strategy: StrategyAgent(strategy, _adapter("this is not a proposal"))
        for strategy in StrategyType
    }
    await AttackWorker(store, NoReplay(), agents).run(  # type: ignore[arg-type]
        run.run_id, rule_spec(ScenarioType.PROMOTION)
    )

    assert store.get_run(run.run_id).outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET
    events = [
        event
        for event in store.events_after(run.run_id)
        if event.event_type == "STRATEGY_TERMINATED"
    ]
    assert len(events) == len(StrategyType)
    for event in events:
        diagnostic = StrategyDiagnostic.model_validate(event.data)
        assert diagnostic.terminal_reason is StrategyTerminalReason.UNPARSABLE_OUTPUT
        assert diagnostic.rejected_proposals == {"PARSE_INVALID": 1}


@pytest.mark.asyncio
async def test_proposal_output_allowance_is_not_throttled_by_the_run_budget() -> None:
    """The completion allowance is a model property, not a slice of the run budget.

    A reasoning model spends its allowance on the chain of thought, so deriving the
    allowance from the remaining budget yields empty content at full price instead of
    a shorter answer, and every retry repeats it.
    """
    seen: list[int | None] = []

    async def recording(_: str, __: str, max_tokens: int | None) -> LLMResponse:
        seen.append(max_tokens)
        return LLMResponse(
            content=_stop(),
            usage=LLMUsage(input_tokens=0, output_tokens=0, cost=0),
        )

    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=6, max_tokens=300, max_cost=1, max_time_seconds=10)
    run = store.create_run(
        job_key="output-allowance",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    agents = {
        strategy: StrategyAgent(
            strategy, RecordedLLMAdapter(recording, provider="fake", model="allowance")
        )
        for strategy in StrategyType
    }
    await AttackWorker(store, NoReplay(), agents).run(  # type: ignore[arg-type]
        run.run_id, rule_spec(ScenarioType.PROMOTION)
    )

    assert seen
    assert all(value == DEFAULT_MAX_OUTPUT_TOKENS for value in seen), seen


@pytest.mark.asyncio
async def test_worker_finalizes_cancel_requested_before_start() -> None:
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=6, max_tokens=10, max_cost=1, max_time_seconds=10)
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
