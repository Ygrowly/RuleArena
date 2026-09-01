from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rulearena_attack_runtime import AttackOutcome, Budget, BudgetUsage
from rulearena_oracle import InvariantId
from rulearena_policy_schema import RuleSpec, ScenarioType


class Visibility(StrEnum):
    DEVELOPMENT = "development"
    HIDDEN = "hidden"


class ExpectedOutcome(StrEnum):
    VULNERABLE = "VULNERABLE"
    NORMAL = "NORMAL"


class BaselineType(StrEnum):
    RANDOM = "RANDOM"
    BFS = "BFS"
    SINGLE_AGENT = "SINGLE_AGENT"
    MULTI_STRATEGY = "MULTI_STRATEGY"


class BenchmarkStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FailureKind(StrEnum):
    NONE = "NONE"
    INFRA_FAILED = "INFRA_FAILED"
    CANCELLED = "CANCELLED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


class VersionTuple(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_version: str
    runtime_version: str
    rule_set_version: str
    scenario_set_version: str
    sandbox_version: str
    oracle_version: str
    model_config_hash: str
    prompt_version: str


class PublicCaseMetadata(BaseModel):
    """Safe metadata that may be shown for either suite."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    benchmark_version: str
    visibility: Visibility
    scenario_type: ScenarioType
    tags: tuple[str, ...]
    budget: Budget


class BenchmarkCase(PublicCaseMetadata):
    """Evaluation-only case; this model must never cross the Runtime/API boundary."""

    rule_version_id: str
    scenario_version_id: str
    sandbox_version: str
    oracle_version: str
    rule_spec: RuleSpec
    expected_outcome: ExpectedOutcome
    expected_invariant_ids: frozenset[InvariantId]
    construction_reason: str
    ground_truth_replays: tuple[bool, bool, bool]
    ground_truth_actions: tuple[dict[str, Any], ...]

    @model_validator(mode="after")
    def validate_ground_truth(self) -> BenchmarkCase:
        if self.expected_outcome is ExpectedOutcome.VULNERABLE:
            if not self.expected_invariant_ids or not self.ground_truth_actions:
                raise ValueError("vulnerable cases require invariant and action ground truth")
            if not all(self.ground_truth_replays):
                raise ValueError("vulnerable ground truth must replay successfully 3/3")
        elif self.expected_invariant_ids or self.ground_truth_actions:
            raise ValueError("normal cases cannot carry vulnerability ground truth")
        return self

    def public_metadata(self) -> PublicCaseMetadata:
        return PublicCaseMetadata.model_validate(
            self.model_dump(
                include={
                    "case_id",
                    "benchmark_version",
                    "visibility",
                    "scenario_type",
                    "tags",
                    "budget",
                }
            )
        )


class RawCaseRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    visibility: Visibility
    baseline: BaselineType
    repetition: int = Field(ge=1)
    attack_run_id: str | None = None
    outcome: AttackOutcome
    failure_kind: FailureKind = FailureKind.NONE
    confirmed_invariant_ids: frozenset[InvariantId] = frozenset()
    replayed_candidates: int = Field(default=0, ge=0)
    confirmed_candidates: int = Field(default=0, ge=0)
    replay_attempts: int = Field(default=0, ge=0)
    replay_successes: int = Field(default=0, ge=0)
    compile_attempted: bool = False
    rule_spec_schema_valid: bool | None = None
    usage: BudgetUsage = BudgetUsage()
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_failure(self) -> RawCaseRun:
        mapped = {
            AttackOutcome.INFRA_FAILED: FailureKind.INFRA_FAILED,
            AttackOutcome.CANCELLED: FailureKind.CANCELLED,
        }
        if self.outcome in mapped and self.failure_kind is not mapped[self.outcome]:
            raise ValueError("failure_kind must preserve infra/cancelled outcomes")
        if self.confirmed_candidates > self.replayed_candidates:
            raise ValueError("confirmed candidates cannot exceed replayed candidates")
        if self.replay_successes > self.replay_attempts:
            raise ValueError("replay successes cannot exceed attempts")
        return self


class BenchmarkRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_run_id: str = Field(default_factory=lambda: str(uuid4()))
    versions: VersionTuple
    baseline: BaselineType
    random_seed: int
    budget: Budget
    repetitions: int = Field(ge=1)
    suite: Visibility
    status: BenchmarkStatus
    raw_runs: tuple[RawCaseRun, ...]
    metrics: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    benchmark_run_id: str | None
    checks: dict[str, bool]
    reasons: tuple[str, ...]
