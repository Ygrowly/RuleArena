from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvariantId(StrEnum):
    NET_PAID_NON_NEGATIVE = "NET_PAID_NON_NEGATIVE"
    REFUND_NOT_EXCEED_PAID = "REFUND_NOT_EXCEED_PAID"
    COUPON_SINGLE_CONSUMPTION = "COUPON_SINGLE_CONSUMPTION"
    POINTS_VALUE_CONSERVATION = "POINTS_VALUE_CONSERVATION"
    ORDER_TERMINAL_MONOTONICITY = "ORDER_TERMINAL_MONOTONICITY"
    ENTITLEMENT_NON_NEGATIVE = "ENTITLEMENT_NON_NEGATIVE"
    ENTITLEMENT_REFUND_CONSISTENCY = "ENTITLEMENT_REFUND_CONSISTENCY"
    IDEMPOTENT_EFFECT = "IDEMPOTENT_EFFECT"


class OracleStatus(StrEnum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class OracleFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invariant_id: InvariantId
    status: OracleStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    before_hash: str | None = None
    after_hash: str | None = None
    related_action_ids: tuple[str, ...] = ()
    related_event_ids: tuple[str, ...] = ()
    explanation: str


class OracleReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: tuple[OracleFinding, ...]

    def finding(self, invariant_id: InvariantId) -> OracleFinding:
        return next(item for item in self.findings if item.invariant_id is invariant_id)

    @property
    def violated(self) -> tuple[OracleFinding, ...]:
        return tuple(item for item in self.findings if item.status is OracleStatus.VIOLATED)
