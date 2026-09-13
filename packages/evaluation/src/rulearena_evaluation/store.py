from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from .models import (
    BaselineType,
    BenchmarkRun,
    BenchmarkStatus,
    RawCaseRun,
    VersionTuple,
    Visibility,
)

_RUN_INSERT = sa.text(
    """
    INSERT INTO control.benchmark_run(
        id, benchmark_version, runtime_version, rule_set_version,
        scenario_set_version, sandbox_version, oracle_version,
        model_config_hash, prompt_version, baseline, random_seed, budget,
        repetitions, suite, status, raw_runs, metrics, started_at, finished_at
    ) VALUES (
        CAST(:id AS uuid), :benchmark_version, :runtime_version,
        :rule_set_version, :scenario_set_version, :sandbox_version,
        :oracle_version, :model_config_hash, :prompt_version, :baseline,
        :random_seed, :budget, :repetitions, :suite, :status, :raw_runs,
        :metrics, :started_at, :finished_at
    )
    """
).bindparams(
    sa.bindparam("budget", type_=JSONB),
    sa.bindparam("raw_runs", type_=JSONB),
    sa.bindparam("metrics", type_=JSONB),
)

_CASE_INSERT = sa.text(
    """
    INSERT INTO control.benchmark_case_run(
        id, benchmark_run_id, case_id, visibility, baseline, repetition,
        attack_run_id, outcome, failure_kind, confirmed_invariant_ids,
        replayed_candidates, confirmed_candidates, replay_attempts,
        replay_successes, compile_attempted, rule_spec_schema_valid,
        usage, strategy_diagnostics, started_at, finished_at
    ) VALUES (
        CAST(:id AS uuid), CAST(:benchmark_run_id AS uuid), :case_id,
        :visibility, :baseline, :repetition,
        CAST(:attack_run_id AS uuid),
        :outcome, :failure_kind, :confirmed_invariant_ids,
        :replayed_candidates, :confirmed_candidates, :replay_attempts,
        :replay_successes, :compile_attempted, :rule_spec_schema_valid,
        :usage, :strategy_diagnostics, :started_at, :finished_at
    )
    """
).bindparams(
    sa.bindparam("confirmed_invariant_ids", type_=JSONB),
    sa.bindparam("usage", type_=JSONB),
    sa.bindparam("strategy_diagnostics", type_=JSONB),
)


def _run_values(run: BenchmarkRun) -> dict[str, Any]:
    """Identity and configuration; the outcome columns are filled in separately."""
    return {
        "id": run.benchmark_run_id,
        **run.versions.model_dump(mode="python"),
        "baseline": run.baseline.value,
        "random_seed": run.random_seed,
        "budget": run.budget.model_dump(mode="json"),
        "repetitions": run.repetitions,
        "suite": run.suite.value,
        "started_at": run.started_at,
    }


def _case_values(benchmark_run_id: str, raw: RawCaseRun) -> dict[str, Any]:
    values = raw.model_dump(mode="json")
    values["id"] = str(
        uuid5(NAMESPACE_URL, f"{benchmark_run_id}:{raw.case_id}:{raw.repetition}")
    )
    values["benchmark_run_id"] = benchmark_run_id
    return values


def _case_from_row(row: sa.RowMapping) -> RawCaseRun:
    """Rebuild a case fact from its row; storage-only columns are dropped."""
    available = set(row.keys())
    return RawCaseRun.model_validate(
        {key: row[key] for key in RawCaseRun.model_fields if key in available}
    )


class BenchmarkStore(Protocol):
    def save(self, run: BenchmarkRun) -> None: ...

    def start_run(self, run: BenchmarkRun) -> None:
        """Persist a RUNNING run so its case facts can be appended as they finish."""
        ...

    def append_case(self, benchmark_run_id: str, raw: RawCaseRun) -> None: ...

    def finalize_run(
        self,
        benchmark_run_id: str,
        *,
        status: BenchmarkStatus,
        raw_runs: tuple[RawCaseRun, ...],
        metrics: dict[str, Any],
        finished_at: datetime,
    ) -> None:
        """Write the outcome once; a finished run stays immutable afterwards."""
        ...

    def running(
        self,
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        suite: Visibility,
    ) -> BenchmarkRun | None:
        """The unfinished run matching this configuration, for resuming."""
        ...

    def completed_cases(self, benchmark_run_id: str) -> tuple[RawCaseRun, ...]: ...

    def get(self, benchmark_run_id: str) -> BenchmarkRun: ...

    def latest(
        self,
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        suite: Visibility,
    ) -> BenchmarkRun | None: ...

    def latest_completed(
        self,
        *,
        suite: Visibility | None = None,
        baseline: BaselineType | None = None,
    ) -> BenchmarkRun | None: ...


class InMemoryBenchmarkStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, BenchmarkRun] = {}

    def save(self, run: BenchmarkRun) -> None:
        with self._lock:
            if run.benchmark_run_id in self._runs:
                raise ValueError("BenchmarkRun is append-only")
            self._runs[run.benchmark_run_id] = run.model_copy(deep=True)

    def get(self, benchmark_run_id: str) -> BenchmarkRun:
        with self._lock:
            return self._runs[benchmark_run_id].model_copy(deep=True)

    def start_run(self, run: BenchmarkRun) -> None:
        with self._lock:
            if run.benchmark_run_id in self._runs:
                raise ValueError("BenchmarkRun is append-only")
            self._runs[run.benchmark_run_id] = run.model_copy(
                update={"status": BenchmarkStatus.RUNNING, "raw_runs": (), "metrics": {}},
                deep=True,
            )

    def append_case(self, benchmark_run_id: str, raw: RawCaseRun) -> None:
        with self._lock:
            run = self._runs[benchmark_run_id]
            if any(
                item.case_id == raw.case_id and item.repetition == raw.repetition
                for item in run.raw_runs
            ):
                raise ValueError("benchmark case fact is append-only")
            self._runs[benchmark_run_id] = run.model_copy(
                update={"raw_runs": run.raw_runs + (raw,)}, deep=True
            )

    def finalize_run(
        self,
        benchmark_run_id: str,
        *,
        status: BenchmarkStatus,
        raw_runs: tuple[RawCaseRun, ...],
        metrics: dict[str, Any],
        finished_at: datetime,
    ) -> None:
        with self._lock:
            run = self._runs[benchmark_run_id]
            if run.status is not BenchmarkStatus.RUNNING:
                raise ValueError("BenchmarkRun is append-only")
            self._runs[benchmark_run_id] = run.model_copy(
                update={
                    "status": status,
                    "raw_runs": raw_runs,
                    "metrics": copy.deepcopy(metrics),
                    "finished_at": finished_at,
                },
                deep=True,
            )

    def running(
        self,
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        suite: Visibility,
    ) -> BenchmarkRun | None:
        with self._lock:
            candidates = [
                run
                for run in self._runs.values()
                if run.versions == versions
                and run.baseline is baseline
                and run.suite is suite
                and run.status is BenchmarkStatus.RUNNING
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda item: item.started_at).model_copy(deep=True)

    def completed_cases(self, benchmark_run_id: str) -> tuple[RawCaseRun, ...]:
        with self._lock:
            return tuple(self._runs[benchmark_run_id].raw_runs)

    def latest(
        self,
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        suite: Visibility,
    ) -> BenchmarkRun | None:
        with self._lock:
            candidates = [
                run
                for run in self._runs.values()
                if run.versions == versions
                and run.baseline is baseline
                and run.suite is suite
                and run.status is BenchmarkStatus.COMPLETED
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda item: item.started_at).model_copy(deep=True)

    def latest_completed(
        self,
        *,
        suite: Visibility | None = None,
        baseline: BaselineType | None = None,
    ) -> BenchmarkRun | None:
        """The most recent *whole-suite* completed run.

        Ordering is by case count before recency on purpose. A benchmark result is a
        suite result; a partial run, and a single-case run written by a test, are both
        complete runs, and presenting one as "the latest benchmark" reports a number
        that measures nothing. Picking the fullest run never selects either.
        """
        with self._lock:
            candidates = [
                run
                for run in self._runs.values()
                if run.status is BenchmarkStatus.COMPLETED
                and (suite is None or run.suite is suite)
                and (baseline is None or run.baseline is baseline)
            ]
            if not candidates:
                return None
            newest = max(candidates, key=lambda item: (len(item.raw_runs), item.started_at))
            return newest.model_copy(deep=True)


class PostgresBenchmarkStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = (
            database_url
            if isinstance(database_url, Engine)
            else sa.create_engine(
                database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1),
                pool_pre_ping=True,
            )
        )

    def save(self, run: BenchmarkRun) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                _RUN_INSERT,
                {
                    **_run_values(run),
                    "status": run.status.value,
                    "raw_runs": [item.model_dump(mode="json") for item in run.raw_runs],
                    "metrics": copy.deepcopy(run.metrics),
                    "finished_at": run.finished_at,
                },
            )
            for raw in run.raw_runs:
                connection.execute(_CASE_INSERT, _case_values(run.benchmark_run_id, raw))

    def start_run(self, run: BenchmarkRun) -> None:
        """The run row exists before its cells so a killed run keeps what it finished."""
        with self.engine.begin() as connection:
            connection.execute(
                _RUN_INSERT,
                {
                    **_run_values(run),
                    "status": BenchmarkStatus.RUNNING.value,
                    "raw_runs": [],
                    "metrics": {},
                    "finished_at": None,
                },
            )

    def append_case(self, benchmark_run_id: str, raw: RawCaseRun) -> None:
        with self.engine.begin() as connection:
            connection.execute(_CASE_INSERT, _case_values(benchmark_run_id, raw))

    def finalize_run(
        self,
        benchmark_run_id: str,
        *,
        status: BenchmarkStatus,
        raw_runs: tuple[RawCaseRun, ...],
        metrics: dict[str, Any],
        finished_at: datetime,
    ) -> None:
        statement = sa.text(
            """
            UPDATE control.benchmark_run
               SET status = :status, raw_runs = :raw_runs, metrics = :metrics,
                   finished_at = :finished_at
             WHERE id = CAST(:id AS uuid) AND status = 'RUNNING'
            """
        ).bindparams(
            sa.bindparam("raw_runs", type_=JSONB),
            sa.bindparam("metrics", type_=JSONB),
        )
        with self.engine.begin() as connection:
            result = connection.execute(
                statement,
                {
                    "id": benchmark_run_id,
                    "status": status.value,
                    "raw_runs": [item.model_dump(mode="json") for item in raw_runs],
                    "metrics": copy.deepcopy(metrics),
                    "finished_at": finished_at,
                },
            )
            if result.rowcount != 1:
                raise ValueError("only a RUNNING benchmark run can be finalized")

    def running(
        self,
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        suite: Visibility,
    ) -> BenchmarkRun | None:
        filters = " AND ".join(f"{key} = :{key}" for key in VersionTuple.model_fields)
        query = sa.text(
            f"""SELECT * FROM control.benchmark_run
                WHERE {filters} AND baseline = :baseline AND suite = :suite
                  AND status = 'RUNNING'
                ORDER BY started_at DESC LIMIT 1"""
        )
        values = {
            **versions.model_dump(mode="python"),
            "baseline": baseline.value,
            "suite": suite.value,
        }
        with self.engine.connect() as connection:
            row = connection.execute(query, values).mappings().one_or_none()
            return self._from_row(row) if row is not None else None

    def completed_cases(self, benchmark_run_id: str) -> tuple[RawCaseRun, ...]:
        """Case facts already durable for a run, so a resumed run does not repeat them."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT * FROM control.benchmark_case_run "
                    "WHERE benchmark_run_id = CAST(:id AS uuid)"
                ),
                {"id": benchmark_run_id},
            ).mappings().all()
        return tuple(_case_from_row(row) for row in rows)

    def get(self, benchmark_run_id: str) -> BenchmarkRun:
        with self.engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT * FROM control.benchmark_run WHERE id = CAST(:id AS uuid)"
                ),
                {"id": benchmark_run_id},
            ).mappings().one()
            return self._from_row(row)

    def latest(
        self,
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        suite: Visibility,
    ) -> BenchmarkRun | None:
        filters = " AND ".join(f"{key} = :{key}" for key in VersionTuple.model_fields)
        query = sa.text(
            f"""SELECT * FROM control.benchmark_run
                WHERE {filters} AND baseline = :baseline AND suite = :suite
                  AND status = 'COMPLETED'
                ORDER BY started_at DESC LIMIT 1"""
        )
        values = {
            **versions.model_dump(mode="python"),
            "baseline": baseline.value,
            "suite": suite.value,
        }
        with self.engine.connect() as connection:
            row = connection.execute(query, values).mappings().one_or_none()
            return self._from_row(row) if row is not None else None

    def latest_completed(
        self,
        *,
        suite: Visibility | None = None,
        baseline: BaselineType | None = None,
    ) -> BenchmarkRun | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT * FROM control.benchmark_run WHERE status = 'COMPLETED' "
                    "AND (CAST(:suite AS text) IS NULL OR suite = CAST(:suite AS text)) "
                    "AND (CAST(:baseline AS text) IS NULL OR baseline = CAST(:baseline AS text)) "
                    "ORDER BY jsonb_array_length(raw_runs) DESC, started_at DESC LIMIT 1"
                ),
                {
                    "suite": suite.value if suite is not None else None,
                    "baseline": baseline.value if baseline is not None else None,
                },
            ).mappings().one_or_none()
            return self._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(row: sa.RowMapping) -> BenchmarkRun:
        versions = VersionTuple(
            **{key: str(row[key]) for key in VersionTuple.model_fields}
        )
        return BenchmarkRun(
            benchmark_run_id=str(row["id"]),
            versions=versions,
            baseline=BaselineType(str(row["baseline"])),
            random_seed=int(row["random_seed"]),
            budget=row["budget"],
            repetitions=int(row["repetitions"]),
            suite=Visibility(str(row["suite"])),
            status=BenchmarkStatus(str(row["status"])),
            raw_runs=tuple(row["raw_runs"]),
            metrics=copy.deepcopy(row["metrics"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def close(self) -> None:
        self.engine.dispose()
