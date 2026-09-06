from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from .compiler import (
    BUILTIN_TEMPLATES,
    CompileResult,
    CompileStatus,
    ConfirmationQuestion,
    LLMCallRecord,
    RuleVersion,
    validate_rule_spec,
)
from .workflow import (
    AttackOutcome,
    AttackRun,
    AttackStatus,
    Budget,
    Checkpoint,
    CounterexampleRecord,
    RuntimeEvent,
    StrategyRun,
    StrategyStatus,
    StrategyType,
    transition_allowed,
)


def sync_database_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


class PostgresRuleVersionStore:
    """Append-only RuleVersion repository protected by a database mutation trigger."""

    def __init__(self, database_url: str | Engine) -> None:
        self.engine = (
            database_url
            if isinstance(database_url, Engine)
            else sa.create_engine(sync_database_url(database_url), pool_pre_ping=True)
        )

    @staticmethod
    def _record_llm_call(
        connection: sa.Connection, policy_id: str, call: LLMCallRecord | None
    ) -> None:
        if call is None:
            return
        connection.execute(
            sa.text(
                """
                INSERT INTO control.llm_call(
                    id, policy_pack_id, provider, model, temperature, seed,
                    prompt_version, schema_version, input_tokens, output_tokens,
                    latency_ms, cost, response_hash, created_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:policy_id AS uuid), :provider, :model,
                    :temperature, :seed, :prompt_version, :schema_version,
                    :input_tokens, :output_tokens, :latency_ms, :cost,
                    :response_hash, now()
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": call.call_id,
                "policy_id": policy_id,
                **call.model_dump(mode="python", exclude={"call_id"}),
            },
        )

    def record_compile(
        self, policy_id: str, source_text: str, result: CompileResult
    ) -> None:
        source_hash = hashlib.sha256(source_text.encode()).hexdigest()
        statement = sa.text(
            """
            INSERT INTO control.policy_compile(
                policy_pack_id, status, template_id, rule_spec, questions,
                errors, llm_call_id, updated_at
            ) VALUES (
                CAST(:policy_id AS uuid), :status, :template_id, :rule_spec,
                :questions, :errors, CAST(:llm_call_id AS uuid), now()
            ) ON CONFLICT (policy_pack_id) DO UPDATE SET
                status = EXCLUDED.status,
                template_id = EXCLUDED.template_id,
                rule_spec = EXCLUDED.rule_spec,
                questions = EXCLUDED.questions,
                errors = EXCLUDED.errors,
                llm_call_id = EXCLUDED.llm_call_id,
                updated_at = now()
            """
        ).bindparams(
            sa.bindparam("rule_spec", type_=JSONB),
            sa.bindparam("questions", type_=JSONB),
            sa.bindparam("errors", type_=JSONB),
        )
        with self.engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO control.policy_pack(id, template_id, source_hash, created_at)
                    VALUES (CAST(:id AS uuid), :template_id, :source_hash, now())
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": policy_id,
                    "template_id": result.template_id,
                    "source_hash": source_hash,
                },
            )
            self._record_llm_call(connection, policy_id, result.llm_call)
            connection.execute(
                statement,
                {
                    "policy_id": policy_id,
                    "status": result.status.value,
                    "template_id": result.template_id,
                    "rule_spec": (
                        result.rule_spec.model_dump(mode="json") if result.rule_spec else None
                    ),
                    "questions": [item.model_dump(mode="json") for item in result.questions],
                    "errors": list(result.errors),
                    "llm_call_id": result.llm_call.call_id if result.llm_call else None,
                },
            )

    def get_draft(self, policy_id: str) -> CompileResult:
        with self.engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    """SELECT * FROM control.policy_compile
                       WHERE policy_pack_id = CAST(:id AS uuid)"""
                ),
                {"id": policy_id},
            ).mappings().one()
            call = None
            if row["llm_call_id"] is not None:
                call_row = connection.execute(
                    sa.text("SELECT * FROM control.llm_call WHERE id = CAST(:id AS uuid)"),
                    {"id": str(row["llm_call_id"])},
                ).mappings().one()
                call = LLMCallRecord(
                    call_id=str(call_row["id"]),
                    provider=str(call_row["provider"]),
                    model=str(call_row["model"]),
                    temperature=float(call_row["temperature"]),
                    seed=int(call_row["seed"]) if call_row["seed"] is not None else None,
                    prompt_version=str(call_row["prompt_version"]),
                    schema_version=str(call_row["schema_version"]),
                    input_tokens=int(call_row["input_tokens"]),
                    output_tokens=int(call_row["output_tokens"]),
                    latency_ms=int(call_row["latency_ms"]),
                    cost=float(call_row["cost"]),
                    response_hash=str(call_row["response_hash"]),
                )
            from rulearena_policy_schema import RuleSpec

            return CompileResult(
                status=CompileStatus(str(row["status"])),
                template_id=str(row["template_id"]),
                rule_spec=(
                    RuleSpec.model_validate_json(json.dumps(row["rule_spec"]))
                    if row["rule_spec"] is not None
                    else None
                ),
                questions=tuple(
                    ConfirmationQuestion.model_validate(item) for item in row["questions"]
                ),
                errors=tuple(str(item) for item in row["errors"]),
                llm_call=call,
            )

    def confirm(self, policy_id: str, result: CompileResult) -> RuleVersion:
        if result.status is not CompileStatus.COMPILED or result.rule_spec is None:
            raise ValueError(
                "all ambiguities must be explicitly resolved and recompiled before confirmation"
            )
        scenario = BUILTIN_TEMPLATES.get(result.template_id)
        if scenario is None or validate_rule_spec(result.rule_spec, scenario):
            raise ValueError("compiled RuleSpec failed deterministic confirmation validation")
        canonical = json.dumps(
            {
                "template_id": result.template_id,
                "rule_spec": result.rule_spec.model_dump(mode="json"),
                "schema_version": "1.0",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:policy_id))"),
                {"policy_id": policy_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO control.policy_pack(id, template_id, source_hash, created_at)
                    VALUES (CAST(:id AS uuid), :template_id, :source_hash, now())
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": policy_id, "template_id": result.template_id, "source_hash": digest},
            )
            self._record_llm_call(connection, policy_id, result.llm_call)
            existing = connection.execute(
                sa.text(
                    """SELECT * FROM control.rule_version
                       WHERE policy_pack_id = CAST(:policy_id AS uuid) AND content_hash = :hash"""
                ),
                {"policy_id": policy_id, "hash": digest},
            ).mappings().one_or_none()
            if existing is None:
                version = int(
                    connection.execute(
                        sa.text(
                            """SELECT COALESCE(MAX(version), 0) + 1 FROM control.rule_version
                               WHERE policy_pack_id = CAST(:policy_id AS uuid)"""
                        ),
                        {"policy_id": policy_id},
                    ).scalar_one()
                )
                version_id = str(uuid4())
                statement = sa.text(
                    """
                    INSERT INTO control.rule_version(
                        id, policy_pack_id, version, template_id, rule_spec, content_hash,
                        prompt_version, confirmed_at
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:policy_id AS uuid), :version, :template_id,
                        :rule_spec, :hash, :prompt_version, now()
                    ) RETURNING *
                    """
                ).bindparams(sa.bindparam("rule_spec", type_=JSONB))
                existing = connection.execute(
                    statement,
                    {
                        "id": version_id,
                        "policy_id": policy_id,
                        "version": version,
                        "template_id": result.template_id,
                        "rule_spec": result.rule_spec.model_dump(mode="json"),
                        "hash": digest,
                        "prompt_version": (
                            result.llm_call.prompt_version if result.llm_call else "compiler-v1"
                        ),
                    },
                ).mappings().one()
            return self._version(dict(existing))

    @staticmethod
    def _version(row: Mapping[str, Any]) -> RuleVersion:
        from rulearena_policy_schema import RuleSpec

        return RuleVersion(
            version_id=str(row["id"]),
            policy_id=str(row["policy_pack_id"]),
            version=int(row["version"]),
            template_id=str(row["template_id"]),
            rule_spec=RuleSpec.model_validate_json(json.dumps(row["rule_spec"])),
            content_hash=str(row["content_hash"]),
            prompt_version=str(row["prompt_version"]),
        )

    def get(self, version_id: str) -> RuleVersion:
        with self.engine.connect() as connection:
            row = connection.execute(
                sa.text("SELECT * FROM control.rule_version WHERE id = CAST(:id AS uuid)"),
                {"id": version_id},
            ).mappings().one()
            return self._version(dict(row))

    def close(self) -> None:
        self.engine.dispose()


class PostgresRuntimeStore:
    """PostgreSQL authority implementing the same CAS and uniqueness contract as the test store."""

    def __init__(self, database_url: str | Engine) -> None:
        self.engine = (
            database_url
            if isinstance(database_url, Engine)
            else sa.create_engine(sync_database_url(database_url), pool_pre_ping=True)
        )

    @staticmethod
    def _run(row: Mapping[str, Any]) -> AttackRun:
        return AttackRun(
            run_id=str(row["id"]),
            job_key=str(row["job_key"]),
            rule_version_id=str(row["rule_version_id"]),
            scenario_version_id=str(row["scenario_version_id"]),
            sandbox_version=str(row["sandbox_version"]),
            oracle_version=str(row["oracle_version"]),
            status=AttackStatus(str(row["status"])),
            outcome=AttackOutcome(str(row["outcome"])) if row["outcome"] else None,
            budget=Budget.model_validate(row["budget"]),
            random_seed=int(row["random_seed"]),
            created_at=row["created_at"],
        )

    def create_run(
        self,
        *,
        job_key: str,
        rule_version_id: str,
        scenario_version_id: str,
        sandbox_version: str,
        oracle_version: str,
        budget: Budget,
        random_seed: int,
    ) -> AttackRun:
        run_id = str(uuid4())
        statement = sa.text(
            """
            INSERT INTO control.attack_run(
                id, job_key, rule_version_id, scenario_version_id, sandbox_version,
                oracle_version, status, outcome, budget, random_seed, created_at
            ) VALUES (
                CAST(:id AS uuid), :job_key, CAST(:rule_version_id AS uuid), :scenario_version_id,
                :sandbox_version, :oracle_version, 'READY', NULL, :budget, :random_seed, now()
            )
            ON CONFLICT (job_key) DO NOTHING
            """
        ).bindparams(sa.bindparam("budget", type_=JSONB))
        with self.engine.begin() as connection:
            connection.execute(
                statement,
                {
                    "id": run_id,
                    "job_key": job_key,
                    "rule_version_id": rule_version_id,
                    "scenario_version_id": scenario_version_id,
                    "sandbox_version": sandbox_version,
                    "oracle_version": oracle_version,
                    "budget": budget.model_dump(mode="json"),
                    "random_seed": random_seed,
                },
            )
            row = connection.execute(
                sa.text("SELECT * FROM control.attack_run WHERE job_key = :job_key"),
                {"job_key": job_key},
            ).mappings().one()
            if str(row["id"]) == run_id:
                self._append_event(connection, run_id, "RUN_CREATED", {"status": "READY"})
            return self._run(dict(row))

    def get_run(self, run_id: str) -> AttackRun:
        with self.engine.connect() as connection:
            row = connection.execute(
                sa.text("SELECT * FROM control.attack_run WHERE id = CAST(:id AS uuid)"),
                {"id": run_id},
            ).mappings().one()
            return self._run(dict(row))

    def compare_and_set_status(
        self,
        run_id: str,
        expected: AttackStatus,
        target: AttackStatus,
        *,
        outcome: AttackOutcome | None = None,
    ) -> bool:
        if not transition_allowed(expected, target):
            raise ValueError(f"illegal runtime transition: {expected} -> {target}")
        terminal = target in {AttackStatus.COMPLETED, AttackStatus.CANCELLED, AttackStatus.FAILED}
        if terminal != (outcome is not None):
            raise ValueError("terminal status and outcome must be assigned together")
        with self.engine.begin() as connection:
            result = connection.execute(
                sa.text(
                    """
                    UPDATE control.attack_run
                       SET status = :target, outcome = :outcome,
                           started_at = CASE WHEN :target = 'SEARCHING' AND started_at IS NULL
                                             THEN now() ELSE started_at END,
                           finished_at = CASE WHEN :terminal THEN now() ELSE NULL END
                     WHERE id = CAST(:id AS uuid) AND status = :expected
                    """
                ),
                {
                    "id": run_id,
                    "expected": expected.value,
                    "target": target.value,
                    "outcome": outcome.value if outcome else None,
                    "terminal": terminal,
                },
            )
            if result.rowcount != 1:
                return False
            self._append_event(
                connection,
                run_id,
                "STATUS_CHANGED",
                {"from": expected.value, "to": target.value, "outcome": outcome},
            )
            return True

    def request_cancel(self, run_id: str) -> bool:
        current = self.get_run(run_id).status
        if current not in {AttackStatus.READY, AttackStatus.SEARCHING, AttackStatus.REPLAYING}:
            return False
        return self.compare_and_set_status(run_id, current, AttackStatus.CANCEL_REQUESTED)

    def is_cancel_requested(self, run_id: str) -> bool:
        return self.get_run(run_id).status is AttackStatus.CANCEL_REQUESTED

    @staticmethod
    def _strategy(row: Mapping[str, Any]) -> StrategyRun:
        return StrategyRun(
            strategy_run_id=str(row["id"]),
            attack_run_id=str(row["attack_run_id"]),
            strategy_type=StrategyType(str(row["strategy_type"])),
            status=StrategyStatus(str(row["status"])),
            budget=Budget.model_validate(row["budget"]),
            usage=row["usage"],
        )

    def ensure_strategy(
        self, attack_run_id: str, strategy_type: StrategyType, budget: Budget
    ) -> StrategyRun:
        strategy_id = str(uuid4())
        statement = sa.text(
            """
            INSERT INTO control.strategy_run(
                id, attack_run_id, strategy_type, status, budget, usage
            )
            VALUES (CAST(:id AS uuid), CAST(:run_id AS uuid), :type, 'PENDING', :budget, :usage)
            ON CONFLICT (attack_run_id, strategy_type) DO NOTHING
            """
        ).bindparams(
            sa.bindparam("budget", type_=JSONB), sa.bindparam("usage", type_=JSONB)
        )
        with self.engine.begin() as connection:
            connection.execute(
                statement,
                {
                    "id": strategy_id,
                    "run_id": attack_run_id,
                    "type": strategy_type.value,
                    "budget": budget.model_dump(mode="json"),
                    "usage": {"steps": 0, "tokens": 0, "cost": 0, "elapsed_seconds": 0},
                },
            )
            row = connection.execute(
                sa.text(
                    """SELECT * FROM control.strategy_run
                       WHERE attack_run_id = CAST(:run_id AS uuid) AND strategy_type = :type"""
                ),
                {"run_id": attack_run_id, "type": strategy_type.value},
            ).mappings().one()
            return self._strategy(dict(row))

    def update_strategy(self, strategy: StrategyRun) -> None:
        statement = sa.text(
            """
            UPDATE control.strategy_run SET status = :status, usage = :usage
             WHERE id = CAST(:id AS uuid)
            """
        ).bindparams(sa.bindparam("usage", type_=JSONB))
        with self.engine.begin() as connection:
            result = connection.execute(
                statement,
                {
                    "id": strategy.strategy_run_id,
                    "status": strategy.status.value,
                    "usage": strategy.usage.model_dump(mode="json"),
                },
            )
            if result.rowcount != 1:
                raise KeyError(strategy.strategy_run_id)

    def save_checkpoint(
        self, strategy_run_id: str, state: dict[str, Any], *, expected_version: int
    ) -> Checkpoint:
        # Versioned CAS: insert when expected is empty, otherwise update only
        # the row whose version matches. (An INSERT..SELECT with a WHERE that
        # filters every row never reaches ON CONFLICT, so the update path must
        # be a real UPDATE statement.)
        insert_statement = sa.text(
            """
            INSERT INTO control.checkpoint(strategy_run_id, version, state, created_at)
            SELECT CAST(:id AS uuid), 1, :state, now()
            ON CONFLICT (strategy_run_id) DO NOTHING
            RETURNING strategy_run_id, version, state, created_at
            """
        )
        update_statement = sa.text(
            """
            UPDATE control.checkpoint
               SET version = version + 1, state = :state, created_at = now()
             WHERE strategy_run_id = CAST(:id AS uuid) AND version = :expected
            RETURNING strategy_run_id, version, state, created_at
            """
        )
        with self.engine.begin() as connection:
            statement = (
                insert_statement.bindparams(sa.bindparam("state", type_=JSONB))
                if expected_version == 0
                else update_statement.bindparams(sa.bindparam("state", type_=JSONB))
            )
            row = connection.execute(
                statement,
                {"id": strategy_run_id, "expected": expected_version, "state": state},
            ).mappings().one_or_none()
            if row is None:
                raise ValueError("stale checkpoint version")
            return Checkpoint(
                strategy_run_id=str(row["strategy_run_id"]),
                version=int(row["version"]),
                state=row["state"],
                created_at=row["created_at"],
            )

    def load_checkpoint(self, strategy_run_id: str) -> Checkpoint | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT * FROM control.checkpoint WHERE strategy_run_id = CAST(:id AS uuid)"
                ),
                {"id": strategy_run_id},
            ).mappings().one_or_none()
            return (
                Checkpoint(
                    strategy_run_id=str(row["strategy_run_id"]),
                    version=int(row["version"]),
                    state=row["state"],
                    created_at=row["created_at"],
                )
                if row
                else None
            )

    @staticmethod
    def _append_event(
        connection: sa.Connection,
        run_id: str,
        event_type: str,
        data: Mapping[str, Any],
    ) -> RuntimeEvent:
        statement = sa.text(
            """
            INSERT INTO control.runtime_event(attack_run_id, event_type, data, created_at)
            VALUES (CAST(:run_id AS uuid), :event_type, :data, now())
            RETURNING cursor, created_at
            """
        ).bindparams(sa.bindparam("data", type_=JSONB))
        row = connection.execute(
            statement, {"run_id": run_id, "event_type": event_type, "data": dict(data)}
        ).mappings().one()
        return RuntimeEvent(
            cursor=int(row["cursor"]),
            run_id=run_id,
            event_type=event_type,
            data=dict(data),
            created_at=row["created_at"],
        )

    def append_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> RuntimeEvent:
        with self.engine.begin() as connection:
            return self._append_event(connection, run_id, event_type, data)

    def events_after(self, run_id: str, cursor: int = 0) -> tuple[RuntimeEvent, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    """SELECT cursor, event_type, data, created_at FROM control.runtime_event
                       WHERE attack_run_id = CAST(:id AS uuid) AND cursor > :cursor
                       ORDER BY cursor"""
                ),
                {"id": run_id, "cursor": cursor},
            ).mappings()
            return tuple(
                RuntimeEvent(
                    cursor=int(row["cursor"]),
                    run_id=run_id,
                    event_type=str(row["event_type"]),
                    data=row["data"],
                    created_at=row["created_at"],
                )
                for row in rows
            )

    def save_counterexample(self, record: CounterexampleRecord) -> CounterexampleRecord:
        statement = sa.text(
            """
            INSERT INTO control.counterexample(
                id, attack_run_id, candidate_key, invariant_id, original_actions,
                minimized_actions, replay_run_id, created_at
            ) VALUES (
                CAST(:id AS uuid), CAST(:run_id AS uuid), :candidate_key, :invariant_id,
                :original, :minimized, :replay_run_id, :created_at
            ) ON CONFLICT (attack_run_id, candidate_key) DO NOTHING
            """
        ).bindparams(
            sa.bindparam("original", type_=JSONB), sa.bindparam("minimized", type_=JSONB)
        )
        with self.engine.begin() as connection:
            connection.execute(
                statement,
                {
                    "id": record.counterexample_id,
                    "run_id": record.attack_run_id,
                    "candidate_key": record.candidate_key,
                    "invariant_id": record.invariant_id,
                    "original": list(record.original_actions),
                    "minimized": list(record.minimized_actions),
                    "replay_run_id": record.replay_run_id,
                    "created_at": record.created_at,
                },
            )
            row = connection.execute(
                sa.text(
                    """SELECT * FROM control.counterexample
                       WHERE attack_run_id = CAST(:run_id AS uuid) AND candidate_key = :key"""
                ),
                {"run_id": record.attack_run_id, "key": record.candidate_key},
            ).mappings().one()
            return self._counterexample(dict(row))

    @staticmethod
    def _counterexample(row: Mapping[str, Any]) -> CounterexampleRecord:
        return CounterexampleRecord(
            counterexample_id=str(row["id"]),
            attack_run_id=str(row["attack_run_id"]),
            candidate_key=str(row["candidate_key"]),
            invariant_id=str(row["invariant_id"]),
            original_actions=tuple(row["original_actions"]),
            minimized_actions=tuple(row["minimized_actions"]),
            replay_run_id=str(row["replay_run_id"]),
            created_at=row["created_at"],
        )

    def counterexamples(self, run_id: str) -> tuple[CounterexampleRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT * FROM control.counterexample WHERE attack_run_id = CAST(:id AS uuid)"
                ),
                {"id": run_id},
            ).mappings()
            return tuple(self._counterexample(dict(row)) for row in rows)

    def close(self) -> None:
        self.engine.dispose()
