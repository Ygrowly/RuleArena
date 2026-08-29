from decimal import Decimal

from rulearena_policy_schema import (
    Currency,
    MembershipRule,
    Money,
    PointsRule,
    PromotionRule,
    RefundRule,
    RuleSpec,
    ScenarioType,
)


def money(amount: str) -> Money:
    return Money(currency=Currency.CNY, amount=Decimal(amount))


def rule_spec(scenario_type: ScenarioType) -> RuleSpec:
    rules: tuple[PromotionRule | RefundRule | PointsRule | MembershipRule, ...]
    if scenario_type is ScenarioType.PROMOTION:
        rules = (
            PromotionRule(
                rule_type="PROMOTION",
                minimum_order_amount=money("150.00"),
                discount_amount=money("50.00"),
                restore_on_full_refund=False,
            ),
            RefundRule(rule_type="REFUND", allow_partial_refund=True, maximum_refunds_per_order=2),
        )
    elif scenario_type is ScenarioType.REFUND_POINTS:
        rules = (
            RefundRule(rule_type="REFUND", allow_partial_refund=True, maximum_refunds_per_order=2),
            PointsRule(
                rule_type="POINTS",
                spend_amount=money("1.00"),
                points_granted=1,
                revoke_on_refund=True,
            ),
        )
    else:
        rules = (
            MembershipRule(
                rule_type="MEMBERSHIP",
                price=money("50.00"),
                entitlement_quantity=2,
                refund_policy="UNUSED_ONLY",
            ),
        )
    return RuleSpec(
        schema_version="1.0",
        scenario_type=scenario_type,
        participants=(),
        assets=(),
        rules=rules,
        invariants=(),
    )
