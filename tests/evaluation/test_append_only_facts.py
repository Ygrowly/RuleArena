import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from rulearena_attack_runtime import (
    Budget,
    CompileResult,
    CompileStatus,
    PostgresRuleVersionStore,
    PostgresRuntimeStore,
    sync_database_url,
)
from rulearena_observability import PostgresTraceStore, TraceKind, TraceRecord
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


@pytest.mark.postgres
def test_trace_and_case_facts_are_append_only_and_cascade_free() -> None:
    database_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL after applying control migrations")
    sync_url = sync_database_url(database_url)
    versions = PostgresRuleVersionStore(database_url)
    runtime = PostgresRuntimeStore(database_url)
    traces = PostgresTraceStore(database_url)
    compiled = CompileResult(
        status=CompileStatus.COMPILED,
        template_id="promotion",
        rule_spec=rule_spec(ScenarioType.PROMOTION),
    )
    policy_id = str(uuid4())
    versions.record_compile(policy_id, "append-only probe", compiled)
    version = versions.confirm(policy_id, compiled)
    run = runtime.create_run(
        job_key=f"append-only:{uuid4()}",
        rule_version_id=version.version_id,
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10),
        random_seed=1,
    )
    traces.append_trace(
        TraceRecord(
            run_id=run.run_id,
            step_id=1,
            kind=TraceKind.ORACLE_CHECK,
            rule_version_id=version.version_id,
            action_summary={},
            tool_result_summary={},
            status="OK",
        )
    )
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        trace_id = connection.execute(
            sa.text(
                "SELECT id FROM control.trace_step "
                "WHERE attack_run_id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": run.run_id},
        ).scalar_one()
    tamper_statements = (
        (
            "UPDATE control.trace_step SET status = 'TAMPERED' "
            "WHERE id = CAST(:id AS uuid)",
            {"id": str(trace_id)},
        ),
        (
            "DELETE FROM control.trace_step WHERE id = CAST(:id AS uuid)",
            {"id": str(trace_id)},
        ),
        (
            "DELETE FROM control.attack_run WHERE id = CAST(:id AS uuid)",
            {"id": run.run_id},
        ),
    )
    try:
        for statement, params in tamper_statements:
            with pytest.raises(sa.exc.DBAPIError, match="append-only|violates"):
                with engine.begin() as connection:
                    connection.execute(sa.text(statement), params)
    finally:
        engine.dispose()
        traces.close()
        runtime.close()
        versions.close()
