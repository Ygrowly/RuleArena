"""Persistence for a refund benchmark run, and the lookup `refund-verify` needs.

One row per run, with every case fact in `raw_runs`. The search benchmark splits its
facts across a second table so a multi-hour run can be resumed and its cells appended as
they finish; this suite takes minutes end to end and has no resume path, so a per-ticket
table would add schema without a consumer. The run row carries everything the metrics
were computed from, and they stay recomputable from it -- which is the property that
matters, not the table count.
"""

from __future__ import annotations

import copy
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from .models import BenchmarkStatus, VersionTuple, Visibility
from .refund_models import AgentMode, RefundBenchmarkRun

# Below this, a "run" is a fragment -- what a test writes when it exercises the
# persistence path with one ticket -- and reporting one as the latest measurement
# publishes a rate that measures nothing.
MIN_TICKETS = 2

_INSERT = sa.text(
    """
    INSERT INTO control.refund_benchmark_run(
        id, benchmark_version, runtime_version, rule_set_version,
        scenario_set_version, sandbox_version, oracle_version,
        model_config_hash, prompt_version, mode, random_seed, repetitions,
        suite, status, raw_runs, metrics, started_at, finished_at
    ) VALUES (
        CAST(:id AS uuid), :benchmark_version, :runtime_version,
        :rule_set_version, :scenario_set_version, :sandbox_version,
        :oracle_version, :model_config_hash, :prompt_version, :mode,
        :random_seed, :repetitions, :suite, :status, :raw_runs,
        :metrics, :started_at, :finished_at
    )
    """
).bindparams(
    sa.bindparam("raw_runs", type_=JSONB),
    sa.bindparam("metrics", type_=JSONB),
)


def _values(run: RefundBenchmarkRun) -> dict[str, Any]:
    return {
        "id": run.benchmark_run_id,
        **run.versions.model_dump(mode="python"),
        "mode": run.mode.value,
        "random_seed": run.random_seed,
        "repetitions": run.repetitions,
        "suite": run.suite.value,
        "status": run.status.value,
        "raw_runs": [item.model_dump(mode="json") for item in run.raw_runs],
        "metrics": copy.deepcopy(run.metrics),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


class RefundBenchmarkStore(Protocol):
    def save(self, run: RefundBenchmarkRun) -> None: ...

    def get(self, benchmark_run_id: str) -> RefundBenchmarkRun: ...

    def latest_completed(
        self, *, versions: VersionTuple | None = None, mode: AgentMode | None = None
    ) -> RefundBenchmarkRun | None: ...


class InMemoryRefundBenchmarkStore:
    def __init__(self) -> None:
        self._runs: dict[str, RefundBenchmarkRun] = {}

    def save(self, run: RefundBenchmarkRun) -> None:
        if run.benchmark_run_id in self._runs:
            raise ValueError("RefundBenchmarkRun is append-only")
        self._runs[run.benchmark_run_id] = run.model_copy(deep=True)

    def get(self, benchmark_run_id: str) -> RefundBenchmarkRun:
        return self._runs[benchmark_run_id].model_copy(deep=True)

    def latest_completed(
        self, *, versions: VersionTuple | None = None, mode: AgentMode | None = None
    ) -> RefundBenchmarkRun | None:
        candidates = [
            run
            for run in self._runs.values()
            if run.status is BenchmarkStatus.COMPLETED
            and len(run.raw_runs) >= MIN_TICKETS
            and (versions is None or run.versions == versions)
            and (mode is None or run.mode is mode)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.started_at).model_copy(deep=True)


class PostgresRefundBenchmarkStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = (
            database_url
            if isinstance(database_url, Engine)
            else sa.create_engine(
                database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1),
                pool_pre_ping=True,
            )
        )

    def save(self, run: RefundBenchmarkRun) -> None:
        with self.engine.begin() as connection:
            connection.execute(_INSERT, _values(run))

    def get(self, benchmark_run_id: str) -> RefundBenchmarkRun:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.text(
                        "SELECT * FROM control.refund_benchmark_run WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": benchmark_run_id},
                )
                .mappings()
                .one()
            )
            return self._from_row(row)

    def latest_completed(
        self, *, versions: VersionTuple | None = None, mode: AgentMode | None = None
    ) -> RefundBenchmarkRun | None:
        filters = ["status = 'COMPLETED'", "jsonb_array_length(raw_runs) >= :min_tickets"]
        values: dict[str, Any] = {"min_tickets": MIN_TICKETS}
        if versions is not None:
            filters.extend(f"{key} = :{key}" for key in VersionTuple.model_fields)
            values.update(versions.model_dump(mode="python"))
        if mode is not None:
            filters.append("mode = :mode")
            values["mode"] = mode.value
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.text(
                        f"""SELECT * FROM control.refund_benchmark_run
                            WHERE {' AND '.join(filters)}
                            ORDER BY started_at DESC LIMIT 1"""
                    ),
                    values,
                )
                .mappings()
                .one_or_none()
            )
            return self._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(row: sa.RowMapping) -> RefundBenchmarkRun:
        return RefundBenchmarkRun(
            benchmark_run_id=str(row["id"]),
            versions=VersionTuple(
                **{key: str(row[key]) for key in VersionTuple.model_fields}
            ),
            mode=AgentMode(str(row["mode"])),
            random_seed=int(row["random_seed"]),
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
