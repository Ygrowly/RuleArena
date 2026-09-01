from __future__ import annotations

import copy
import json
import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine


class TraceKind(StrEnum):
    LLM_CALL = "LLM_CALL"
    ACTION_PROPOSAL = "ACTION_PROPOSAL"
    SIMULATION = "SIMULATION"
    SANDBOX_HTTP = "SANDBOX_HTTP"
    SNAPSHOT = "SNAPSHOT"
    ORACLE_CHECK = "ORACLE_CHECK"


_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "ground_truth",
    "ground_truth_ref",
    "hidden_action_sequence",
    "expected_invariant_ids",
    "sandbox_profile",
    "secret",
    "source_text",
}
_FORBIDDEN_MARKERS = (
    "ground_truth",
    "hidden_action_sequence",
    "expected_invariant_ids",
    "sandbox_profile",
    "bearer ",
)


def _assert_safe(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise ValueError(f"sensitive trace field is forbidden: {key}")
            _assert_safe(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_safe(item)
    elif isinstance(value, str):
        lowered = value.casefold()
        if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
            raise ValueError("ground-truth or credential marker is forbidden in trace")


class TraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    strategy_id: str | None = None
    step_id: int = Field(ge=0)
    kind: TraceKind
    rule_version_id: str
    model_config_hash: str | None = None
    prompt_version: str | None = None
    action_summary: dict[str, Any] = Field(default_factory=dict)
    tool_result_summary: dict[str, Any] = Field(default_factory=dict)
    before_state_hash: str | None = None
    after_state_hash: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    status: str
    error_type: str | None = None
    parent_trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def reject_sensitive_payloads(self) -> TraceRecord:
        _assert_safe(self.action_summary)
        _assert_safe(self.tool_result_summary)
        if self.error_type:
            _assert_safe(self.error_type)
        return self


class TraceSink(Protocol):
    def append_trace(self, record: TraceRecord) -> None: ...

    def traces_for_run(self, run_id: str) -> tuple[TraceRecord, ...]: ...


class NullTraceSink:
    def append_trace(self, record: TraceRecord) -> None:
        return None

    def traces_for_run(self, run_id: str) -> tuple[TraceRecord, ...]:
        return ()


class InMemoryTraceStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: list[TraceRecord] = []

    def append_trace(self, record: TraceRecord) -> None:
        with self._lock:
            self._records.append(record.model_copy(deep=True))

    def traces_for_run(self, run_id: str) -> tuple[TraceRecord, ...]:
        with self._lock:
            return tuple(
                item.model_copy(deep=True) for item in self._records if item.run_id == run_id
            )


class PostgresTraceStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = (
            database_url
            if isinstance(database_url, Engine)
            else sa.create_engine(
                database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1),
                pool_pre_ping=True,
            )
        )

    def append_trace(self, record: TraceRecord) -> None:
        statement = sa.text(
            """
            INSERT INTO control.trace_step(
                id, attack_run_id, strategy_run_id, step_id, kind, rule_version_id,
                model_config_hash, prompt_version, action_summary, tool_result_summary,
                before_state_hash, after_state_hash, latency_ms, input_tokens,
                output_tokens, cost, retry_count, status, error_type, parent_trace_id,
                created_at
            ) VALUES (
                CAST(:id AS uuid), CAST(:run_id AS uuid),
                CAST(:strategy_id AS uuid),
                :step_id, :kind, CAST(:rule_version_id AS uuid), :model_config_hash,
                :prompt_version, :action_summary, :tool_result_summary,
                :before_state_hash, :after_state_hash, :latency_ms, :input_tokens,
                :output_tokens, :cost, :retry_count, :status, :error_type,
                CAST(:parent_trace_id AS uuid),
                :created_at
            ) ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(
            sa.bindparam("action_summary", type_=JSONB),
            sa.bindparam("tool_result_summary", type_=JSONB),
        )
        values = record.model_dump(mode="python")
        values["id"] = values.pop("trace_id")
        values["kind"] = record.kind.value
        with self.engine.begin() as connection:
            connection.execute(statement, values)

    def traces_for_run(self, run_id: str) -> tuple[TraceRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT * FROM control.trace_step "
                    "WHERE attack_run_id = CAST(:run_id AS uuid) ORDER BY step_id, created_at, id"
                ),
                {"run_id": run_id},
            ).mappings()
            return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: sa.RowMapping) -> TraceRecord:
        return TraceRecord(
            trace_id=str(row["id"]),
            run_id=str(row["attack_run_id"]),
            strategy_id=(
                str(row["strategy_run_id"]) if row["strategy_run_id"] is not None else None
            ),
            step_id=int(row["step_id"]),
            kind=TraceKind(str(row["kind"])),
            rule_version_id=str(row["rule_version_id"]),
            model_config_hash=row["model_config_hash"],
            prompt_version=row["prompt_version"],
            action_summary=copy.deepcopy(row["action_summary"]),
            tool_result_summary=copy.deepcopy(row["tool_result_summary"]),
            before_state_hash=row["before_state_hash"],
            after_state_hash=row["after_state_hash"],
            latency_ms=int(row["latency_ms"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cost=float(row["cost"]),
            retry_count=int(row["retry_count"]),
            status=str(row["status"]),
            error_type=row["error_type"],
            parent_trace_id=(
                str(row["parent_trace_id"]) if row["parent_trace_id"] is not None else None
            ),
            created_at=row["created_at"],
        )

    def close(self) -> None:
        self.engine.dispose()


def trace_payload(record: TraceRecord) -> str:
    """Canonical safe serialization used by leakage scanners and export."""
    return json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
