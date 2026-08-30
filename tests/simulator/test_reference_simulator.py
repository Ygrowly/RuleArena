from rulearena_domain_contracts import ActionType
from rulearena_policy_schema import ScenarioType
from rulearena_reference_simulator import (
    ReferenceSimulator,
    SearchBudget,
    SearchStatus,
    SimAction,
    TransitionStatus,
    breadth_first_search,
    seeded_random_search,
)

from tests.phase2_factories import rule_spec


def apply_legal(simulator: ReferenceSimulator, steps: int) -> tuple[str, ...]:
    state = simulator.initial_state()
    hashes = [state.state_hash()]
    for _ in range(steps):
        legal = simulator.legal_actions(state)
        if not legal:
            break
        result = simulator.transition(state, legal[0])
        assert result.status is TransitionStatus.APPLIED
        state = result.state
        hashes.append(state.state_hash())
    return tuple(hashes)


def test_simulator_is_deterministic_and_hash_is_stable() -> None:
    first = ReferenceSimulator(rule_spec(ScenarioType.PROMOTION))
    second = ReferenceSimulator(rule_spec(ScenarioType.PROMOTION))
    assert apply_legal(first, 6) == apply_legal(second, 6)
    assert first.initial_state().normalized() == second.initial_state().normalized()


def test_invalid_and_unsupported_actions_are_explicit() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.PROMOTION))
    state = simulator.initial_state()
    rejected = simulator.transition(
        state,
        SimAction.build(ActionType.PAY_ORDER, target_id="missing"),
    )
    unsupported = simulator.transition(state, SimAction.build(ActionType.INSPECT_STATE))
    assert rejected.status is TransitionStatus.REJECTED
    assert rejected.state is state
    assert unsupported.status is TransitionStatus.UNSUPPORTED


def test_cross_scenario_action_is_unsupported() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.PROMOTION))
    result = simulator.transition(
        simulator.initial_state(),
        SimAction.build(ActionType.ACTIVATE_MEMBERSHIP, paid_amount="50.00", quantity=2),
    )
    assert result.status is TransitionStatus.UNSUPPORTED
    assert result.code == "ACTION_NOT_SUPPORTED"


def test_bfs_is_bounded_and_deduplicated() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.REFUND_POINTS))
    result = breadth_first_search(
        simulator,
        lambda state, trace: bool(state.orders and state.orders[0].status == "REFUNDED"),
        SearchBudget(max_depth=6, max_nodes=30),
    )
    assert result.status is SearchStatus.FOUND
    assert len(result.trace) <= 6
    assert result.unique_states <= result.nodes_expanded + 2


def test_seeded_random_search_repeats_exactly() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.MEMBERSHIP_ENTITLEMENT))
    budget = SearchBudget(max_depth=5, max_nodes=20)
    first = seeded_random_search(
        simulator, lambda state, trace: False, seed=20260829, budget=budget
    )
    second = seeded_random_search(
        simulator, lambda state, trace: False, seed=20260829, budget=budget
    )
    assert first.status is SearchStatus.BUDGET_EXHAUSTED
    assert first.seed == 20260829
    assert first.budget == budget
    assert first.trace == second.trace
    assert first.state.state_hash() == second.state.state_hash()


def test_random_search_checks_initial_state_even_with_zero_budget() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.PROMOTION))
    result = seeded_random_search(
        simulator,
        lambda state, trace: not state.users and not trace,
        seed=1,
        budget=SearchBudget(max_depth=0, max_nodes=0),
    )
    assert result.status is SearchStatus.FOUND
    assert result.nodes_expanded == 0


def test_state_hash_includes_idempotency_memory_that_changes_future_behavior() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.PROMOTION))
    initial = simulator.initial_state()
    action = SimAction.build(
        ActionType.CREATE_USER,
        initial_balance="10.00",
        idempotency_key="same-key",
    )
    applied = simulator.transition(initial, action)
    assert applied.status is TransitionStatus.APPLIED
    without_memory = applied.state.__class__(
        scenario_type=applied.state.scenario_type,
        users=applied.state.users,
        coupons=applied.state.coupons,
        orders=applied.state.orders,
        memberships=applied.state.memberships,
        entitlements=applied.state.entitlements,
    )
    assert applied.state.state_hash() != without_memory.state_hash()


def test_invalid_values_are_rejected_without_escaping_the_simulator() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.PROMOTION))
    result = simulator.transition(
        simulator.initial_state(),
        SimAction.build(ActionType.CREATE_USER, initial_balance="not-money"),
    )
    assert result.status is TransitionStatus.REJECTED
    assert result.code == "INVALID_ARGUMENT"


def test_partial_refund_revokes_points_proportionally() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.REFUND_POINTS))
    state = simulator.initial_state()
    for action in (
        SimAction.build(ActionType.CREATE_USER, initial_balance="200.00"),
        SimAction.build(ActionType.CREATE_ORDER, target_id="user-1", amount="100.00"),
        SimAction.build(ActionType.PAY_ORDER, target_id="order-1"),
        SimAction.build(ActionType.REFUND_ORDER, target_id="order-1", amount="50.00"),
    ):
        result = simulator.transition(state, action)
        assert result.status is TransitionStatus.APPLIED
        state = result.state
    assert state.users[0].points_balance == 50
    assert state.orders[0].points_revoked == 50


def test_unused_only_membership_refund_rejects_after_consumption() -> None:
    simulator = ReferenceSimulator(rule_spec(ScenarioType.MEMBERSHIP_ENTITLEMENT))
    state = simulator.initial_state()
    for action in (
        SimAction.build(ActionType.CREATE_USER, initial_balance="100.00"),
        SimAction.build(
            ActionType.ACTIVATE_MEMBERSHIP,
            target_id="user-1",
            paid_amount="50.00",
            quantity=2,
        ),
        SimAction.build(ActionType.CONSUME_ENTITLEMENT, target_id="entitlement-1", quantity=1),
    ):
        result = simulator.transition(state, action)
        assert result.status is TransitionStatus.APPLIED
        state = result.state
    rejected = simulator.transition(
        state,
        SimAction.build(
            ActionType.CANCEL_MEMBERSHIP,
            target_id="membership-1",
            refund_requested=True,
        ),
    )
    assert rejected.status is TransitionStatus.REJECTED
    assert rejected.code == "ENTITLEMENT_CONSUMED"
