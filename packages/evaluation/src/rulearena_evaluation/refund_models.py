"""The refund-agent suite: its tickets, its two modes, and what a run of one records.

Isomorphic to the search benchmark's models, and deliberately not the same types. It
answers a different question -- "did the agent do the task, and did anything leak money
on the way" -- over a different unit -- one ticket handled once, rather than one
invariant hunted for. Folding it into `BenchmarkCase` would have meant one table holding
two incompatible notions of "outcome", and the release gate reads the old one.

Two fields are worth explaining because they are load-bearing:

* `expects_escalation` is what makes "该升级的升级了没" checkable, and what separates a
  ticket the agent must not be blocked on from one it must hand off. The spec's case
  schema does not list it; without it neither of those two checks can be computed.
* `defect_axes` is the same closed vocabulary the search suite uses, so a ticket names
  the exact environment it is measuring rather than inheriting a scenario's whole set.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rulearena_attack_runtime import BudgetUsage
from rulearena_domain_contracts import DefectAxis, unreachable_axes
from rulearena_oracle import InvariantId
from rulearena_policy_schema import RuleSpec, ScenarioType
from rulearena_refund_agent import AgentOutcome

from .models import BenchmarkStatus, VersionTuple, Visibility


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentMode(StrEnum):
    """The one thing that differs between the two arms of the comparison.

    Same agent, same suite, same tickets. `GATED` puts a `RuntimeGate` behind the
    gateway; `BARE` does not, and a failure or a timeout is simply retried.
    """

    BARE = "BARE"
    GATED = "GATED"


class RefundTicketCase(StrictModel):
    case_id: str
    benchmark_version: str
    visibility: Visibility
    scenario_type: ScenarioType
    rule_version_id: str
    scenario_version_id: str
    # The user the ticket belongs to. Part of the case rather than the ticket text: the
    # agent is told who the request is for, and it must not have to guess.
    user_id: str = "user-1"
    ticket_text: str
    # Deterministic preconditions, replayed through the real Sandbox before the agent
    # starts: create the user, the order, pay for it.
    setup_actions: tuple[dict[str, Any], ...]
    defect_axes: frozenset[DefectAxis] = frozenset()
    # The authoritative facts a correct run ends with. Compared as a subset, matched by
    # `id`, so a case names only the fields it is measuring.
    expected_final_state: dict[str, Any]
    expected_invariants: frozenset[InvariantId]
    construction_reason: str
    expects_escalation: bool = False
    rule_spec: RuleSpec

    @model_validator(mode="after")
    def validate_case(self) -> RefundTicketCase:
        unreachable = unreachable_axes(self.scenario_type, self.defect_axes)
        if unreachable:
            names = ", ".join(sorted(axis.value for axis in unreachable))
            raise ValueError(f"the case's scenario cannot exhibit: {names}")
        if not self.expected_invariants:
            raise ValueError("a ticket must declare which invariants must hold afterwards")
        if not self.setup_actions:
            raise ValueError("a ticket must set up the state it is measured on")
        return self

    @property
    def replay_defect_axes(self) -> tuple[str, ...]:
        return tuple(sorted(axis.value for axis in self.defect_axes))


class RefundCaseRun(StrictModel):
    """One ticket, handled once, under one mode. Every number here is recomputable."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    mode: AgentMode
    repetition: int = Field(ge=1)
    sandbox_run_id: str | None = None
    # Absent only when the cell failed before the agent reached a decision.
    agent_outcome: AgentOutcome | None = None
    claimed_complete: bool
    escalated: bool
    escalation_reason: str | None = None
    # Did the authoritative state end up where the ticket said it should?
    final_state_satisfied: bool = False
    # Are all of the case's invariants SATISFIED on the authoritative record?
    invariants_satisfied: bool = False
    violated_invariants: frozenset[InvariantId] = frozenset()
    # Money that left the business that should not have, as the Oracle found it.
    loss_order_ids: tuple[str, ...] = ()
    loss_amount: str = "0"
    tool_calls: int = Field(default=0, ge=0)
    refused_writes: int = Field(default=0, ge=0)
    gate_checks: int = Field(default=0, ge=0)
    status: BenchmarkStatus = BenchmarkStatus.COMPLETED
    failure_reason: str | None = None
    usage: BudgetUsage = BudgetUsage()
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_coherence(self) -> RefundCaseRun:
        if self.claimed_complete and self.escalated:
            raise ValueError("a run cannot both claim completion and be escalated")
        if self.tool_calls < self.refused_writes:
            raise ValueError("refused writes cannot exceed tool calls")
        return self


class RefundBenchmarkRun(StrictModel):
    benchmark_run_id: str = Field(default_factory=lambda: str(uuid4()))
    versions: VersionTuple
    mode: AgentMode
    random_seed: int
    repetitions: int = Field(ge=1)
    suite: Visibility
    status: BenchmarkStatus
    raw_runs: tuple[RefundCaseRun, ...]
    metrics: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None


def expected_state_satisfied(expected: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    """Whether `state` carries every fact `expected` names, matched by `id`.

    A subset comparison, on purpose: a case states the fields it is measuring and stays
    silent about the rest, so adding a field to the Sandbox snapshot does not silently
    invalidate every expectation in the suite.
    """
    for collection, wanted in expected.items():
        actual = state.get(collection)
        if not isinstance(actual, list) or not isinstance(wanted, list):
            return False
        by_id = {
            str(item["id"]): item
            for item in actual
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        for want in wanted:
            if not isinstance(want, Mapping):
                return False
            item = by_id.get(str(want.get("id")))
            if item is None:
                return False
            if any(item.get(key) != value for key, value in want.items()):
                return False
    return True
