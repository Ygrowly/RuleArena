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
    MetricValue,
    PostgresBenchmarkStore,
    RawCaseRun,
    ReleaseGate,
    VersionTuple,
    Visibility,
    compute_metrics,
    intervals_overlap,
    load_hidden_manifest,
    pass_at_k,
    pass_to_k,
    verify_ground_truth,
    wilson_interval,
)
from rulearena_oracle import InvariantId

ROOT = Path(__file__).resolve().parents[2]
BUDGET = Budget(max_steps=12, max_tokens=100000, max_cost=1.5, max_time_seconds=90)


def _versions(**changes: str) -> VersionTuple:
    values = {
        "benchmark_version": "golden-v4",
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


def _benchmark_run(
    *,
    run_id: str,
    cases: int,
    started_at: datetime,
    baseline: BaselineType = BaselineType.RANDOM,
) -> BenchmarkRun:
    return BenchmarkRun(
        benchmark_run_id=run_id,
        versions=_versions(),
        baseline=baseline,
        random_seed=7,
        budget=BUDGET,
        repetitions=1,
        suite=Visibility.DEVELOPMENT,
        status=BenchmarkStatus.COMPLETED,
        raw_runs=tuple(_raw(f"case-{index}") for index in range(cases)),
        metrics={},
        started_at=started_at,
        finished_at=started_at,
    )


def test_latest_completed_skips_fragments_and_still_means_latest() -> None:
    """Two properties at once, because getting either alone is a different bug.

    A one-case run is a test artifact and must never be served as the latest benchmark.
    But "the fullest run" is not the answer either -- a 48-case run from an older suite
    is not more recent than the 21-case run that superseded it.
    """
    store = InMemoryBenchmarkStore()
    store.save(
        _benchmark_run(
            run_id="older-but-larger", cases=48, started_at=datetime(2026, 9, 12, tzinfo=UTC)
        )
    )
    store.save(
        _benchmark_run(run_id="current", cases=21, started_at=datetime(2026, 9, 13, tzinfo=UTC))
    )
    store.save(
        _benchmark_run(run_id="fragment", cases=1, started_at=datetime(2026, 9, 14, tzinfo=UTC))
    )
    found = store.latest_completed()
    assert found is not None
    assert found.benchmark_run_id == "current"
    assert store.latest_completed(baseline=BaselineType.BFS) is None
    assert store.latest_completed(suite=Visibility.HIDDEN) is None


def test_golden_assets_are_public_with_an_answer_free_hidden_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    development = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()
    hidden = load_hidden_manifest(ROOT / "benchmarks/hidden-manifest.json")
    assert len(development) == 21
    assert len(hidden) == 17
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
    source = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()[:17]
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"rule_specs": rule_specs, "cases": rows}), encoding="utf-8"
        )
        monkeypatch.setenv("RULEARENA_PROCESS_ROLE", "evaluation")
        monkeypatch.setenv("RULEARENA_HIDDEN_SUITE_PATH", str(path))
        loaded = HiddenCaseLoader(EvaluationAccess.from_environment()).load()
        assert len(loaded) == 17
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
    at_k = pass_at_k(sample, 2).model_dump()
    assert at_k["value"] == 1.0
    assert at_k["numerator"] == 2
    assert at_k["denominator"] == 2
    assert at_k["lower"] == pytest.approx(0.3424, abs=1e-4)
    assert at_k["upper"] == 1.0
    assert at_k["source_run_ids"] == ()
    assert pass_to_k(sample, 2).value == 0


def test_wilson_interval_matches_published_values() -> None:
    assert wilson_interval(0, 0) is None
    assert wilson_interval(0, 9) == pytest.approx((0.0, 0.2991), abs=1e-4)
    # Mirror symmetry: the interval for 0/n is the reflection of the one for n/n.
    assert wilson_interval(9, 9) == pytest.approx((0.7009, 1.0), abs=1e-4)
    assert wilson_interval(0, 2) == pytest.approx((0.0, 0.6576), abs=1e-4)
    assert wilson_interval(2, 2) == pytest.approx((0.3424, 1.0), abs=1e-4)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_rate_metrics_carry_intervals_and_noise_forbids_a_claim() -> None:
    """Two point estimates are not a comparison.

    The published golden-v2 reading (BFS 2/9 beats the LLM's 0/9) sits entirely
    inside the noise nine cases allow, and the interval is what makes that visible.
    """
    cases = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()
    vulnerable = [case for case in cases if case.expected_invariant_ids][:9]
    normal = [case for case in cases if not case.expected_invariant_ids][:7]
    agent = compute_metrics(
        tuple(vulnerable + normal),
        tuple(_raw(case.case_id) for case in vulnerable + normal),
    )
    rate = MetricValue.model_validate(agent["vulnerability_discovery_rate"])
    assert rate.interval() is not None

    bfs_hits = 2
    bfs_interval = wilson_interval(bfs_hits, 9)
    assert bfs_interval is not None
    bfs = MetricValue(
        value=bfs_hits / 9,
        numerator=bfs_hits,
        denominator=9,
        lower=bfs_interval[0],
        upper=bfs_interval[1],
    )
    assert intervals_overlap(rate, bfs), "0/9 and 2/9 must not be read as a difference"
    assert not intervals_overlap(
        MetricValue(value=1.0, lower=0.7, upper=1.0),
        MetricValue(value=0.0, lower=0.0, upper=0.3),
    )


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


@pytest.mark.asyncio
async def test_runner_keeps_finished_cases_when_interrupted_and_resumes() -> None:
    """A multi-hour benchmark must survive an interruption and continue where it stopped.

    Case facts used to be written only when a whole baseline finished, so a kill lost
    every finished case; the diagnostics of the interrupted run were unrecoverable.
    """
    cases = DevelopmentCaseLoader(ROOT / "benchmarks/development-v1.json").load()[:3]

    class InterruptingExecutor:
        def __init__(self, fail_at: int) -> None:
            self.calls = 0
            self.fail_at = fail_at

        async def execute(
            self,
            case: BenchmarkCase,
            *,
            baseline: BaselineType,
            repetition: int,
            random_seed: int,
        ) -> RawCaseRun:
            self.calls += 1
            if self.calls == self.fail_at:
                raise KeyboardInterrupt("simulated kill")
            return _raw(case.case_id, repetition=repetition)

    store = InMemoryBenchmarkStore()
    with pytest.raises(KeyboardInterrupt):
        await BenchmarkRunner(store, InterruptingExecutor(fail_at=3)).run(
            cases,
            versions=_versions(),
            baseline=BaselineType.BFS,
            repetitions=1,
            random_seed=7,
        )
    interrupted = store.running(
        versions=_versions(), baseline=BaselineType.BFS, suite=Visibility.DEVELOPMENT
    )
    assert interrupted is not None
    durable = store.completed_cases(interrupted.benchmark_run_id)
    assert [item.case_id for item in durable] == [cases[0].case_id, cases[1].case_id]

    executed: list[str] = []

    class RecordingExecutor:
        async def execute(
            self,
            case: BenchmarkCase,
            *,
            baseline: BaselineType,
            repetition: int,
            random_seed: int,
        ) -> RawCaseRun:
            executed.append(case.case_id)
            return _raw(case.case_id, repetition=repetition)

    result = await BenchmarkRunner(store, RecordingExecutor()).run(
        cases,
        versions=_versions(),
        baseline=BaselineType.BFS,
        repetitions=1,
        random_seed=7,
        resume=True,
    )
    assert executed == [cases[2].case_id], "finished cases must not be repeated"
    assert result.benchmark_run_id == interrupted.benchmark_run_id
    assert len(result.raw_runs) == 3
    assert (
        store.running(
            versions=_versions(), baseline=BaselineType.BFS, suite=Visibility.DEVELOPMENT
        )
        is None
    )
    assert (
        store.latest(
            versions=_versions(), baseline=BaselineType.BFS, suite=Visibility.DEVELOPMENT
        )
        is not None
    )


class _RollbackEngine:
    """Runs store writes inside a transaction the test rolls back.

    The benchmark tables are append-only, so synthetic rows written to the development
    database could never be removed and would sit in the history that real gate queries
    read. This keeps the verification honest and the database clean.
    """

    def __init__(self, connection: sa.Connection) -> None:
        self._connection = connection

    def begin(self) -> object:
        connection = self._connection

        class _Context:
            def __enter__(self) -> sa.Connection:
                return connection

            def __exit__(self, *_: object) -> None:
                return None

        return _Context()

    def connect(self) -> object:
        return self.begin()


@pytest.mark.postgres
def test_run_lifecycle_is_write_once_and_identity_stays_frozen() -> None:
    database_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL after applying control migrations")
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    engine = sa.create_engine(sync_url)
    now = datetime.now(UTC)
    run = BenchmarkRun(
        versions=_versions(),
        baseline=BaselineType.BFS,
        random_seed=11,
        budget=BUDGET,
        repetitions=1,
        suite=Visibility.DEVELOPMENT,
        status=BenchmarkStatus.RUNNING,
        raw_runs=(),
        metrics={},
        started_at=now,
        finished_at=None,
    )
    raw = _raw("lifecycle-case-1")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            store = PostgresBenchmarkStore(engine)
            store.engine = _RollbackEngine(connection)  # type: ignore[assignment]
            store.start_run(run)
            running = store.running(
                versions=_versions(),
                baseline=BaselineType.BFS,
                suite=Visibility.DEVELOPMENT,
            )
            assert running is not None
            assert running.benchmark_run_id == run.benchmark_run_id

            store.append_case(run.benchmark_run_id, raw)
            assert [item.case_id for item in store.completed_cases(run.benchmark_run_id)] == [
                "lifecycle-case-1"
            ]

            store.finalize_run(
                run.benchmark_run_id,
                status=BenchmarkStatus.COMPLETED,
                raw_runs=(raw,),
                metrics={"marker": 1},
                finished_at=now,
            )
            with pytest.raises(ValueError, match="only a RUNNING"):
                store.finalize_run(
                    run.benchmark_run_id,
                    status=BenchmarkStatus.COMPLETED,
                    raw_runs=(raw,),
                    metrics={"marker": 2},
                    finished_at=now,
                )
            # Identity and configuration stay frozen even for the one allowed transition.
            with pytest.raises(sa.exc.ProgrammingError, match="append-only"):
                connection.execute(
                    sa.text(
                        "UPDATE control.benchmark_run SET random_seed = 1234 "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": run.benchmark_run_id},
                )
        finally:
            transaction.rollback()
    engine.dispose()


@pytest.mark.postgres
def test_postgres_benchmark_facts_are_normalized_and_append_only() -> None:
    database_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL after applying control migrations")
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    engine = sa.create_engine(sync_url)
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
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            store = PostgresBenchmarkStore(engine)
            store.engine = _RollbackEngine(connection)  # type: ignore[assignment]
            store.save(run)
            assert store.get(run.benchmark_run_id) == run
            count = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM control.benchmark_case_run "
                    "WHERE benchmark_run_id = CAST(:id AS uuid)"
                ),
                {"id": run.benchmark_run_id},
            )
            assert count == 1
            # Each rejection aborts the transaction, so it needs its own savepoint for
            # the following one to be attempted against a usable transaction.
            for statement in (
                "UPDATE control.benchmark_run SET status = 'FAILED' WHERE id = CAST(:id AS uuid)",
                "DELETE FROM control.benchmark_case_run WHERE benchmark_run_id = CAST(:id AS uuid)",
                "DELETE FROM control.benchmark_run WHERE id = CAST(:id AS uuid)",
            ):
                savepoint = connection.begin_nested()
                with pytest.raises(sa.exc.ProgrammingError):
                    connection.execute(sa.text(statement), {"id": run.benchmark_run_id})
                savepoint.rollback()
        finally:
            transaction.rollback()
    engine.dispose()


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
