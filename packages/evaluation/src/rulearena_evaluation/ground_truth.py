from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from rulearena_attack_runtime import ReplayClassification, SandboxReplayRunner
from rulearena_domain_contracts import ActionType
from rulearena_reference_simulator import SimAction

from .models import BenchmarkCase, ExpectedOutcome


class GroundTruthEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    replay_run_ids: tuple[str, ...]
    successful_replays: int
    attempted_replays: int


def parse_ground_truth_actions(case: BenchmarkCase) -> tuple[SimAction, ...]:
    actions: list[SimAction] = []
    for raw in case.ground_truth_actions:
        action_type = raw.get("action_type")
        arguments = raw.get("arguments", {})
        if not isinstance(action_type, str) or not isinstance(arguments, dict):
            raise ValueError("ground-truth actions must use the structured SimAction schema")
        if not all(isinstance(key, str) for key in arguments):
            raise ValueError("ground-truth argument names must be strings")
        actions.append(
            SimAction(
                action_type=ActionType(action_type),
                actor_id=str(raw.get("actor_id", "user-1")),
                target_id=(str(raw["target_id"]) if raw.get("target_id") else None),
                arguments=tuple(sorted(arguments.items())),
            )
        )
    return tuple(actions)


async def verify_ground_truth(
    case: BenchmarkCase,
    replay: SandboxReplayRunner,
    *,
    repetitions: int = 3,
) -> GroundTruthEvidence:
    if case.expected_outcome is not ExpectedOutcome.VULNERABLE:
        raise ValueError("only vulnerable cases have positive Ground Truth")
    if repetitions != 3:
        raise ValueError("new Ground Truth must be verified exactly 3 times")
    actions = parse_ground_truth_actions(case)
    run_ids: list[str] = []
    successes = 0
    for _ in range(repetitions):
        for invariant in sorted(case.expected_invariant_ids, key=lambda item: item.value):
            result = await replay.replay(
                case.rule_spec,
                actions,
                invariant,
                sandbox_version=case.sandbox_version,
            )
            run_ids.append(result.run_id)
            if result.classification is ReplayClassification.CONFIRMED_VIOLATION:
                successes += 1
    attempts = repetitions * len(case.expected_invariant_ids)
    if successes != attempts:
        raise AssertionError(
            f"Ground Truth replay failed for {case.case_id}: {successes}/{attempts}"
        )
    return GroundTruthEvidence(
        case_id=case.case_id,
        replay_run_ids=tuple(run_ids),
        successful_replays=successes,
        attempted_replays=attempts,
    )
