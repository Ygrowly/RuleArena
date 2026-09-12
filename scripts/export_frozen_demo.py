"""Export one real confirmed run as the frozen golden demo.

Two modes, both running the production pipeline end to end: a real PostgreSQL-backed
Commerce Sandbox over HTTP, real receipts/snapshots/events, the deterministic Oracle,
and Delta minimization.

- default: strategy proposals come from a scripted FakeLLM, so the export is
  deterministic and free.
- ``--live``: the search is driven by the configured model, so the frozen demo shows a
  path the model actually proposed rather than one a script dictated. The payload
  records which mode produced it in ``provenance``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rulearena_attack_runtime import (
    AttackOutcome,
    AttackWorker,
    Budget,
    CompileStatus,
    FakeLLMAdapter,
    InMemoryRuntimeStore,
    OpenAICompatibleLLMAdapter,
    ReplayClassification,
    RuleCompiler,
    RuleVersionStore,
    SandboxReplayRunner,
    StrategyAgent,
    StrategyType,
    max_output_tokens_from_environment,
    proposal_json_schema,
    structured_response_format_enabled,
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
    replay: SandboxReplayRunner,
    spec: RuleSpec,
    actions: list[Any],
    sandbox_version: str,
    invariant: InvariantId = InvariantId.POINTS_VALUE_CONSERVATION,
) -> dict[str, Any]:
    result = await replay.replay(
        spec, tuple(actions), invariant, sandbox_version=sandbox_version
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
        "target_invariant": invariant.value,
        "actions": normalized,
        "snapshots": list(result.snapshots),
        "receipts": list(result.receipts),
        "events": list(result.events),
        # The Oracle's own reasoning, so the demo can show *why* the numbers constitute a
        # violation instead of leaving the reader to infer it from a diff.
        "findings": [
            {
                "invariant": finding.invariant_id.value,
                "status": finding.status.value,
                "explanation": finding.explanation,
                "evidence": finding.evidence,
            }
            for finding in result.report.findings
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Export the frozen golden demo run.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="drive the search with the configured model instead of a scripted FakeLLM",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=36,
        help="run step budget; the run budget is split across the three strategies, so "
        "this is three times the per-strategy room a search actually gets",
    )
    args = parser.parse_args()
    load_dotenv(override=False)

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
    # The run budget is split across the three strategies, so the scripted five-step walk
    # needs at least 5 x 3 steps to fit, and a live search wants more room still. A budget
    # that looks generous per run can still starve each strategy.
    budget = Budget(
        max_steps=args.steps, max_tokens=100000, max_cost=1.5, max_time_seconds=300
    )
    run = store.create_run(
        job_key="frozen-demo",
        rule_version_id=version.version_id,
        scenario_version_id="refund-points-v1",
        sandbox_version="vulnerable",
        oracle_version="1.0",
        budget=budget,
        random_seed=20260830,
    )
    stop = [proposal("STOP")]
    if args.live:

        def adapter_factory(prompt_version: str) -> OpenAICompatibleLLMAdapter:
            return OpenAICompatibleLLMAdapter(
                base_url=os.environ["LLM_BASE_URL"],
                api_key=os.environ["LLM_API_KEY"],
                model=os.environ["LLM_MODEL"],
                prompt_version=prompt_version,
                response_schema=proposal_json_schema(),
                schema_name="rulearena_agent_proposal",
                input_cost_per_million_tokens=float(
                    os.getenv("LLM_INPUT_COST_PER_MTOKEN", "0") or 0
                ),
                output_cost_per_million_tokens=float(
                    os.getenv("LLM_OUTPUT_COST_PER_MTOKEN", "0") or 0
                ),
                timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120") or 120),
                session_header=os.getenv("LLM_SESSION_HEADER") or None,
                use_response_format=structured_response_format_enabled(),
            )

        value_flow_agent = StrategyAgent(
            StrategyType.VALUE_FLOW,
            adapter_factory("value-flow-v1"),
            max_output_tokens=max_output_tokens_from_environment(),
        )
    else:
        value_flow_agent = StrategyAgent(
            StrategyType.VALUE_FLOW,
            FakeLLMAdapter(
                [
                    proposal(
                        "ACTION",
                        action_type="CREATE_USER",
                        arguments={"initial_balance": "500.00"},
                    ),
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
            ),
        )
    agents = {
        StrategyType.VALUE_FLOW: value_flow_agent,
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
        # Say what the search actually did rather than only that it failed, so a bad demo
        # export is diagnosable without re-running it.
        for event in store.events_after(run.run_id):
            if event.event_type == "STRATEGY_TERMINATED":
                print(f"  strategy: {json.dumps(event.data, ensure_ascii=False)}")
        print(f"  outcome: {completed.outcome}")
        raise RuntimeError(f"frozen demo run did not confirm: {completed.outcome}")
    counterexamples = store.counterexamples(run.run_id)
    if not counterexamples:
        raise RuntimeError("frozen demo run produced no counterexample")

    # One path can break several invariants at once; the demo narrates the refund/points
    # one when it is among them.
    primary = next(
        (
            item
            for item in counterexamples
            if item.invariant_id == InvariantId.POINTS_VALUE_CONSERVATION.value
        ),
        counterexamples[0],
    )
    invariant = InvariantId(primary.invariant_id)
    minimal_actions = deserialize_actions(primary.minimized_actions)
    vulnerable_evidence = await run_replay(
        replay, version.rule_spec, minimal_actions, "vulnerable", invariant
    )
    if vulnerable_evidence["classification"] != ReplayClassification.CONFIRMED_VIOLATION.value:
        raise RuntimeError("vulnerable replay must still confirm")
    fixed_evidence = await run_replay(
        replay, version.rule_spec, minimal_actions, "fixed", invariant
    )
    if fixed_evidence["classification"] == ReplayClassification.CONFIRMED_VIOLATION.value:
        raise RuntimeError("the fixed profile must not confirm the same path")

    honesty = (
        "真实运行：Commerce Sandbox 通过真实 HTTP API 重放，快照/回执/事件来自真实服务，"
        "Oracle 为确定性裁决，最小化使用 Delta Debugging。动作序列由真实模型 "
        f"{os.environ.get('LLM_MODEL', 'unknown')} 提出，模型只负责提议路径，"
        "是否构成违规由 Oracle 判定。"
        if args.live
        else "真实运行：Commerce Sandbox 通过真实 HTTP API 重放，快照/回执/事件来自真实服务，"
        "Oracle 为确定性裁决，最小化使用 Delta Debugging。策略动议由确定性脚本（FakeLLM）"
        "驱动，未调用真实模型。"
    )

    payload = {
        "provenance": {
            "generated_by": "scripts/export_frozen_demo.py",
            "search_mode": "live_model" if args.live else "scripted",
            "honesty": honesty,
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
