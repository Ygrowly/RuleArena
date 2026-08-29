from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, NewType
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rulearena_policy_schema import Money

RunId = NewType("RunId", str)
ActorId = NewType("ActorId", str)
AssetId = NewType("AssetId", str)
IdempotencyKey = NewType("IdempotencyKey", str)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreateUser(StrictModel):
    action_type: Literal["CREATE_USER"]
    initial_balance: Money


class IssueCoupon(StrictModel):
    action_type: Literal["ISSUE_COUPON"]
    value: Money
    threshold: Money


class CreateOrder(StrictModel):
    action_type: Literal["CREATE_ORDER"]
    amount: Money


class ApplyCoupon(StrictModel):
    action_type: Literal["APPLY_COUPON"]
    order_id: AssetId
    coupon_id: AssetId


class PayOrder(StrictModel):
    action_type: Literal["PAY_ORDER"]
    order_id: AssetId


class CancelOrder(StrictModel):
    action_type: Literal["CANCEL_ORDER"]
    order_id: AssetId


class RefundOrder(StrictModel):
    action_type: Literal["REFUND_ORDER"]
    order_id: str
    amount: Money


class RedeemPoints(StrictModel):
    action_type: Literal["REDEEM_POINTS"]
    amount: Annotated[int, Field(strict=True, gt=0)]


class ActivateMembership(StrictModel):
    action_type: Literal["ACTIVATE_MEMBERSHIP"]
    paid_amount: Money
    quantity: Annotated[int, Field(strict=True, gt=0)]


class ConsumeEntitlement(StrictModel):
    action_type: Literal["CONSUME_ENTITLEMENT"]
    quantity: Annotated[int, Field(strict=True, gt=0)]


class CancelMembership(StrictModel):
    action_type: Literal["CANCEL_MEMBERSHIP"]
    refund_requested: bool


class InspectState(StrictModel):
    action_type: Literal["INSPECT_STATE"]
    scope: Literal["RUN", "USER", "ORDER", "MEMBERSHIP"]


type BusinessAction = Annotated[
    CreateUser
    | IssueCoupon
    | CreateOrder
    | ApplyCoupon
    | PayOrder
    | CancelOrder
    | RefundOrder
    | RedeemPoints
    | ActivateMembership
    | ConsumeEntitlement
    | CancelMembership
    | InspectState,
    Field(discriminator="action_type"),
]


class ActionRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: RunId
    actor_id: ActorId
    idempotency_key: IdempotencyKey | None = None
    action: BusinessAction

    @model_validator(mode="after")
    def require_key_for_writes(self) -> ActionRequest:
        if self.action.action_type != "INSPECT_STATE" and not self.idempotency_key:
            raise ValueError("idempotency_key is required for write actions")
        return self


class ActionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class ActionType(StrEnum):
    CREATE_USER = "CREATE_USER"
    ISSUE_COUPON = "ISSUE_COUPON"
    CREATE_ORDER = "CREATE_ORDER"
    APPLY_COUPON = "APPLY_COUPON"
    PAY_ORDER = "PAY_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"
    REFUND_ORDER = "REFUND_ORDER"
    REDEEM_POINTS = "REDEEM_POINTS"
    ACTIVATE_MEMBERSHIP = "ACTIVATE_MEMBERSHIP"
    CONSUME_ENTITLEMENT = "CONSUME_ENTITLEMENT"
    CANCEL_MEMBERSHIP = "CANCEL_MEMBERSHIP"
    INSPECT_STATE = "INSPECT_STATE"


class BusinessEventType(StrEnum):
    USER_CREATED = "USER_CREATED"
    COUPON_ISSUED = "COUPON_ISSUED"
    COUPON_RESERVED = "COUPON_RESERVED"
    COUPON_USED = "COUPON_USED"
    COUPON_RESTORED = "COUPON_RESTORED"
    ORDER_CREATED = "ORDER_CREATED"
    PAYMENT_CAPTURED = "PAYMENT_CAPTURED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    REFUND_ISSUED = "REFUND_ISSUED"
    POINTS_GRANTED = "POINTS_GRANTED"
    POINTS_REVOKED = "POINTS_REVOKED"
    POINTS_REDEEMED = "POINTS_REDEEMED"
    MEMBERSHIP_ACTIVATED = "MEMBERSHIP_ACTIVATED"
    ENTITLEMENT_GRANTED = "ENTITLEMENT_GRANTED"
    ENTITLEMENT_CONSUMED = "ENTITLEMENT_CONSUMED"
    ENTITLEMENT_REVOKED = "ENTITLEMENT_REVOKED"
    MEMBERSHIP_CANCELLED = "MEMBERSHIP_CANCELLED"
    MEMBERSHIP_REFUNDED = "MEMBERSHIP_REFUNDED"


class ApiError(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ActionReceipt(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    receipt_id: UUID
    run_id: RunId
    idempotency_key: IdempotencyKey | None
    action_type: ActionType
    status: ActionStatus
    monetary_effects: tuple[Money, ...] = ()
    result: dict[str, Any] = Field(default_factory=dict)
    error: ApiError | None = None
    occurred_at: datetime


class BusinessEvent(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    run_id: RunId
    aggregate_type: str
    aggregate_id: AssetId
    event_type: BusinessEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: IdempotencyKey | None = None
    occurred_at: datetime


class StateSnapshot(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: RunId
    snapshot_version: int = Field(strict=True, ge=0)
    state_hash: str
    state: dict[str, Any]
    captured_at: datetime
