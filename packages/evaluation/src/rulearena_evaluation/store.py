from __future__ import annotations

import copy
import threading
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from .models import BaselineType, BenchmarkRun, BenchmarkStatus, VersionTuple, Visibility


class BenchmarkStore(Protocol):
    def save(self, run: BenchmarkRun) -> None: ...

    def get(self, benchmark_run_id: str) -> BenchmarkRun: ...

    def latest(
        self,
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        suite: Visibility,
    ) -> BenchmarkRun | None: ...

    def latest_completed(self) -> BenchmarkRun | None: ...


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

    def latest_completed(self) -> BenchmarkRun | None:
        with self._lock:
            candidates = [
                run for run in self._runs.values() if run.status is BenchmarkStatus.COMPLETED
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda item: item.started_at).model_copy(deep=True)


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
        statement = sa.text(
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
        values = {
            "id": run.benchmark_run_id,
            **run.versions.model_dump(mode="python"),
            "baseline": run.baseline.value,
            "random_seed": run.random_seed,
            "budget": run.budget.model_dump(mode="json"),
            "repetitions": run.repetitions,
            "suite": run.suite.value,
            "status": run.status.value,
            "raw_runs": [item.model_dump(mode="json") for item in run.raw_runs],
            "metrics": copy.deepcopy(run.metrics),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }
        with self.engine.begin() as connection:
            connection.execute(statement, values)
            case_statement = sa.text(
                """
                INSERT INTO control.benchmark_case_run(
                    id, benchmark_run_id, case_id, visibility, baseline, repetition,
                    attack_run_id, outcome, failure_kind, confirmed_invariant_ids,
                    replayed_candidates, confirmed_candidates, replay_attempts,
                    replay_successes, compile_attempted, rule_spec_schema_valid,
                    usage, started_at, finished_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:benchmark_run_id AS uuid), :case_id,
                    :visibility, :baseline, :repetition,
                    CAST(:attack_run_id AS uuid),
                    :outcome, :failure_kind, :confirmed_invariant_ids,
                    :replayed_candidates, :confirmed_candidates, :replay_attempts,
                    :replay_successes, :compile_attempted, :rule_spec_schema_valid,
                    :usage, :started_at, :finished_at
                )
                """
            ).bindparams(
                sa.bindparam("confirmed_invariant_ids", type_=JSONB),
                sa.bindparam("usage", type_=JSONB),
            )
            for raw in run.raw_runs:
                raw_values = raw.model_dump(mode="json")
                raw_values["id"] = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{run.benchmark_run_id}:{raw.case_id}:{raw.repetition}",
                    )
                )
                raw_values["benchmark_run_id"] = run.benchmark_run_id
                connection.execute(case_statement, raw_values)

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
        filters = " AND ".join(f"{key} = :{key}" for key in versions.model_fields)
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

    def latest_completed(self) -> BenchmarkRun | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT * FROM control.benchmark_run WHERE status = 'COMPLETED' "
                    "ORDER BY started_at DESC LIMIT 1"
                )
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
