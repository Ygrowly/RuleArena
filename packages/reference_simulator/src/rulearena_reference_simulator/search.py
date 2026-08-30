from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from random import Random

from .models import SimAction, SimulationState, TransitionStatus
from .simulator import ReferenceSimulator


class SearchStatus(StrEnum):
    FOUND = "FOUND"
    EXHAUSTED = "EXHAUSTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


@dataclass(frozen=True)
class SearchBudget:
    max_depth: int = 8
    max_nodes: int = 1_000

    def __post_init__(self) -> None:
        if self.max_depth < 0 or self.max_nodes < 0:
            raise ValueError("search budgets must be non-negative")


@dataclass(frozen=True)
class SearchResult:
    status: SearchStatus
    trace: tuple[SimAction, ...]
    state: SimulationState
    nodes_expanded: int
    unique_states: int
    budget: SearchBudget
    seed: int | None = None


Goal = Callable[[SimulationState, tuple[SimAction, ...]], bool]
DEFAULT_BUDGET = SearchBudget()


def breadth_first_search(
    simulator: ReferenceSimulator, goal: Goal, budget: SearchBudget = DEFAULT_BUDGET
) -> SearchResult:
    initial = simulator.initial_state()
    queue: deque[tuple[SimulationState, tuple[SimAction, ...]]] = deque([(initial, ())])
    seen = {initial.state_hash()}
    expanded = 0
    last = initial
    while queue:
        state, trace = queue.popleft()
        last = state
        if goal(state, trace):
            return SearchResult(SearchStatus.FOUND, trace, state, expanded, len(seen), budget)
        if len(trace) >= budget.max_depth:
            continue
        if expanded >= budget.max_nodes:
            return SearchResult(
                SearchStatus.BUDGET_EXHAUSTED, trace, state, expanded, len(seen), budget
            )
        expanded += 1
        for action in simulator.legal_actions(state):
            result = simulator.transition(state, action)
            if result.status is not TransitionStatus.APPLIED:
                continue
            state_hash = result.state.state_hash()
            if state_hash not in seen:
                seen.add(state_hash)
                queue.append((result.state, trace + (action,)))
    return SearchResult(SearchStatus.EXHAUSTED, tuple(), last, expanded, len(seen), budget)


def seeded_random_search(
    simulator: ReferenceSimulator, goal: Goal, *, seed: int, budget: SearchBudget = DEFAULT_BUDGET
) -> SearchResult:
    rng = Random(seed)
    state = simulator.initial_state()
    trace: tuple[SimAction, ...] = ()
    seen = {state.state_hash()}
    if goal(state, trace):
        return SearchResult(SearchStatus.FOUND, trace, state, 0, 1, budget, seed)
    for expanded in range(budget.max_nodes):
        if goal(state, trace):
            return SearchResult(SearchStatus.FOUND, trace, state, expanded, len(seen), budget, seed)
        legal = simulator.legal_actions(state)
        if not legal or len(trace) >= budget.max_depth:
            state, trace = simulator.initial_state(), ()
            legal = simulator.legal_actions(state)
        if not legal:
            return SearchResult(
                SearchStatus.EXHAUSTED, trace, state, expanded, len(seen), budget, seed
            )
        action = legal[rng.randrange(len(legal))]
        result = simulator.transition(state, action)
        if result.status is TransitionStatus.APPLIED:
            state, trace = result.state, trace + (action,)
            seen.add(state.state_hash())
    return SearchResult(
        SearchStatus.BUDGET_EXHAUSTED,
        trace,
        state,
        budget.max_nodes,
        len(seen),
        budget,
        seed,
    )
