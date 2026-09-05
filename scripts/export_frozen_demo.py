"""Export one real confirmed run as the frozen golden demo.

The pipeline is the production one: a real PostgreSQL-backed Commerce Sandbox over
HTTP, real receipts/snapshots/events, the deterministic Oracle, and Delta
minimization. Strategy proposals are driven by a scripted FakeLLM so the export is
deterministic and free; the JSON records this honestly in `provenance`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from rulearena_attack_runtime import (
    AttackOutcome,
    AttackWorker,
    Budget,
    CompileStatus,
    FakeLLMAdapter,
    InMemoryRuntimeStore,
    ReplayClassification,
    RuleCompiler,
    RuleVersionStore,
    SandboxReplayRunner,
    StrategyAgent,
    StrategyType,
)
from rulearena_domain_contracts import ActionType
from rulearena_observability import InMemoryTraceStore
from rulearena_oracle import InvariantId
from rulearena_policy_schema import (
    Currency,
    Money,
    PointsRule,
    RefundRule,
    RuleSpec,
    ScenarioType,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "frontend" / "public" / "frozen" / "golden-run.json"

CHINESE_MODIFICATION = "每消费 1 元获得 1 积分，退款时按退款金额撤销积分。"


def rule_spec() -> RuleSpec:
    return RuleSpec(
        schema_version="1.0",
        scenario_type=ScenarioType.REFUND_POINTS,
        participants=(),
        assets=(),
        rules=(
            RefundRule(
                rule_type="REFUND",
                allow_partial_refund=True,
                maximum_refunds_per_order=2,
            ),
            PointsRule(
                rule_type="POINTS",
                spend_amount=Money(currency=Currency.CNY, amount=Decimal("1.00")),
                points_granted=1,
                revoke_on_refund=True,
            ),
        ),
        invariants=(),
    )


def proposal(
    proposal_type: str,
    *,
    action_type: str | None = None,
    target_id: str | None = None,
    arguments: dict[str, object] | None = None,
    candidate_invariant: str | None = None,
) -> str:
    value: dict[str, object] = {"proposal_type": proposal_type, "reason": "frozen demo script"}
    if action_type:
        value["action_type"] = action_type
        value["arguments"] = arguments or {}
    if target_id:
        value["target_id"] = target_id
    if candidate_invariant:
        value["candidate_invariant"] = candidate_invariant
    return json.dumps(value)


def deserialize_actions(serialized: tuple[dict[str, Any], ...]) -> list[Any]:
    from rulearena_reference_simulator import SimAction

    actions = []
    for item in serialized:
        arguments = {
            key: value
            for key, value in item.get("arguments", {}).items()
            if isinstance(value, str | int | bool)
        }
        actions.append(
            SimAction.build(
                ActionType(str(item["action_type"])),
                actor_id=str(item.get("actor_id", "user-1")),
                target_id=item.get("target_id"),
                idempotency_key=item.get("idempotency_key"),
                **arguments,
            )
        )
    return actions


async def run_replay(
    replay: SandboxReplayRunner, spec: RuleSpec, actions: list[Any], sandbox_version: str
) -> dict[str, Any]:
    result = await replay.replay(
        spec, tuple(actions), InvariantId.POINTS_VALUE_CONSERVATION, sandbox_version=sandbox_version
    )
    # Normalize the HTTP payload shape ({"action": "create_user"}) into the
    # UI contract ({"action_type": "CREATE_USER"}) so the frozen demo renders
    # exactly like live evidence.
    normalized = [
        {
            "action_type": str(action.get("action", "")).upper(),
            "actor_id": action.get("actor_id"),
            "target_id": action.get("target_id"),
            "idempotency_key": action.get("idempotency_key"),
            "arguments": action.get("arguments", {}),
        }
        for action in (action.to_http_payload() for action in result.actions)
    ]
    return {
        "classification": result.classification.value,
        "target_invariant": "POINTS_VALUE_CONSERVATION",
        "actions": normalized,
        "snapshots": list(result.snapshots),
        "receipts": list(result.receipts),
        "events": list(result.events),
    }


async def main() -> int:
    url = os.environ["SANDBOX_HTTP_URL"]
    token = os.environ["INTERNAL_SERVICE_TOKEN"]
    spec = rule_spec()
    compiled = await RuleCompiler(FakeLLMAdapter([spec.model_dump_json()])).compile(
        "refund-points", CHINESE_MODIFICATION
    )
    if compiled.status is not CompileStatus.COMPILED:
        raise RuntimeError("frozen demo rule must compile cleanly")
    version = RuleVersionStore().confirm("frozen-demo-policy", compiled)
    store = InMemoryRuntimeStore()
    run = store.create_run(
        job_key="frozen-demo",
        rule_version_id=version.version_id,
        scenario_version_id="refund-points-v1",
        sandbox_version="vulnerable",
        oracle_version="1.0",
        budget=Budget(max_steps=8, max_tokens=1000, max_cost=1, max_time_seconds=30),
        random_seed=20260830,
    )
    value_flow = [
        proposal("ACTION", action_type="CREATE_USER", arguments={"initial_balance": "500.00"}),
        proposal(
            "ACTION",
            action_type="CREATE_ORDER",
            target_id="user-1",
            arguments={"amount": "100.00"},
        ),
        proposal("ACTION", action_type="PAY_ORDER", target_id="order-1"),
        proposal(
            "ACTION",
            action_type="REFUND_ORDER",
            target_id="order-1",
            arguments={"amount": "50.00"},
        ),
        proposal("STOP", candidate_invariant="POINTS_VALUE_CONSERVATION"),
    ]
    stop = [proposal("STOP")]
    agents = {
        StrategyType.VALUE_FLOW: StrategyAgent(
            StrategyType.VALUE_FLOW, FakeLLMAdapter(value_flow)
        ),
        StrategyType.LIFECYCLE: StrategyAgent(StrategyType.LIFECYCLE, FakeLLMAdapter(stop.copy())),
        StrategyType.BOUNDARY: StrategyAgent(StrategyType.BOUNDARY, FakeLLMAdapter(stop.copy())),
    }
    replay = SandboxReplayRunner(url, token)
    trace = InMemoryTraceStore()
    await AttackWorker(store, replay, agents, trace_sink=trace).run(
        run.run_id, version.rule_spec
    )
    completed = store.get_run(run.run_id)
    if completed.outcome is not AttackOutcome.CONFIRMED_VIOLATION:
        raise RuntimeError(f"frozen demo run did not confirm: {completed.outcome}")
    counterexamples = store.counterexamples(run.run_id)
    if len(counterexamples) != 1:
        raise RuntimeError("frozen demo expects exactly one counterexample")

    minimal_actions = deserialize_actions(counterexamples[0].minimized_actions)
    vulnerable_evidence = await run_replay(replay, version.rule_spec, minimal_actions, "vulnerable")
    if vulnerable_evidence["classification"] != ReplayClassification.CONFIRMED_VIOLATION.value:
        raise RuntimeError("vulnerable replay must still confirm")
    fixed_evidence = await run_replay(replay, version.rule_spec, minimal_actions, "fixed")

    payload = {
        "provenance": {
            "generated_by": "scripts/export_frozen_demo.py",
            "honesty": (
                "真实运行：Commerce Sandbox 通过真实 HTTP API 重放，快照/回执/事件来自真实服务，"
                "Oracle 为确定性裁决，最小化使用 Delta Debugging。策略动议由确定性脚本（FakeLLM）"
                "驱动，未调用真实模型。"
            ),
            "sandbox_versions": ["vulnerable", "fixed"],
            "oracle_version": "1.0",
        },
        "rule": {
            "template_id": "refund-points",
            "chinese_modification": CHINESE_MODIFICATION,
            "version_id": version.version_id,
            "rule_spec": version.rule_spec.model_dump(mode="json"),
        },
        "run": completed.model_dump(mode="json"),
        "counterexamples": [item.model_dump(mode="json") for item in counterexamples],
        "evidence": {"vulnerable": vulnerable_evidence, "fixed_regression": fixed_evidence},
        "trace": [item.model_dump(mode="json") for item in trace.traces_for_run(run.run_id)],
        "events": [item.model_dump(mode="json") for item in store.events_after(run.run_id)],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
