"""Historical P0 regression evidence recomputed from deterministic Oracle facts.

The Release Gate requires ``historical_p0_pass_rate == 1.0``. The rate is
recomputed here from the recorded P0 snapshots, never hand-written into a
benchmark result.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from rulearena_oracle import DeterministicOracle, InvariantId, OracleStatus
from rulearena_policy_schema import (
    Currency,
    Money,
    PointsRule,
    RefundRule,
    RuleSpec,
    ScenarioType,
)


class HistoricalP0Case(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    description: str
    invariant_id: InvariantId
    rule_spec: RuleSpec
    snapshots: tuple[dict[str, Any], ...]


def _money(amount: str) -> Money:
    return Money(currency=Currency.CNY, amount=Decimal(amount))


def _refund_points_rule_spec() -> RuleSpec:
    return RuleSpec(
        schema_version="1.0",
        scenario_type=ScenarioType.REFUND_POINTS,
        participants=(),
        assets=(),
        rules=(
            RefundRule(
                rule_type="REFUND",
                allow_partial_refund=True,
                maximum_refunds_per_order=2,
            ),
            PointsRule(
                rule_type="POINTS",
                spend_amount=_money("1.00"),
                points_granted=1,
                revoke_on_refund=True,
            ),
        ),
        invariants=(),
    )


def historical_p0_cases() -> tuple[HistoricalP0Case, ...]:
    state = {
        "users": [{"id": "u", "points_balance": 999}],
        "orders": [
            {
                "id": "o",
                "paid_amount": "100.00",
                "refunded_amount": "101.00",
                "points_granted": 999,
                "status": "PAID",
            }
        ],
        "coupons": [],
        "memberships": [],
        "entitlements": [],
    }
    return (
        HistoricalP0Case(
            finding_id="P0-refund-exceeds-paid",
            description=(
                "Historical P0: refunds must never exceed cumulative payment; the "
                "recorded over-refund snapshots must keep violating the invariant."
            ),
            invariant_id=InvariantId.REFUND_NOT_EXCEED_PAID,
            rule_spec=_refund_points_rule_spec(),
            snapshots=(
                {"state_hash": "before", "state": copy.deepcopy(state)},
                {"state_hash": "after", "state": state},
            ),
        ),
    )


def historical_p0_pass_rate(
    cases: tuple[HistoricalP0Case, ...] | None = None,
) -> float | None:
    """Fraction of historical P0 findings that still violate as recorded; None if empty."""
    if cases is None:
        cases = historical_p0_cases()
    if not cases:
        return None
    oracle = DeterministicOracle()
    passed = 0
    for case in cases:
        report = oracle.evaluate(case.rule_spec, snapshots=list(case.snapshots))
        if report.finding(case.invariant_id).status is OracleStatus.VIOLATED:
            passed += 1
    return passed / len(cases)
