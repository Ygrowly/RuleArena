from __future__ import annotations

import json
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
    # ARQ serializes the payload as JSON (Decimal -> string), so the payload
    # must be validated in JSON mode, not Python mode.
    rule_spec = (
        RuleSpec.model_validate_json(json.dumps(rule_spec_json))
        if not isinstance(rule_spec_json, RuleSpec)
        else rule_spec_json
    )
    await runtime.run(run_id, rule_spec)
