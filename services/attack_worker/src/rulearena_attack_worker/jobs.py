from __future__ import annotations

from typing import Any, Protocol, TypedDict, cast

from rulearena_attack_runtime import AttackWorker
from rulearena_policy_schema import RuleSpec


class QueueJob(Protocol):
    job_id: str | None


class WorkerContext(TypedDict):
    runtime: AttackWorker
    job: QueueJob


async def execute_attack(
    context: dict[str, Any], run_id: str, rule_spec_json: dict[str, Any]
) -> None:
    """ARQ entrypoint with queue and Runtime idempotency gates."""

    runtime = cast(AttackWorker, context["runtime"])
    rule_spec = RuleSpec.model_validate(rule_spec_json)
    await runtime.run(run_id, rule_spec)
