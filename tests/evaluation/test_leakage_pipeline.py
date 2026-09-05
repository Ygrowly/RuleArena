import json
from pathlib import Path
from typing import Any

import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    Budget,
    BudgetUsage,
    FakeLLMAdapter,
    MinimizationResult,
    ReplayClassification,
    ReplayResult,
)
from rulearena_evaluation import (
    AgentBaselineExecutor,
    BaselineType,
    BenchmarkCase,
    BenchmarkRunner,
    DevelopmentCaseLoader,
    ExpectedOutcome,
    FailureKind,
    InMemoryBenchmarkStore,
    RawCaseRun,
    VersionTuple,
    Visibility,
    compute_metrics,
    historical_p0_pass_rate,
)
from rulearena_oracle import InvariantId, OracleFinding, OracleReport, OracleStatus
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec

ROOT = Path(__file__).resolve().parents[2]
BUDGET = Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10)
VERSIONS = VersionTuple(
    benchmark_version="golden-v1",
    runtime_version="runtime-v1",
    rule_set_version="rules-v1",
    scenario_set_version="scenarios-v1",
    sandbox_version="fixed",
    oracle_version="1.0",
    model_config_hash="a" * 64,
    prompt_version="benchmark-v1",
)


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


def _proposal(payload: dict[str, object]) -> str:
    return json.dumps(payload)


_ACTION = _proposal(
    {
        "proposal_type": "ACTION",
        "action_type": "CREATE_USER",
        "arguments": {"initial_balance": "500.00"},
        "reason": "start path",
    }
)
_STOP_DONE = _proposal({"proposal_type": "STOP", "reason": "done"})


def _vulnerable_case(case_id: str) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        benchmark_version="golden-v1",
        visibility=Visibility.DEVELOPMENT,
        scenario_type=ScenarioType.PROMOTION,
        tags=("unit",),
        budget=BUDGET,
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        rule_spec=rule_spec(ScenarioType.PROMOTION),
        expected_outcome=ExpectedOutcome.VULNERABLE,
        expected_invariant_ids=frozenset({InvariantId.NET_PAID_NON_NEGATIVE}),
        construction_reason="unit test case",
        ground_truth_replays=(True, True, True),
        ground_truth_actions=(
            {"action_type": "CREATE_USER", "arguments": {"initial_balance": "500.00"}},
        ),
    )


def _executor_with_sequences(
    classification: ReplayClassification,
    sequences: dict[str, list[str]],
) -> AgentBaselineExecutor:
    def factory(prompt_version: str) -> FakeLLMAdapter:
        return FakeLLMAdapter(list(sequences[prompt_version]))

    return AgentBaselineExecutor(ClassifiedReplay(classification), factory)  # type: ignore[arg-type]


def test_metrics_pipeline_reports_leakage_and_historical_p0() -> None:
    cases = DevelopmentCaseLoader(ROOT / "benchmarks" / "development-v1.json").load()
    default = compute_metrics(cases, [])
    assert default["ground_truth_leakage_count"] == 0
    assert default["historical_p0_pass_rate"] is None
    finding = compute_metrics(
        cases, [], leakage_findings=("payload[0] contains forbidden marker",)
    )
    assert finding["ground_truth_leakage_count"] == 1


def test_historical_p0_rate_is_recomputed_from_oracle_facts() -> None:
    assert historical_p0_pass_rate() == 1.0


@pytest.mark.asyncio
async def test_agent_baseline_counts_one_replayed_candidate_on_confirmation() -> None:
    case = _vulnerable_case("unit-promotion-01")
    executor = _executor_with_sequences(
        ReplayClassification.CONFIRMED_VIOLATION,
        {
            "value_flow-v1": [
                _ACTION,
                _proposal(
                    {
                        "proposal_type": "STOP",
                        "candidate_invariant": "NET_PAID_NON_NEGATIVE",
                        "reason": "value flow candidate",
                    }
                ),
                _STOP_DONE,
            ],
            "lifecycle-v1": [_STOP_DONE],
            "boundary-v1": [_STOP_DONE],
        },
    )
    fact = await executor.execute(
        case, baseline=BaselineType.MULTI_STRATEGY, repetition=1, random_seed=1
    )
    assert fact.confirmed_candidates == 1
    assert fact.replayed_candidates == 1
    assert fact.outcome is AttackOutcome.CONFIRMED_VIOLATION


@pytest.mark.asyncio
async def test_agent_baseline_counts_multiple_replayed_candidates_without_crash() -> None:
    """Two replayed candidates with none confirmed must not violate the
    confirmed <= replayed invariant."""
    case = _vulnerable_case("unit-promotion-02")
    executor = _executor_with_sequences(
        ReplayClassification.MODEL_DIVERGENCE,
        {
            "value_flow-v1": [
                _ACTION,
                _proposal(
                    {
                        "proposal_type": "STOP",
                        "candidate_invariant": "NET_PAID_NON_NEGATIVE",
                        "reason": "value flow candidate",
                    }
                ),
                _STOP_DONE,
            ],
            "lifecycle-v1": [
                _ACTION,
                _proposal(
                    {
                        "proposal_type": "STOP",
                        "candidate_invariant": "ORDER_TERMINAL_MONOTONICITY",
                        "reason": "lifecycle candidate",
                    }
                ),
                _STOP_DONE,
            ],
            "boundary-v1": [_STOP_DONE],
        },
    )
    fact = await executor.execute(
        case, baseline=BaselineType.MULTI_STRATEGY, repetition=1, random_seed=1
    )
    assert fact.replayed_candidates == 2
    assert fact.confirmed_candidates == 0
    assert fact.outcome is AttackOutcome.UNCONFIRMED_CANDIDATE


@pytest.mark.asyncio
async def test_agent_baseline_fails_closed_on_leakage_marker_in_model_payload() -> None:
    case = _vulnerable_case("unit-promotion-03")
    executor = _executor_with_sequences(
        ReplayClassification.CONFIRMED_VIOLATION,
        {
            "value_flow-v1": [
                _proposal(
                    {
                        "proposal_type": "STOP",
                        "reason": "ground_truth data revealed",
                    }
                )
            ],
            "lifecycle-v1": [_STOP_DONE],
            "boundary-v1": [_STOP_DONE],
        },
    )
    with pytest.raises(ValueError, match="ground truth leakage"):
        await executor.execute(
            case, baseline=BaselineType.MULTI_STRATEGY, repetition=1, random_seed=1
        )


@pytest.mark.asyncio
async def test_runner_wires_historical_p0_and_zero_leakage_into_metrics() -> None:
    cases = DevelopmentCaseLoader(ROOT / "benchmarks" / "development-v1.json").load()[:2]

    class Executor:
        async def execute(
            self,
            case: BenchmarkCase,
            *,
            baseline: BaselineType,
            repetition: int,
            random_seed: int,
        ) -> RawCaseRun:
            return RawCaseRun(
                case_id=case.case_id,
                visibility=case.visibility,
                baseline=baseline,
                repetition=repetition,
                outcome=AttackOutcome.NO_VIOLATION_WITHIN_BUDGET,
                failure_kind=FailureKind.NONE,
                usage=BudgetUsage(steps=1, elapsed_seconds=0.1),
            )

    store = InMemoryBenchmarkStore()
    result = await BenchmarkRunner(store, Executor()).run(
        cases,
        versions=VERSIONS,
        baseline=BaselineType.BFS,
        repetitions=1,
        random_seed=7,
        historical_p0_pass_rate=0.5,
    )
    assert result.metrics["historical_p0_pass_rate"] == 0.5
    assert result.metrics["ground_truth_leakage_count"] == 0


@pytest.mark.asyncio
async def test_runner_rejects_mixed_rule_versions_within_one_scenario() -> None:
    cases = DevelopmentCaseLoader(ROOT / "benchmarks" / "development-v1.json").load()[:2]
    assert cases[0].scenario_type is cases[1].scenario_type

    class Executor:
        async def execute(
            self,
            case: BenchmarkCase,
            *,
            baseline: BaselineType,
            repetition: int,
            random_seed: int,
        ) -> RawCaseRun:
            return RawCaseRun(
                case_id=case.case_id,
                visibility=case.visibility,
                baseline=baseline,
                repetition=repetition,
                outcome=AttackOutcome.NO_VIOLATION_WITHIN_BUDGET,
                failure_kind=FailureKind.NONE,
                usage=BudgetUsage(steps=1, elapsed_seconds=0.1),
            )

    drifted = cases[1].model_copy(update={"rule_version_id": "other-rule-version"})
    with pytest.raises(ValueError, match="one rule and scenario version"):
        await BenchmarkRunner(InMemoryBenchmarkStore(), Executor()).run(
            (cases[0], drifted),
            versions=VERSIONS,
            baseline=BaselineType.BFS,
            repetitions=1,
            random_seed=7,
        )
