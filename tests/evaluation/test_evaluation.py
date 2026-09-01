import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from rulearena_attack_runtime import AttackOutcome, Budget, BudgetUsage, SandboxReplayRunner
from rulearena_evaluation import (
    BaselineType,
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunner,
    BenchmarkStatus,
    DevelopmentCaseLoader,
    EvaluationAccess,
    FailureKind,
    HiddenCaseLoader,
    InMemoryBenchmarkStore,
    PostgresBenchmarkStore,
    RawCaseRun,
    ReleaseGate,
    VersionTuple,
    Visibility,
    compute_metrics,
    load_hidden_manifest,
    pass_at_k,
    pass_to_k,
    verify_ground_truth,
)
from rulearena_oracle import InvariantId

ROOT = Path(__file__).resolve().parents[2]
BUDGET = Budget(max_steps=12, max_tokens=12000, max_cost=1.5, max_time_seconds=90)


def _versions(**changes: str) -> VersionTuple:
    values = {
        "benchmark_version": "golden-v1",
        "runtime_version": "runtime-v1",
        "rule_set_version": "rules-v1",
        "scenario_set_version": "scenarios-v1",
        "sandbox_version": "sandbox-suite-v1",
        "oracle_version": "1.0",
        "model_config_hash": "a" * 64,
        "prompt_version": "benchmark-v1",
    }
    values.update(changes)
    return VersionTuple(**values)


def _raw(
    case_id: str,
    *,
    outcome: AttackOutcome = AttackOutcome.NO_VIOLATION_WITHIN_BUDGET,
    failure: FailureKind = FailureKind.NONE,
    invariants: frozenset[InvariantId] = frozenset(),
    repetition: int = 1,
) -> RawCaseRun:
    return RawCaseRun(
        case_id=case_id,
        visibility=Visibility.DEVELOPMENT,
        baseline=BaselineType.BFS,
        repetition=repetition,
        outcome=outcome,
        failure_kind=failure,
        confirmed_invariant_ids=invariants,
        usage=BudgetUsage(steps=2, tokens=3, cost=0.1, elapsed_seconds=0.2),
    )


def test_golden_assets_have_16_public_and_8_non_leaking_hidden_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    development = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()
    hidden = load_hidden_manifest(ROOT / "benchmarks/hidden-manifest.json")
    assert len(development) == 16
    assert len(hidden) == 8
    assert {case.scenario_type for case in development} == {
        case.scenario_type for case in hidden
    }
    assert {case.expected_outcome.value for case in development} == {"VULNERABLE", "NORMAL"}
    assert all(
        all(case.ground_truth_replays)
        for case in development
        if case.expected_invariant_ids
    )
    serialized_manifest = json.dumps(
        [case.model_dump(mode="json") for case in hidden], sort_keys=True
    ).casefold()
    assert "expected_outcome" not in serialized_manifest
    assert "expected_invariant" not in serialized_manifest
    monkeypatch.delenv("RULEARENA_PROCESS_ROLE", raising=False)
    monkeypatch.delenv("RULEARENA_HIDDEN_SUITE_PATH", raising=False)
    with pytest.raises(PermissionError):
        EvaluationAccess.from_environment()


def test_private_hidden_loader_requires_evaluation_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()[:8]
    rule_specs: dict[str, object] = {}
    rows: list[dict[str, object]] = []
    for index, case in enumerate(source):
        reference = f"rule-{index}"
        rule_specs[reference] = case.rule_spec.model_dump(mode="json")
        row = case.model_dump(mode="json", exclude={"rule_spec"})
        row.update(
            {
                "case_id": f"private-{index}",
                "visibility": "hidden",
                "rule_spec_ref": reference,
            }
        )
        rows.append(row)
    path = ROOT / ".cache" / "phase4-private-suite.json"
    try:
        path.write_text(
            json.dumps({"rule_specs": rule_specs, "cases": rows}), encoding="utf-8"
        )
        monkeypatch.setenv("RULEARENA_PROCESS_ROLE", "evaluation")
        monkeypatch.setenv("RULEARENA_HIDDEN_SUITE_PATH", str(path))
        loaded = HiddenCaseLoader(EvaluationAccess.from_environment()).load()
        assert len(loaded) == 8
        assert all(case.visibility is Visibility.HIDDEN for case in loaded)
    finally:
        path.unlink(missing_ok=True)


def test_metrics_recompute_and_failure_denominators_are_exact() -> None:
    cases = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()
    vulnerable = next(case for case in cases if case.expected_invariant_ids)
    normal = next(case for case in cases if not case.expected_invariant_ids)
    target = next(iter(vulnerable.expected_invariant_ids))
    raw = (
        _raw(vulnerable.case_id, invariants=frozenset({target})),
        _raw(
            vulnerable.case_id,
            outcome=AttackOutcome.INFRA_FAILED,
            failure=FailureKind.INFRA_FAILED,
            repetition=2,
        ),
        _raw(
            vulnerable.case_id,
            outcome=AttackOutcome.CANCELLED,
            failure=FailureKind.CANCELLED,
            repetition=3,
        ),
        _raw(normal.case_id),
    )
    first = compute_metrics((vulnerable, normal), raw)
    second = compute_metrics((vulnerable, normal), raw)
    assert first == second
    assert first["vulnerability_discovery_rate"]["value"] == 1
    assert first["normal_confirmed_false_positive_rate"]["value"] == 0
    assert first["candidate_confirmation_rate"]["value"] is None
    assert first["failure_counts"] == {
        "INFRA_FAILED": 1,
        "CANCELLED": 1,
        "EVALUATION_FAILED": 0,
    }
    sample = {"a": [True, False], "b": [False, True], "short": [True]}
    assert pass_at_k(sample, 2).model_dump() == {
        "value": 1.0,
        "numerator": 2,
        "denominator": 2,
        "source_run_ids": (),
    }
    assert pass_to_k(sample, 2).value == 0


def test_release_gate_rejects_stale_version_and_store_is_append_only() -> None:
    now = datetime.now(UTC)
    metrics = {
        "normal_confirmed_false_positive_rate": {"value": 0, "denominator": 3},
        "vulnerability_discovery_rate": {"value": 0.8, "denominator": 5},
        "replay_stability_rate": {"value": 1, "denominator": 15},
        "historical_p0_pass_rate": 1.0,
        "ground_truth_leakage_count": 0,
    }
    run = BenchmarkRun(
        versions=_versions(),
        baseline=BaselineType.MULTI_STRATEGY,
        random_seed=7,
        budget=BUDGET,
        repetitions=3,
        suite=Visibility.HIDDEN,
        status=BenchmarkStatus.COMPLETED,
        raw_runs=(),
        metrics=metrics,
        started_at=now,
        finished_at=now,
    )
    store = InMemoryBenchmarkStore()
    store.save(run)
    with pytest.raises(ValueError, match="append-only"):
        store.save(run)
    assert store.latest(
        versions=_versions(runtime_version="runtime-v2"),
        baseline=BaselineType.MULTI_STRATEGY,
        suite=Visibility.HIDDEN,
    ) is None
    assert ReleaseGate().evaluate(
        run,
        expected_versions=_versions(runtime_version="runtime-v2"),
        expected_budget=BUDGET,
        expected_seed=7,
    ).passed is False
    assert ReleaseGate().evaluate(
        run,
        expected_versions=_versions(),
        expected_budget=BUDGET,
        expected_seed=7,
    ).passed is True


@pytest.mark.asyncio
async def test_runner_persists_raw_facts_and_rejects_budget_drift() -> None:
    cases = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()[:2]

    class Executor:
        async def execute(
            self,
            case: BenchmarkCase,
            *,
            baseline: BaselineType,
            repetition: int,
            random_seed: int,
        ) -> RawCaseRun:
            return _raw(case.case_id, repetition=repetition)

    store = InMemoryBenchmarkStore()
    result = await BenchmarkRunner(store, Executor()).run(
        cases,
        versions=_versions(),
        baseline=BaselineType.BFS,
        repetitions=2,
        random_seed=7,
    )
    assert len(result.raw_runs) == 4
    assert store.get(result.benchmark_run_id).raw_runs == result.raw_runs
    drifted = cases[1].model_copy(
        update={
            "budget": Budget(
                max_steps=11, max_tokens=12000, max_cost=1.5, max_time_seconds=90
            )
        }
    )
    with pytest.raises(ValueError, match="normalized case budget"):
        await BenchmarkRunner(store, Executor()).run(
            (cases[0], drifted),
            versions=_versions(),
            baseline=BaselineType.BFS,
            repetitions=1,
            random_seed=7,
        )


@pytest.mark.postgres
def test_postgres_benchmark_facts_are_normalized_and_append_only() -> None:
    database_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL after applying control migrations")
    now = datetime.now(UTC)
    raw = _raw("dev-promotion-01")
    run = BenchmarkRun(
        versions=_versions(),
        baseline=BaselineType.BFS,
        random_seed=99,
        budget=BUDGET,
        repetitions=1,
        suite=Visibility.DEVELOPMENT,
        status=BenchmarkStatus.COMPLETED,
        raw_runs=(raw,),
        metrics={"source": "recomputed"},
        started_at=now,
        finished_at=now,
    )
    store = PostgresBenchmarkStore(database_url)
    try:
        store.save(run)
        assert store.get(run.benchmark_run_id) == run
        with store.engine.connect() as connection:
            count = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM control.benchmark_case_run "
                    "WHERE benchmark_run_id = CAST(:id AS uuid)"
                ),
                {"id": run.benchmark_run_id},
            )
        assert count == 1
        with pytest.raises(sa.exc.DBAPIError):
            with store.engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE control.benchmark_run SET status = 'FAILED' "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": run.benchmark_run_id},
                )
    finally:
        store.close()


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_development_vulnerable_ground_truth_replays_three_of_three(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    cases = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()
    replay = SandboxReplayRunner(sandbox_http_url, sandbox_token)
    vulnerable = [case for case in cases if case.expected_invariant_ids]
    evidence = [await verify_ground_truth(case, replay) for case in vulnerable]
    assert len(evidence) == 9
    assert all(item.successful_replays == item.attempted_replays for item in evidence)
