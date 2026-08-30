from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rulearena_policy_schema import ScenarioType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SandboxVersion(StrEnum):
    FIXED = "fixed"
    VULNERABLE = "vulnerable"


class ActionName(StrEnum):
    CREATE_USER = "create_user"
    ISSUE_COUPON = "issue_coupon"
    CREATE_ORDER = "create_order"
    APPLY_COUPON = "apply_coupon"
    PAY_ORDER = "pay_order"
    CANCEL_ORDER = "cancel_order"
    REFUND_ORDER = "refund_order"
    REDEEM_POINTS = "redeem_points"
    ACTIVATE_MEMBERSHIP = "activate_membership"
    CONSUME_ENTITLEMENT = "consume_entitlement"
    CANCEL_MEMBERSHIP = "cancel_membership"
    INSPECT_STATE = "inspect_state"


class Scope(StrEnum):
    RUN = "RUN"
    USER = "USER"
    ORDER = "ORDER"
    MEMBERSHIP = "MEMBERSHIP"


class CreateRunRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    scenario_type: ScenarioType
    sandbox_version: SandboxVersion = SandboxVersion.FIXED

    @model_validator(mode="before")
    @classmethod
    def normalize_profile_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        aliases = [name for name in ("sandbox_profile", "profile") if name in data]
        if aliases and "sandbox_version" in data:
            raise ValueError("provide only sandbox_version or one profile alias")
        if len(aliases) > 1:
            raise ValueError("provide only one profile alias")
        if aliases:
            data["sandbox_version"] = data.pop(aliases[0])
        return data


class ActionCommand(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    action: ActionName
    actor_id: str = Field(min_length=1, max_length=128)
    target_id: str | None = Field(default=None, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)

    def requires_idempotency_key(self) -> bool:
        return self.action is not ActionName.INSPECT_STATE


class RunResponse(StrictModel):
    run_id: str
    scenario_type: ScenarioType
    scenario_version: str
    snapshot: dict[str, Any]
