from copy import deepcopy
from typing import Any

from rulearena_oracle import DeterministicOracle, InvariantId, OracleStatus
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


def snapshot(state: dict[str, Any], version: int = 1) -> dict[str, object]:
    return {"state_hash": f"hash-{version}", "snapshot_version": version, "state": state}


def base_state() -> dict[str, Any]:
    return {
        "users": [{"id": "u", "points_balance": 100}],
        "orders": [
            {
                "id": "o",
                "paid_amount": "100.00",
                "refunded_amount": "0.00",
                "points_granted": 100,
                "status": "PAID",
            }
        ],
        "coupons": [],
        "memberships": [],
        "entitlements": [],
    }


def test_oracle_emits_all_stable_invariant_ids_and_honest_statuses() -> None:
    report = DeterministicOracle().evaluate(
        rule_spec(ScenarioType.REFUND_POINTS), snapshots=[snapshot(base_state())]
    )
    assert {finding.invariant_id for finding in report.findings} == set(InvariantId)
    assert report.finding(InvariantId.NET_PAID_NON_NEGATIVE).status is OracleStatus.SATISFIED
    assert (
        report.finding(InvariantId.COUPON_SINGLE_CONSUMPTION).status is OracleStatus.NOT_APPLICABLE
    )
    assert (
        report.finding(InvariantId.ORDER_TERMINAL_MONOTONICITY).status
        is OracleStatus.INSUFFICIENT_EVIDENCE
    )


def test_oracle_detects_money_coupon_points_and_terminal_violations() -> None:
    state = base_state()
    order = state["orders"][0]
    assert isinstance(order, dict)
    order["refunded_amount"] = "101.00"
    order["points_granted"] = 999
    state["coupons"] = [{"id": "c", "usage_count": 2}]
    before = deepcopy(state)
    before_order = before["orders"][0]
    assert isinstance(before_order, dict)
    before_order["status"] = "REFUNDED"
    report = DeterministicOracle().evaluate(
        rule_spec(ScenarioType.REFUND_POINTS),
        snapshots=[snapshot(before, 1), snapshot(state, 2)],
        events=[
            {"event_id": "e1", "event_type": "COUPON_USED", "aggregate_id": "c"},
            {"event_id": "e2", "event_type": "COUPON_USED", "aggregate_id": "c"},
        ],
    )
    expected = {
        InvariantId.NET_PAID_NON_NEGATIVE,
        InvariantId.REFUND_NOT_EXCEED_PAID,
        InvariantId.COUPON_SINGLE_CONSUMPTION,
        InvariantId.POINTS_VALUE_CONSERVATION,
        InvariantId.ORDER_TERMINAL_MONOTONICITY,
    }
    assert expected <= {finding.invariant_id for finding in report.violated}


def test_oracle_detects_entitlement_and_idempotency_violations() -> None:
    state = base_state()
    state["orders"] = []
    state["memberships"] = [{"id": "m", "status": "REFUNDED"}]
    state["entitlements"] = [
        {
            "id": "e",
            "membership_id": "m",
            "granted_quantity": 2,
            "consumed_quantity": 2,
            "revoked_quantity": 1,
        }
    ]
    receipts = [
        {
            "receipt_id": "r1",
            "action_type": "CREATE_USER",
            "idempotency_key": "same",
            "status": "SUCCEEDED",
            "result": {"id": "u1"},
        },
        {
            "receipt_id": "r2",
            "action_type": "CREATE_USER",
            "idempotency_key": "same",
            "status": "SUCCEEDED",
            "result": {"id": "u2"},
        },
    ]
    events = [
        {"event_type": "USER_CREATED", "idempotency_key": "same"},
        {"event_type": "USER_CREATED", "idempotency_key": "same"},
    ]
    report = DeterministicOracle().evaluate(
        rule_spec(ScenarioType.MEMBERSHIP_ENTITLEMENT),
        snapshots=[snapshot(state)],
        receipts=receipts,
        events=events,
    )
    assert report.finding(InvariantId.ENTITLEMENT_NON_NEGATIVE).status is OracleStatus.VIOLATED
    assert (
        report.finding(InvariantId.ENTITLEMENT_REFUND_CONSISTENCY).status is OracleStatus.VIOLATED
    )
    assert report.finding(InvariantId.IDEMPOTENT_EFFECT).status is OracleStatus.VIOLATED


def test_each_invariant_has_satisfied_or_not_applicable_and_insufficient_cases() -> None:
    oracle = DeterministicOracle()
    state = base_state()
    state["coupons"] = [{"id": "c", "usage_count": 1}]
    receipts = [
        {
            "receipt_id": "r",
            "action_type": "PAY_ORDER",
            "idempotency_key": "once",
            "status": "SUCCEEDED",
            "result": {"order_id": "o"},
        }
    ]
    points_events = [
        {
            "event_type": "POINTS_GRANTED",
            "aggregate_id": "u",
            "payload": {"order_id": "o", "amount": 100},
            "idempotency_key": "once",
        }
    ]
    report = oracle.evaluate(
        rule_spec(ScenarioType.REFUND_POINTS),
        snapshots=[snapshot(deepcopy(state), 1), snapshot(state, 2)],
        receipts=receipts,
        events=points_events,
    )
    for invariant in (
        InvariantId.NET_PAID_NON_NEGATIVE,
        InvariantId.REFUND_NOT_EXCEED_PAID,
        InvariantId.COUPON_SINGLE_CONSUMPTION,
        InvariantId.POINTS_VALUE_CONSERVATION,
        InvariantId.ORDER_TERMINAL_MONOTONICITY,
        InvariantId.IDEMPOTENT_EFFECT,
    ):
        assert report.finding(invariant).status is OracleStatus.SATISFIED

    membership_state = base_state()
    membership_state["orders"] = []
    membership_state["memberships"] = [{"id": "m", "status": "ACTIVE"}]
    membership_state["entitlements"] = [
        {
            "id": "e",
            "membership_id": "m",
            "granted_quantity": 2,
            "consumed_quantity": 1,
            "revoked_quantity": 0,
        }
    ]
    membership_report = oracle.evaluate(
        rule_spec(ScenarioType.MEMBERSHIP_ENTITLEMENT),
        snapshots=[snapshot(membership_state)],
    )
    assert (
        membership_report.finding(InvariantId.ENTITLEMENT_NON_NEGATIVE).status
        is OracleStatus.SATISFIED
    )
    assert (
        membership_report.finding(InvariantId.ENTITLEMENT_REFUND_CONSISTENCY).status
        is OracleStatus.SATISFIED
    )

    for spec in (
        rule_spec(ScenarioType.PROMOTION),
        rule_spec(ScenarioType.REFUND_POINTS),
        rule_spec(ScenarioType.MEMBERSHIP_ENTITLEMENT),
    ):
        missing = oracle.evaluate(spec, snapshots=[])
        for finding in missing.findings:
            assert finding.status in {
                OracleStatus.INSUFFICIENT_EVIDENCE,
                OracleStatus.NOT_APPLICABLE,
            }


def test_membership_event_order_is_correlated_to_the_same_entitlement() -> None:
    state = base_state()
    state["orders"] = []
    state["memberships"] = [
        {"id": "m1", "status": "REFUNDED"},
        {"id": "m2", "status": "ACTIVE"},
    ]
    state["entitlements"] = [
        {
            "id": "e1",
            "membership_id": "m1",
            "granted_quantity": 1,
            "consumed_quantity": 0,
            "revoked_quantity": 1,
        },
        {
            "id": "e2",
            "membership_id": "m2",
            "granted_quantity": 2,
            "consumed_quantity": 1,
            "revoked_quantity": 0,
        },
    ]
    report = DeterministicOracle().evaluate(
        rule_spec(ScenarioType.MEMBERSHIP_ENTITLEMENT),
        snapshots=[snapshot(state)],
        events=[
            {
                "event_type": "MEMBERSHIP_REFUNDED",
                "aggregate_id": "m1",
                "payload": {"membership_id": "m1"},
            },
            {"event_type": "ENTITLEMENT_CONSUMED", "aggregate_id": "e2"},
        ],
    )
    assert (
        report.finding(InvariantId.ENTITLEMENT_REFUND_CONSISTENCY).status
        is OracleStatus.SATISFIED
    )


def test_malformed_numeric_evidence_is_insufficient_instead_of_crashing() -> None:
    state = base_state()
    state["coupons"] = [{"id": "c", "usage_count": "not-an-integer"}]
    report = DeterministicOracle().evaluate(
        rule_spec(ScenarioType.PROMOTION), snapshots=[snapshot(state)]
    )
    assert (
        report.finding(InvariantId.COUPON_SINGLE_CONSUMPTION).status
        is OracleStatus.INSUFFICIENT_EVIDENCE
    )
