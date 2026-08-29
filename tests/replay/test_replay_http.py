import pytest
from rulearena_attack_runtime import ReplayClassification, SandboxReplayRunner
from rulearena_domain_contracts import ActionType
from rulearena_oracle import InvariantId
from rulearena_policy_schema import ScenarioType
from rulearena_reference_simulator import SimAction

from tests.phase2_factories import rule_spec


def vulnerable_cases() -> tuple[tuple[ScenarioType, InvariantId, tuple[SimAction, ...]], ...]:
    promotion = (
        SimAction.build(ActionType.CREATE_USER, initial_balance="500.00"),
        SimAction.build(
            ActionType.ISSUE_COUPON,
            target_id="user-1",
            value="50.00",
            threshold="100.00",
        ),
        SimAction.build(ActionType.CREATE_ORDER, target_id="user-1", amount="150.00"),
        SimAction.build(ActionType.APPLY_COUPON, target_id="order-1", coupon_id="coupon-1"),
        SimAction.build(ActionType.PAY_ORDER, target_id="order-1"),
        SimAction.build(ActionType.REFUND_ORDER, target_id="order-1", amount="60.00"),
        SimAction.build(ActionType.REFUND_ORDER, target_id="order-1", amount="100.00"),
    )
    points = (
        SimAction.build(ActionType.CREATE_USER, initial_balance="200.00"),
        SimAction.build(ActionType.CREATE_ORDER, target_id="user-1", amount="100.00"),
        SimAction.build(ActionType.PAY_ORDER, target_id="order-1"),
        SimAction.build(ActionType.REFUND_ORDER, target_id="order-1", amount="50.00"),
    )
    membership = (
        SimAction.build(ActionType.CREATE_USER, initial_balance="100.00"),
        SimAction.build(
            ActionType.ACTIVATE_MEMBERSHIP,
            target_id="user-1",
            paid_amount="50.00",
            quantity=2,
        ),
        SimAction.build(ActionType.CONSUME_ENTITLEMENT, target_id="entitlement-1", quantity=1),
        SimAction.build(
            ActionType.CANCEL_MEMBERSHIP,
            target_id="membership-1",
            refund_requested=True,
        ),
        SimAction.build(ActionType.CONSUME_ENTITLEMENT, target_id="entitlement-1", quantity=1),
    )
    return (
        (ScenarioType.PROMOTION, InvariantId.REFUND_NOT_EXCEED_PAID, promotion),
        (ScenarioType.REFUND_POINTS, InvariantId.POINTS_VALUE_CONSERVATION, points),
        (
            ScenarioType.MEMBERSHIP_ENTITLEMENT,
            InvariantId.ENTITLEMENT_REFUND_CONSISTENCY,
            membership,
        ),
    )


@pytest.mark.sandbox
async def test_real_replay_uses_three_fresh_runs_and_classifies_divergence(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    runner = SandboxReplayRunner(sandbox_http_url, sandbox_token)
    actions = (
        SimAction.build(
            ActionType.CREATE_USER,
            actor_id="replay-user",
            initial_balance="200.00",
        ),
        SimAction.build(
            ActionType.CREATE_ORDER,
            actor_id="replay-user",
            amount="100.00",
        ),
        SimAction.build(ActionType.PAY_ORDER, actor_id="replay-user", target_id="order-1"),
    )
    results = await runner.replay_repeated(
        rule_spec(ScenarioType.REFUND_POINTS),
        actions,
        InvariantId.NET_PAID_NON_NEGATIVE,
    )
    assert len({result.run_id for result in results}) == 3
    assert all(result.classification is ReplayClassification.MODEL_DIVERGENCE for result in results)
    assert all(len(result.snapshots) == len(actions) + 1 for result in results)


@pytest.mark.sandbox
async def test_three_vulnerable_cases_replay_three_of_three_with_equivalent_evidence(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    runner = SandboxReplayRunner(sandbox_http_url, sandbox_token)
    for scenario, invariant, actions in vulnerable_cases():
        results = await runner.replay_repeated(
            rule_spec(scenario),
            actions,
            invariant,
            sandbox_version="vulnerable",
        )
        assert all(
            result.classification is ReplayClassification.CONFIRMED_VIOLATION for result in results
        )
        assert len({result.run_id for result in results}) == 3
        assert len({result.snapshots[-1]["state_hash"] for result in results}) == 1
        assert len({repr(result.report.finding(invariant).evidence) for result in results}) == 1


@pytest.mark.sandbox
async def test_fixed_profiles_do_not_confirm_vulnerable_sequences(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    runner = SandboxReplayRunner(sandbox_http_url, sandbox_token)
    for scenario, invariant, actions in vulnerable_cases():
        result = await runner.replay(rule_spec(scenario), actions, invariant)
        assert result.classification is ReplayClassification.MODEL_DIVERGENCE


@pytest.mark.sandbox
async def test_real_minimization_is_one_minimal_on_fresh_runs(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    runner = SandboxReplayRunner(sandbox_http_url, sandbox_token)
    scenario, invariant, actions = vulnerable_cases()[0]
    result = await runner.minimize(
        rule_spec(scenario),
        actions,
        invariant,
        sandbox_version="vulnerable",
    )
    assert result.one_minimal
    assert len(result.minimized_actions) < result.original_length
    for index in range(len(result.minimized_actions)):
        candidate = result.minimized_actions[:index] + result.minimized_actions[index + 1 :]
        replay = await runner.replay(
            rule_spec(scenario),
            candidate,
            invariant,
            sandbox_version="vulnerable",
        )
        assert replay.classification is ReplayClassification.MODEL_DIVERGENCE
