"""The agent's own vocabulary: what it is asked, and what it may say back.

Nothing here knows about gates, benchmarks, ground truth, or the environment's defect
axes. That is the point of the package boundary -- see `tests/refund/test_invariants.py`,
which fails if this package ever imports the gate.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from rulearena_domain_contracts import ActionType


class StrictModel(BaseModel):
    """The forbid-extra, frozen base every model in this workspace is built on."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Ticket(StrictModel):
    """What the agent is given: a customer request and who it belongs to.

    Deliberately just this. No expected outcome, no defect axes, no ground truth -- a
    ticket that carried the answer would measure nothing.
    """

    ticket_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)


class AgentAction(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_type: Literal["ACTION"]
    action_type: ActionType
    target_id: str | None = None
    arguments: dict[str, str | int | bool] = Field(default_factory=dict)
    reason: str = Field(max_length=500)


class AgentEscalate(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_type: Literal["ESCALATE"]
    reason: str = Field(max_length=500)


AgentDecision = Annotated[AgentAction | AgentEscalate, Field(discriminator="proposal_type")]
_DECISION_ADAPTER: TypeAdapter[AgentDecision] = TypeAdapter(AgentDecision)


def agent_decision_json_schema() -> dict[str, Any]:
    """The decision schema, as a model-backed agent would need it.

    v1 runs a deterministic policy and never calls a model, so nothing consumes this yet.
    It is exported because it is the exact contract a model-backed agent would have to
    satisfy, and having it here means that agent would not change the vocabulary.
    """
    return _DECISION_ADAPTER.json_schema()


def parse_agent_decision(payload: str) -> AgentDecision:
    return _DECISION_ADAPTER.validate_json(payload)
