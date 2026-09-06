import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from rulearena_attack_runtime import (
    AttackStatus,
    Budget,
    CompileResult,
    CompileStatus,
    PostgresRuleVersionStore,
    PostgresRuntimeStore,
    StrategyType,
    sync_database_url,
)
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


@pytest.mark.postgres
def test_postgres_runtime_cas_uniqueness_checkpoint_and_immutability() -> None:
    database_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL after applying control migrations")
    versions = PostgresRuleVersionStore(database_url)
    runtime = PostgresRuntimeStore(database_url)
    policy_id = str(uuid4())
    compiled = CompileResult(
        status=CompileStatus.COMPILED,
        template_id="promotion",
        rule_spec=rule_spec(ScenarioType.PROMOTION),
    )
    versions.record_compile(policy_id, "满 150 减 50", compiled)
    assert versions.get_draft(policy_id) == compiled
    version = versions.confirm(policy_id, compiled)
    assert versions.confirm(
        policy_id,
        CompileResult(
            status=CompileStatus.COMPILED,
            template_id="promotion",
            rule_spec=rule_spec(ScenarioType.PROMOTION),
        ),
    ) == version

    budget = Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10)
    job_key = f"postgres-test:{uuid4()}"
    run = runtime.create_run(
        job_key=job_key,
        rule_version_id=version.version_id,
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=1,
    )
    assert runtime.create_run(
        job_key=job_key,
        rule_version_id=version.version_id,
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=1,
    ).run_id == run.run_id
    assert runtime.compare_and_set_status(run.run_id, AttackStatus.READY, AttackStatus.SEARCHING)
    assert not runtime.compare_and_set_status(
        run.run_id, AttackStatus.READY, AttackStatus.SEARCHING
    )
    strategy = runtime.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, budget)
    assert runtime.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, budget) == strategy
    checkpoint = runtime.save_checkpoint(
        strategy.strategy_run_id, {"frontier": [1]}, expected_version=0
    )
    assert checkpoint.version == 1
    with pytest.raises(ValueError, match="stale"):
        runtime.save_checkpoint(
            strategy.strategy_run_id, {"frontier": [2]}, expected_version=0
        )
    first_cursor = runtime.events_after(run.run_id)[-1].cursor
    runtime.append_event(run.run_id, "TEST", {"safe": True})
    assert runtime.events_after(run.run_id, first_cursor)[0].event_type == "TEST"

    engine = sa.create_engine(sync_database_url(database_url))
    with engine.begin() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(
            sa.text(
                """UPDATE control.rule_version SET template_id = 'changed'
                   WHERE id = CAST(:id AS uuid)"""
            ),
            {"id": version.version_id},
        )
    engine.dispose()
    runtime.close()
    versions.close()


def test_postgres_checkpoint_second_save_cas_roundtrip() -> None:
    database_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL after applying control migrations")
    runtime = PostgresRuntimeStore(database_url)
    versions = PostgresRuleVersionStore(database_url)
    compiled = CompileResult(
        status=CompileStatus.COMPILED,
        template_id="promotion",
        rule_spec=rule_spec(ScenarioType.PROMOTION),
    )
    policy_id = str(uuid4())
    versions.record_compile(policy_id, "checkpoint cas", compiled)
    version = versions.confirm(policy_id, compiled)
    budget = Budget(max_steps=5, max_tokens=100, max_cost=1, max_time_seconds=10)
    run = runtime.create_run(
        job_key=f"checkpoint-cas:{uuid4()}",
        rule_version_id=version.version_id,
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=1,
    )
    strategy = runtime.ensure_strategy(run.run_id, StrategyType.VALUE_FLOW, budget)
    first = runtime.save_checkpoint(strategy.strategy_run_id, {"actions": [1]}, expected_version=0)
    assert first.version == 1
    second = runtime.save_checkpoint(
        strategy.strategy_run_id, {"actions": [1, 2]}, expected_version=first.version
    )
    assert second.version == 2
    with pytest.raises(ValueError, match="stale"):
        runtime.save_checkpoint(
            strategy.strategy_run_id, {"actions": [9]}, expected_version=first.version
        )
    runtime.close()
    versions.close()
