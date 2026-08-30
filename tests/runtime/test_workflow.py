import pytest
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackStatus,
    Budget,
    InMemoryRuntimeStore,
    StrategyType,
)


def _run(store: InMemoryRuntimeStore) -> str:
    return store.create_run(
        job_key="job-1",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=Budget(max_steps=3, max_tokens=100, max_cost=1, max_time_seconds=10),
        random_seed=7,
    ).run_id


def test_job_strategy_checkpoint_and_event_cursor_are_idempotent() -> None:
    store = InMemoryRuntimeStore()
    run_id = _run(store)
    assert _run(store) == run_id
    budget = store.get_run(run_id).budget
    first = store.ensure_strategy(run_id, StrategyType.VALUE_FLOW, budget)
    assert store.ensure_strategy(run_id, StrategyType.VALUE_FLOW, budget) == first
    checkpoint = store.save_checkpoint(first.strategy_run_id, {"frontier": [1]}, expected_version=0)
    with pytest.raises(ValueError, match="stale"):
        store.save_checkpoint(first.strategy_run_id, {"frontier": [2]}, expected_version=0)
    assert store.load_checkpoint(first.strategy_run_id) == checkpoint
    cursor = store.events_after(run_id)[-1].cursor
    store.append_event(run_id, "PROGRESS", {"step": 1})
    assert [event.event_type for event in store.events_after(run_id, cursor)] == ["PROGRESS"]


def test_cas_prevents_old_worker_overwriting_terminal_state() -> None:
    store = InMemoryRuntimeStore()
    run_id = _run(store)
    assert store.compare_and_set_status(run_id, AttackStatus.READY, AttackStatus.SEARCHING)
    assert store.compare_and_set_status(
        run_id,
        AttackStatus.SEARCHING,
        AttackStatus.COMPLETED,
        outcome=AttackOutcome.NO_VIOLATION_WITHIN_BUDGET,
    )
    assert not store.compare_and_set_status(run_id, AttackStatus.READY, AttackStatus.SEARCHING)
    assert store.get_run(run_id).outcome is AttackOutcome.NO_VIOLATION_WITHIN_BUDGET


def test_cancel_is_explicit_and_outcome_is_separate() -> None:
    store = InMemoryRuntimeStore()
    run_id = _run(store)
    assert store.request_cancel(run_id)
    pending = store.get_run(run_id)
    assert pending.status is AttackStatus.CANCEL_REQUESTED
    assert pending.outcome is None
    assert store.compare_and_set_status(
        run_id,
        AttackStatus.CANCEL_REQUESTED,
        AttackStatus.CANCELLED,
        outcome=AttackOutcome.CANCELLED,
    )
