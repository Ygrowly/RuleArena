from copy import deepcopy

from rulearena_oracle import DeterministicOracle, InvariantId, OracleStatus
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


def test_historical_p0_oracle_evidence_replays_three_of_three() -> None:
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
    snapshots = [
        {"state_hash": "before", "state": deepcopy(state)},
        {"state_hash": "after", "state": state},
    ]
    results = [
        DeterministicOracle()
        .evaluate(rule_spec(ScenarioType.REFUND_POINTS), snapshots=snapshots)
        .finding(InvariantId.REFUND_NOT_EXCEED_PAID)
        .status
        for _ in range(3)
    ]
    assert results == [OracleStatus.VIOLATED] * 3
