from __future__ import annotations

import copy
import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AttackStatus(StrEnum):
    DRAFT = "DRAFT"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    READY = "READY"
    SEARCHING = "SEARCHING"
    REPLAYING = "REPLAYING"
    RECOVERING = "RECOVERING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class AttackOutcome(StrEnum):
    CONFIRMED_VIOLATION = "CONFIRMED_VIOLATION"
    UNCONFIRMED_CANDIDATE = "UNCONFIRMED_CANDIDATE"
    NO_VIOLATION_WITHIN_BUDGET = "NO_VIOLATION_WITHIN_BUDGET"
    AMBIGUOUS_POLICY = "AMBIGUOUS_POLICY"
    UNSUPPORTED_RULE = "UNSUPPORTED_RULE"
    INFRA_FAILED = "INFRA_FAILED"
    CANCELLED = "CANCELLED"


class StrategyType(StrEnum):
    VALUE_FLOW = "VALUE_FLOW"
    LIFECYCLE = "LIFECYCLE"
    BOUNDARY = "BOUNDARY"


class StrategyStatus(StrEnum):
    PENDING = "PENDING"
    SEARCHING = "SEARCHING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(gt=0)
    max_tokens: int = Field(ge=0)
    max_cost: float = Field(ge=0)
    max_time_seconds: float = Field(gt=0)


class BudgetUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)

    def within(self, budget: Budget) -> bool:
        return (
            self.steps <= budget.max_steps
            and self.tokens <= budget.max_tokens
            and self.cost <= budget.max_cost
            and self.elapsed_seconds <= budget.max_time_seconds
        )


class AttackRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    job_key: str
    rule_version_id: str
    scenario_version_id: str
    sandbox_version: str
    oracle_version: str
    status: AttackStatus = AttackStatus.READY
    outcome: AttackOutcome | None = None
    budget: Budget
    random_seed: int
    created_at: datetime


class StrategyRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_run_id: str
    attack_run_id: str
    strategy_type: StrategyType
    status: StrategyStatus
    budget: Budget
    usage: BudgetUsage = BudgetUsage()


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_run_id: str
    version: int = Field(ge=1)
    state: dict[str, Any]
    created_at: datetime


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: int = Field(ge=1)
    run_id: str
    event_type: str
    data: dict[str, Any]
    created_at: datetime


class CounterexampleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    counterexample_id: str
    attack_run_id: str
    candidate_key: str
    invariant_id: str
    original_actions: tuple[dict[str, Any], ...]
    minimized_actions: tuple[dict[str, Any], ...]
    replay_run_id: str
    created_at: datetime


_ALLOWED_TRANSITIONS: dict[AttackStatus, frozenset[AttackStatus]] = {
    AttackStatus.DRAFT: frozenset({AttackStatus.NEEDS_CONFIRMATION, AttackStatus.READY}),
    AttackStatus.NEEDS_CONFIRMATION: frozenset({AttackStatus.READY}),
    AttackStatus.READY: frozenset(
        {AttackStatus.SEARCHING, AttackStatus.CANCEL_REQUESTED, AttackStatus.FAILED}
    ),
    AttackStatus.SEARCHING: frozenset(
        {
            AttackStatus.REPLAYING,
            AttackStatus.COMPLETED,
            AttackStatus.RECOVERING,
            AttackStatus.CANCEL_REQUESTED,
            AttackStatus.FAILED,
        }
    ),
    AttackStatus.REPLAYING: frozenset(
        {
            AttackStatus.SEARCHING,
            AttackStatus.COMPLETED,
            AttackStatus.RECOVERING,
            AttackStatus.CANCEL_REQUESTED,
            AttackStatus.FAILED,
        }
    ),
    AttackStatus.RECOVERING: frozenset(
        {
            AttackStatus.SEARCHING,
            AttackStatus.REPLAYING,
            AttackStatus.CANCEL_REQUESTED,
            AttackStatus.FAILED,
        }
    ),
    AttackStatus.CANCEL_REQUESTED: frozenset({AttackStatus.CANCELLED}),
    AttackStatus.CANCELLED: frozenset(),
    AttackStatus.FAILED: frozenset({AttackStatus.RECOVERING}),
    AttackStatus.COMPLETED: frozenset(),
}


def transition_allowed(current: AttackStatus, target: AttackStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


class RuntimeStore(Protocol):
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
    ) -> AttackRun: ...

    def get_run(self, run_id: str) -> AttackRun: ...

    def compare_and_set_status(
        self,
        run_id: str,
        expected: AttackStatus,
        target: AttackStatus,
        *,
        outcome: AttackOutcome | None = None,
    ) -> bool: ...

    def request_cancel(self, run_id: str) -> bool: ...

    def is_cancel_requested(self, run_id: str) -> bool: ...

    def ensure_strategy(
        self, attack_run_id: str, strategy_type: StrategyType, budget: Budget
    ) -> StrategyRun: ...

    def update_strategy(self, strategy: StrategyRun) -> None: ...

    def save_checkpoint(
        self, strategy_run_id: str, state: dict[str, Any], *, expected_version: int
    ) -> Checkpoint: ...

    def load_checkpoint(self, strategy_run_id: str) -> Checkpoint | None: ...

    def append_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> RuntimeEvent: ...

    def events_after(self, run_id: str, cursor: int = 0) -> tuple[RuntimeEvent, ...]: ...

    def save_counterexample(self, record: CounterexampleRecord) -> CounterexampleRecord: ...

    def counterexamples(self, run_id: str) -> tuple[CounterexampleRecord, ...]: ...


class InMemoryRuntimeStore:
    """Atomic reference store used by tests and the worker contract.

    The PostgreSQL implementation uses the same compare-and-set semantics; this store deliberately
    deep-copies values so strategy checkpoints cannot observe each other through object aliasing.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, AttackRun] = {}
        self._jobs: dict[str, str] = {}
        self._strategies: dict[str, StrategyRun] = {}
        self._strategy_keys: dict[tuple[str, StrategyType], str] = {}
        self._checkpoints: dict[str, Checkpoint] = {}
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._counterexamples: dict[tuple[str, str], CounterexampleRecord] = {}

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
        with self._lock:
            if job_key in self._jobs:
                return self._runs[self._jobs[job_key]]
            item = AttackRun(
                run_id=str(uuid4()),
                job_key=job_key,
                rule_version_id=rule_version_id,
                scenario_version_id=scenario_version_id,
                sandbox_version=sandbox_version,
                oracle_version=oracle_version,
                budget=budget,
                random_seed=random_seed,
                created_at=datetime.now(UTC),
            )
            self._runs[item.run_id] = item
            self._jobs[job_key] = item.run_id
            self._events[item.run_id] = []
            self.append_event(item.run_id, "RUN_CREATED", {"status": item.status.value})
            return item

    def get_run(self, run_id: str) -> AttackRun:
        with self._lock:
            return self._runs[run_id].model_copy(deep=True)

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
        if target in {AttackStatus.COMPLETED, AttackStatus.CANCELLED, AttackStatus.FAILED}:
            if outcome is None:
                raise ValueError("terminal status requires a deterministic outcome")
        elif outcome is not None:
            raise ValueError("outcome may only be assigned with a terminal status")
        with self._lock:
            current = self._runs[run_id]
            if current.status is not expected:
                return False
            self._runs[run_id] = current.model_copy(update={"status": target, "outcome": outcome})
            self.append_event(
                run_id,
                "STATUS_CHANGED",
                {"from": expected.value, "to": target.value, "outcome": outcome},
            )
            return True

    def request_cancel(self, run_id: str) -> bool:
        with self._lock:
            status = self._runs[run_id].status
        if status not in {AttackStatus.READY, AttackStatus.SEARCHING, AttackStatus.REPLAYING}:
            return False
        return self.compare_and_set_status(run_id, status, AttackStatus.CANCEL_REQUESTED)

    def is_cancel_requested(self, run_id: str) -> bool:
        return self.get_run(run_id).status is AttackStatus.CANCEL_REQUESTED

    def ensure_strategy(
        self, attack_run_id: str, strategy_type: StrategyType, budget: Budget
    ) -> StrategyRun:
        key = (attack_run_id, strategy_type)
        with self._lock:
            existing = self._strategy_keys.get(key)
            if existing:
                return self._strategies[existing].model_copy(deep=True)
            item = StrategyRun(
                strategy_run_id=str(uuid4()),
                attack_run_id=attack_run_id,
                strategy_type=strategy_type,
                status=StrategyStatus.PENDING,
                budget=budget,
            )
            self._strategies[item.strategy_run_id] = item
            self._strategy_keys[key] = item.strategy_run_id
            return item

    def update_strategy(self, strategy: StrategyRun) -> None:
        with self._lock:
            if strategy.strategy_run_id not in self._strategies:
                raise KeyError(strategy.strategy_run_id)
            self._strategies[strategy.strategy_run_id] = strategy.model_copy(deep=True)

    def save_checkpoint(
        self, strategy_run_id: str, state: dict[str, Any], *, expected_version: int
    ) -> Checkpoint:
        with self._lock:
            current = self._checkpoints.get(strategy_run_id)
            actual = current.version if current else 0
            if actual != expected_version:
                raise ValueError("stale checkpoint version")
            checkpoint = Checkpoint(
                strategy_run_id=strategy_run_id,
                version=actual + 1,
                state=copy.deepcopy(state),
                created_at=datetime.now(UTC),
            )
            self._checkpoints[strategy_run_id] = checkpoint
            return checkpoint.model_copy(deep=True)

    def load_checkpoint(self, strategy_run_id: str) -> Checkpoint | None:
        with self._lock:
            value = self._checkpoints.get(strategy_run_id)
            return value.model_copy(deep=True) if value else None

    def append_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> RuntimeEvent:
        with self._lock:
            values = self._events.setdefault(run_id, [])
            event = RuntimeEvent(
                cursor=len(values) + 1,
                run_id=run_id,
                event_type=event_type,
                data=copy.deepcopy(data),
                created_at=datetime.now(UTC),
            )
            values.append(event)
            return event.model_copy(deep=True)

    def events_after(self, run_id: str, cursor: int = 0) -> tuple[RuntimeEvent, ...]:
        with self._lock:
            return tuple(
                item.model_copy(deep=True)
                for item in self._events[run_id]
                if item.cursor > cursor
            )

    def save_counterexample(self, record: CounterexampleRecord) -> CounterexampleRecord:
        key = (record.attack_run_id, record.candidate_key)
        with self._lock:
            existing = self._counterexamples.get(key)
            if existing:
                return existing.model_copy(deep=True)
            self._counterexamples[key] = record.model_copy(deep=True)
            return record.model_copy(deep=True)

    def counterexamples(self, run_id: str) -> tuple[CounterexampleRecord, ...]:
        with self._lock:
            return tuple(
                value.model_copy(deep=True)
                for (attack_run_id, _), value in self._counterexamples.items()
                if attack_run_id == run_id
            )
