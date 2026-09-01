from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from rulearena_attack_runtime import (
    AttackOutcome,
    AttackWorker,
    BudgetUsage,
    FakeLLMAdapter,
    InMemoryRuntimeStore,
    LLMAdapter,
    ReplayClassification,
    SandboxReplayRunner,
    StrategyAgent,
    StrategyType,
)
from rulearena_domain_contracts import ActionType
from rulearena_oracle import InvariantId
from rulearena_policy_schema import ScenarioType
from rulearena_reference_simulator import (
    ReferenceSimulator,
    SearchBudget,
    SimAction,
    SimulationState,
    breadth_first_search,
    seeded_random_search,
)

from .models import BaselineType, BenchmarkCase, FailureKind, RawCaseRun

_SCENARIO_INVARIANTS: dict[ScenarioType, tuple[InvariantId, ...]] = {
    ScenarioType.PROMOTION: (
        InvariantId.NET_PAID_NON_NEGATIVE,
        InvariantId.REFUND_NOT_EXCEED_PAID,
        InvariantId.COUPON_SINGLE_CONSUMPTION,
        InvariantId.ORDER_TERMINAL_MONOTONICITY,
        InvariantId.IDEMPOTENT_EFFECT,
    ),
    ScenarioType.REFUND_POINTS: (
        InvariantId.NET_PAID_NON_NEGATIVE,
        InvariantId.REFUND_NOT_EXCEED_PAID,
        InvariantId.POINTS_VALUE_CONSERVATION,
        InvariantId.ORDER_TERMINAL_MONOTONICITY,
        InvariantId.IDEMPOTENT_EFFECT,
    ),
    ScenarioType.MEMBERSHIP_ENTITLEMENT: (
        InvariantId.ENTITLEMENT_NON_NEGATIVE,
        InvariantId.ENTITLEMENT_REFUND_CONSISTENCY,
        InvariantId.IDEMPOTENT_EFFECT,
    ),
}


def _deserialize_action(value: Mapping[str, object]) -> SimAction:
    raw_arguments = value.get("arguments", {})
    if not isinstance(raw_arguments, Mapping):
        raise ValueError("counterexample action arguments are invalid")
    arguments: dict[str, str | int | bool] = {}
    for key, item in raw_arguments.items():
        if (
            not isinstance(key, str)
            or isinstance(item, float)
            or not isinstance(item, str | int | bool)
        ):
            raise ValueError("counterexample action argument has an invalid type")
        arguments[key] = item
    return SimAction.build(
        ActionType(str(value["action_type"])),
        actor_id=str(value.get("actor_id", "user-1")),
        target_id=(
            str(value["target_id"]) if value.get("target_id") is not None else None
        ),
        idempotency_key=(
            str(value["idempotency_key"])
            if value.get("idempotency_key") is not None
            else None
        ),
        **arguments,
    )


class SearchBaselineExecutor:
    """Random/BFS candidate generation followed by the same Sandbox replay boundary."""

    def __init__(self, replay: SandboxReplayRunner) -> None:
        self.replay = replay

    async def execute(
        self,
        case: BenchmarkCase,
        *,
        baseline: BaselineType,
        repetition: int,
        random_seed: int,
    ) -> RawCaseRun:
        if baseline not in {BaselineType.RANDOM, BaselineType.BFS}:
            raise ValueError("SearchBaselineExecutor supports only random and BFS")
        started_at = datetime.now(UTC)
        started = time.monotonic()
        simulator = ReferenceSimulator(case.rule_spec)
        minimum_depth = {
            ScenarioType.PROMOTION: 4,
            ScenarioType.REFUND_POINTS: 3,
            ScenarioType.MEMBERSHIP_ENTITLEMENT: 3,
        }[case.scenario_type]
        def goal(_state: SimulationState, trace: tuple[SimAction, ...]) -> bool:
            return len(trace) >= minimum_depth
        budget = SearchBudget(
            max_depth=case.budget.max_steps,
            max_nodes=max(1, case.budget.max_steps * 32),
        )
        result = (
            breadth_first_search(simulator, goal, budget)
            if baseline is BaselineType.BFS
            else seeded_random_search(simulator, goal, seed=random_seed, budget=budget)
        )
        confirmed: set[InvariantId] = set()
        replayed = 0
        stability_attempts = 0
        stability_successes = 0
        for invariant in _SCENARIO_INVARIANTS[case.scenario_type]:
            replayed += 1
            replay = await self.replay.replay(
                case.rule_spec,
                result.trace,
                invariant,
                sandbox_version=case.sandbox_version,
            )
            if replay.classification is ReplayClassification.CONFIRMED_VIOLATION:
                confirmed.add(invariant)
                # Candidate confirmation and counterexample stability are distinct
                # metrics. Count the confirming replay plus two fresh RunSpaces so
                # every reported counterexample has explicit 3/3 evidence.
                stability_attempts += 3
                stability_successes += 1
                for _ in range(2):
                    repeated = await self.replay.replay(
                        case.rule_spec,
                        result.trace,
                        invariant,
                        sandbox_version=case.sandbox_version,
                    )
                    if (
                        repeated.classification
                        is ReplayClassification.CONFIRMED_VIOLATION
                    ):
                        stability_successes += 1
        elapsed = time.monotonic() - started
        return RawCaseRun(
            case_id=case.case_id,
            visibility=case.visibility,
            baseline=baseline,
            repetition=repetition,
            outcome=(
                AttackOutcome.CONFIRMED_VIOLATION
                if confirmed
                else AttackOutcome.NO_VIOLATION_WITHIN_BUDGET
            ),
            confirmed_invariant_ids=frozenset(confirmed),
            replayed_candidates=replayed,
            confirmed_candidates=len(confirmed),
            replay_attempts=stability_attempts,
            replay_successes=stability_successes,
            usage=BudgetUsage(steps=len(result.trace), elapsed_seconds=elapsed),
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )


AdapterFactory = Callable[[str], LLMAdapter]


class AgentBaselineExecutor:
    """Runs either one predefined general agent or the three isolated strategies."""

    def __init__(self, replay: SandboxReplayRunner, adapter_factory: AdapterFactory) -> None:
        self.replay = replay
        self.adapter_factory = adapter_factory

    async def execute(
        self,
        case: BenchmarkCase,
        *,
        baseline: BaselineType,
        repetition: int,
        random_seed: int,
    ) -> RawCaseRun:
        if baseline not in {BaselineType.SINGLE_AGENT, BaselineType.MULTI_STRATEGY}:
            raise ValueError("AgentBaselineExecutor supports only agent baselines")
        started_at = datetime.now(UTC)
        store = InMemoryRuntimeStore()
        run = store.create_run(
            job_key=f"benchmark:{case.case_id}:{baseline.value}:{repetition}:{random_seed}",
            rule_version_id=case.rule_version_id,
            scenario_version_id=case.scenario_version_id,
            sandbox_version=case.sandbox_version,
            oracle_version=case.oracle_version,
            budget=case.budget,
            random_seed=random_seed,
        )
        if baseline is BaselineType.SINGLE_AGENT:
            agents = {
                StrategyType.VALUE_FLOW: StrategyAgent(
                    StrategyType.VALUE_FLOW,
                    self.adapter_factory("single-general-v1"),
                    role_name="GENERAL",
                ),
                StrategyType.LIFECYCLE: StrategyAgent(
                    StrategyType.LIFECYCLE,
                    FakeLLMAdapter(['{"proposal_type":"STOP","reason":"disabled"}']),
                ),
                StrategyType.BOUNDARY: StrategyAgent(
                    StrategyType.BOUNDARY,
                    FakeLLMAdapter(['{"proposal_type":"STOP","reason":"disabled"}']),
                ),
            }
        else:
            agents = {
                strategy: StrategyAgent(
                    strategy, self.adapter_factory(f"{strategy.value.casefold()}-v1")
                )
                for strategy in StrategyType
            }
        try:
            await AttackWorker(store, self.replay, agents).run(run.run_id, case.rule_spec)
        except Exception:
            finished = datetime.now(UTC)
            return RawCaseRun(
                case_id=case.case_id,
                visibility=case.visibility,
                baseline=baseline,
                repetition=repetition,
                attack_run_id=run.run_id,
                outcome=AttackOutcome.INFRA_FAILED,
                failure_kind=FailureKind.INFRA_FAILED,
                started_at=started_at,
                finished_at=finished,
                usage=BudgetUsage(
                    elapsed_seconds=(finished - started_at).total_seconds()
                ),
            )
        completed = store.get_run(run.run_id)
        counterexamples = store.counterexamples(run.run_id)
        stability_attempts = 0
        stability_successes = 0
        for counterexample in counterexamples:
            invariant = InvariantId(counterexample.invariant_id)
            actions = tuple(
                _deserialize_action(action)
                for action in counterexample.minimized_actions
            )
            for _ in range(3):
                stability_attempts += 1
                repeated = await self.replay.replay(
                    case.rule_spec,
                    actions,
                    invariant,
                    sandbox_version=case.sandbox_version,
                )
                if (
                    repeated.classification
                    is ReplayClassification.CONFIRMED_VIOLATION
                ):
                    stability_successes += 1
        strategies = [
            store.ensure_strategy(run.run_id, strategy, case.budget)
            for strategy in StrategyType
        ]
        usage = BudgetUsage(
            steps=sum(item.usage.steps for item in strategies),
            tokens=sum(item.usage.tokens for item in strategies),
            cost=sum(item.usage.cost for item in strategies),
            elapsed_seconds=(datetime.now(UTC) - started_at).total_seconds(),
        )
        return RawCaseRun(
            case_id=case.case_id,
            visibility=case.visibility,
            baseline=baseline,
            repetition=repetition,
            attack_run_id=run.run_id,
            outcome=completed.outcome or AttackOutcome.INFRA_FAILED,
            failure_kind=(
                FailureKind.NONE
                if completed.outcome is not AttackOutcome.INFRA_FAILED
                else FailureKind.INFRA_FAILED
            ),
            confirmed_invariant_ids=frozenset(
                InvariantId(item.invariant_id) for item in counterexamples
            ),
            replayed_candidates=(
                1
                if completed.outcome
                in {
                    AttackOutcome.CONFIRMED_VIOLATION,
                    AttackOutcome.UNCONFIRMED_CANDIDATE,
                }
                else 0
            ),
            confirmed_candidates=len(counterexamples),
            replay_attempts=stability_attempts,
            replay_successes=stability_successes,
            usage=usage,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )


class DelegatingCaseExecutor:
    def __init__(
        self,
        search: SearchBaselineExecutor,
        agent: AgentBaselineExecutor,
    ) -> None:
        self.search = search
        self.agent = agent

    async def execute(
        self,
        case: BenchmarkCase,
        *,
        baseline: BaselineType,
        repetition: int,
        random_seed: int,
    ) -> RawCaseRun:
        executor = (
            self.search
            if baseline in {BaselineType.RANDOM, BaselineType.BFS}
            else self.agent
        )
        return await executor.execute(
            case,
            baseline=baseline,
            repetition=repetition,
            random_seed=random_seed,
        )
