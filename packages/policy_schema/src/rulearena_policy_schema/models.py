from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Currency(StrEnum):
    CNY = "CNY"
    USD = "USD"


class ScenarioType(StrEnum):
    PROMOTION = "PROMOTION"
    REFUND_POINTS = "REFUND_POINTS"
    MEMBERSHIP_ENTITLEMENT = "MEMBERSHIP_ENTITLEMENT"


MoneyAmount = Annotated[
    Decimal,
    Field(strict=True, ge=Decimal("0"), max_digits=18, decimal_places=4),
]


class Money(StrictModel):
    currency: Currency
    amount: MoneyAmount


class Participant(StrictModel):
    participant_id: StrictStr
    kind: Literal["USER", "SYSTEM"]


class Asset(StrictModel):
    asset_id: StrictStr
    kind: Literal["BALANCE", "COUPON", "POINTS", "MEMBERSHIP", "ENTITLEMENT"]
    initial_money: Money | None = None
    initial_quantity: Annotated[int, Field(strict=True, ge=0)] | None = None


class PromotionRule(StrictModel):
    rule_type: Literal["PROMOTION"]
    minimum_order_amount: Money
    discount_amount: Money
    new_users_only: bool = False
    restore_on_full_refund: bool

    @model_validator(mode="after")
    def require_one_currency(self) -> PromotionRule:
        if self.minimum_order_amount.currency is not self.discount_amount.currency:
            raise ValueError("promotion monetary values must use one currency")
        return self


class RefundRule(StrictModel):
    rule_type: Literal["REFUND"]
    allow_partial_refund: bool
    maximum_refunds_per_order: Annotated[int, Field(strict=True, ge=1)]


class PointsRule(StrictModel):
    rule_type: Literal["POINTS"]
    spend_amount: Money
    points_granted: Annotated[int, Field(strict=True, ge=0)]
    revoke_on_refund: bool

    @model_validator(mode="after")
    def require_positive_spend_amount(self) -> PointsRule:
        if self.spend_amount.amount <= 0:
            raise ValueError("points spend_amount must be positive")
        return self


class MembershipRule(StrictModel):
    rule_type: Literal["MEMBERSHIP"]
    price: Money
    entitlement_quantity: Annotated[int, Field(strict=True, ge=1)]
    refund_policy: Literal["UNUSED_ONLY", "PRORATED", "NON_REFUNDABLE"]

    @model_validator(mode="after")
    def require_positive_price(self) -> MembershipRule:
        if self.price.amount <= 0:
            raise ValueError("membership price must be positive")
        return self


Rule = Annotated[
    PromotionRule | RefundRule | PointsRule | MembershipRule,
    Field(discriminator="rule_type"),
]


class Invariant(StrictModel):
    invariant_type: Literal[
        "REFUND_CONSERVATION",
        "REWARD_CONSERVATION",
        "COUPON_SINGLE_VALUE",
        "ENTITLEMENT_REFUND_CONSISTENCY",
        "IDEMPOTENT_EFFECT",
        "LEGAL_TRANSITION",
        "LEDGER_CONSISTENCY",
        "NON_NEGATIVE_ASSETS",
    ]
    invariant_id: StrictStr


class Ambiguity(StrictModel):
    ambiguity_id: StrictStr
    field_path: StrictStr
    question: StrictStr

    @field_validator("question")
    @classmethod
    def reject_code_like_questions(cls, value: str) -> str:
        lowered = value.casefold()
        forbidden = ("eval(", "exec(", "__import__", "select ", "insert ", "```", "${")
        if any(token in lowered for token in forbidden):
            raise ValueError("code, SQL, and template expressions are not allowed")
        return value


class RuleSpec(StrictModel):
    schema_version: Literal["1.0"]
    scenario_type: ScenarioType
    participants: tuple[Participant, ...]
    assets: tuple[Asset, ...]
    rules: tuple[Rule, ...]
    invariants: tuple[Invariant, ...]
    ambiguities: tuple[Ambiguity, ...] = ()

    @model_validator(mode="after")
    def require_rules_for_scenario(self) -> RuleSpec:
        allowed: dict[ScenarioType, tuple[type[StrictModel], ...]] = {
            ScenarioType.PROMOTION: (PromotionRule, RefundRule),
            ScenarioType.REFUND_POINTS: (RefundRule, PointsRule),
            ScenarioType.MEMBERSHIP_ENTITLEMENT: (MembershipRule,),
        }
        primary: dict[ScenarioType, type[StrictModel]] = {
            ScenarioType.PROMOTION: PromotionRule,
            ScenarioType.REFUND_POINTS: PointsRule,
            ScenarioType.MEMBERSHIP_ENTITLEMENT: MembershipRule,
        }
        if any(not isinstance(rule, allowed[self.scenario_type]) for rule in self.rules):
            raise ValueError("rule type does not belong to scenario_type")
        if not any(isinstance(rule, primary[self.scenario_type]) for rule in self.rules):
            raise ValueError("scenario_type requires its primary rule")
        rule_types = [rule.rule_type for rule in self.rules]
        if len(rule_types) != len(set(rule_types)):
            raise ValueError("duplicate rule_type values are not allowed")
        return self
