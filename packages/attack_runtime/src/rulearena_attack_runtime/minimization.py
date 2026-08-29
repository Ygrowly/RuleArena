from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from rulearena_oracle import InvariantId
from rulearena_reference_simulator import SimAction

from .models import MinimizationResult

Predicate = Callable[[tuple[SimAction, ...], InvariantId], Awaitable[bool]]


async def minimize_trace(
    actions: Sequence[SimAction], invariant_id: InvariantId, confirms: Predicate
) -> MinimizationResult:
    """Deletion-based delta debugging; every predicate call must use a fresh run."""
    current = tuple(actions)
    trials = 0
    trials += 1
    if not await confirms(current, invariant_id):
        raise ValueError("the original trace does not violate the target invariant")
    granularity = 2
    while len(current) >= 2:
        chunk_size = max(1, (len(current) + granularity - 1) // granularity)
        reduced = False
        for start in range(0, len(current), chunk_size):
            candidate = current[:start] + current[start + chunk_size :]
            trials += 1
            if await confirms(candidate, invariant_id):
                current = candidate
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if not reduced:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)
    index = 0
    while index < len(current):
        candidate = current[:index] + current[index + 1 :]
        trials += 1
        if await confirms(candidate, invariant_id):
            current = candidate
        else:
            index += 1
    one_minimal = True
    for index in range(len(current)):
        trials += 1
        candidate = current[:index] + current[index + 1 :]
        if await confirms(candidate, invariant_id):
            one_minimal = False
            break
    return MinimizationResult(
        invariant_id=invariant_id,
        original_length=len(actions),
        minimized_actions=current,
        trials=trials,
        one_minimal=one_minimal,
    )
