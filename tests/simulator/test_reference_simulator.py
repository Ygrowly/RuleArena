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
