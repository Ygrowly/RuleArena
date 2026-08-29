from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from rulearena_domain_contracts import ActionType
from rulearena_policy_schema import Currency, ScenarioType


class TransitionStatus(StrEnum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class SimAction:
    action_type: ActionType
    actor_id: str = "user-1"
    target_id: str | None = None
    arguments: tuple[tuple[str, str | int | bool], ...] = ()
    idempotency_key: str | None = None

    @classmethod
    def build(
        cls,
        action_type: ActionType,
        *,
        actor_id: str = "user-1",
        target_id: str | None = None,
        idempotency_key: str | None = None,
        **arguments: str | int | bool,
    ) -> SimAction:
        return cls(
            action_type=action_type,
            actor_id=actor_id,
            target_id=target_id,
            arguments=tuple(sorted(arguments.items())),
            idempotency_key=idempotency_key,
        )

    def argument(self, name: str, default: Any = None) -> Any:
        return dict(self.arguments).get(name, default)

    def canonical_key(self) -> str:
        return json.dumps(self.to_http_payload(), sort_keys=True, separators=(",", ":"))

    def to_http_payload(self, *, key: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "action": self.action_type.value.lower(),
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "arguments": dict(self.arguments),
            "idempotency_key": key if key is not None else self.idempotency_key,
        }


@dataclass(frozen=True)
class SimUser:
    user_id: str
    balance: Decimal
    currency: Currency
    points_balance: int = 0
    membership_status: str = "NONE"
    is_new_user: bool = True


@dataclass(frozen=True)
class SimCoupon:
    coupon_id: str
    owner_id: str
    face_value: Decimal
    threshold: Decimal
    currency: Currency
    status: str = "AVAILABLE"
    reserved_order_id: str | None = None
    used_order_id: str | None = None
    usage_count: int = 0


@dataclass(frozen=True)
class SimOrder:
    order_id: str
    user_id: str
    original_amount: Decimal
    currency: Currency
    discount_amount: Decimal = Decimal("0")
    paid_amount: Decimal = Decimal("0")
    refunded_amount: Decimal = Decimal("0")
    points_granted: int = 0
    refund_count: int = 0
    status: str = "CREATED"
    coupon_id: str | None = None


@dataclass(frozen=True)
class SimMembership:
    membership_id: str
    user_id: str
    paid_amount: Decimal
    currency: Currency
    status: str = "ACTIVE"
    refunded_amount: Decimal = Decimal("0")


@dataclass(frozen=True)
class SimEntitlement:
    entitlement_id: str
    membership_id: str
    granted: int
    consumed: int = 0
    revoked: int = 0

    @property
    def available(self) -> int:
        return self.granted - self.consumed - self.revoked


@dataclass(frozen=True)
class SimEvent:
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: tuple[tuple[str, str | int | bool], ...] = ()
    idempotency_key: str | None = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple | list | frozenset):
        items = [_jsonable(item) for item in value]
        return (
            sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
            if isinstance(value, frozenset)
            else items
        )
    return value


@dataclass(frozen=True)
class SimulationState:
    scenario_type: ScenarioType
    users: tuple[SimUser, ...] = ()
    coupons: tuple[SimCoupon, ...] = ()
    orders: tuple[SimOrder, ...] = ()
    memberships: tuple[SimMembership, ...] = ()
    entitlements: tuple[SimEntitlement, ...] = ()
    seen_idempotency_keys: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    def normalized(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("seen_idempotency_keys", None)
        for collection in ("users", "coupons", "orders", "memberships", "entitlements"):
            data[collection] = sorted(data[collection], key=lambda item: next(iter(item.values())))
        normalized = _jsonable(data)
        if not isinstance(normalized, dict):
            raise TypeError("normalized state must be a dictionary")
        return normalized

    def state_hash(self) -> str:
        encoded = json.dumps(self.normalized(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class TransitionResult:
    status: TransitionStatus
    state: SimulationState
    events: tuple[SimEvent, ...] = ()
    code: str | None = None
    message: str | None = None
