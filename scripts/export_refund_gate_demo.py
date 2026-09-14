"""Export one real BARE/GATED pair as the frozen refund-gate demo.

Same shape as `export_frozen_demo.py`, and the same rule: the payload is a real run
against the real Commerce Sandbox over HTTP, with real receipts, real snapshots, real
events, and the deterministic Oracle. `provenance.honesty` says exactly what drove it --
here, a deterministic agent with no model in the loop at all, which is the one thing a
reader of a "the agent retried and lost money" demo most needs to know.

Only a handful of tickets are exported. The headline numbers over the whole suite come
from `rulearena refund-bench`, and the export says so rather than presenting a
three-ticket subset as the measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rulearena_evaluation import (
    AgentMode,
    InMemoryRefundBenchmarkStore,
    RefundBenchmarkRun,
    RefundBenchmarkRunner,
    RefundCaseExecutor,
    RefundReleaseGate,
    RefundSuiteLoader,
    RefundTicketCase,
    VersionTuple,
    compute_refund_metrics,
    public_metric_summary,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "benchmarks" / "refund_agents" / "development-v1.json"
OUTPUT = ROOT / "frontend" / "public" / "frozen" / "refund-gate-demo.json"

# One ticket per failure surface, so the demo shows the mechanism rather than a rate:
# the acknowledgement loss (duplicated under the bare arm, recovered under the gated
# one), a ticket that must simply be handled, and one that must be handed off.
DEFAULT_TICKETS = ("rf-acklost-01", "rf-normal-full-01", "rf-mismatch-01")

VERSIONS = VersionTuple(
    benchmark_version="refund-v1",
    runtime_version="runtime-v1",
    rule_set_version="rules-v1",
    scenario_set_version="scenarios-v1",
    sandbox_version="sandbox-suite-v1",
    oracle_version="1.0",
    model_config_hash="0" * 64,
    prompt_version="refund-agent-v1",
)

HONESTY = (
    "真实运行：Commerce Sandbox 通过真实 HTTP API 调用，快照、回执与事件均来自真实服务，"
    "违规判定由确定性 Oracle 在权威状态上给出，门禁只做预算与回执的确定性算术。"
    "被测 Agent 为确定性策略（不调用任何模型）：它按工单执行退款，并在工具未给出结果时"
    "换一个新的幂等键重试——这是本项目要暴露的失败面本身，不是模拟出来的。"
)


def _subset(
    cases: tuple[RefundTicketCase, ...], ticket_ids: tuple[str, ...]
) -> tuple[RefundTicketCase, ...]:
    selected = [case for case in cases if case.case_id in ticket_ids]
    missing = sorted(set(ticket_ids) - {case.case_id for case in selected})
    if missing:
        raise SystemExit(f"unknown ticket ids: {', '.join(missing)}")
    return tuple(selected)


async def _run_mode(
    cases: tuple[RefundTicketCase, ...],
    executor: RefundCaseExecutor,
    mode: AgentMode,
) -> RefundBenchmarkRun:
    return await RefundBenchmarkRunner(InMemoryRefundBenchmarkStore(), executor).run(
        cases, versions=VERSIONS, mode=mode, repetitions=1, random_seed=20260831
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Export the frozen refund-gate demo.")
    parser.add_argument(
        "--tickets",
        default=",".join(DEFAULT_TICKETS),
        help="comma-separated ticket ids to run in both modes",
    )
    args = parser.parse_args()
    load_dotenv(override=False)

    ticket_ids = tuple(item.strip() for item in args.tickets.split(",") if item.strip())
    cases = _subset(RefundSuiteLoader(SUITE).load(), ticket_ids)
    executor = RefundCaseExecutor(
        os.environ["SANDBOX_HTTP_URL"], os.environ["INTERNAL_SERVICE_TOKEN"]
    )

    runs = {mode: await _run_mode(cases, executor, mode) for mode in AgentMode}
    gate = RefundReleaseGate().evaluate(runs)
    if not gate.passed:
        raise SystemExit(f"the exported pair does not pass its own gate: {gate.reasons}")

    payload = {
        "provenance": {
            "generated_by": "scripts/export_refund_gate_demo.py",
            "driver": "deterministic_agent_no_model",
            "honesty": HONESTY,
            "sandbox_version": "fixed + the ticket's declared defect axes",
            "oracle_version": "1.0",
            "scope": (
                "本文件是 " + str(len(cases)) + " 张工单的机制演示，不是全量指标；"
                "全量数字由 `rulearena refund-bench` 产出，并由 `rulearena refund-verify` 复核。"
            ),
        },
        "suite": "benchmarks/refund_agents/development-v1.json",
        "ticket_ids": list(ticket_ids),
        # The tickets as they were handed to the agent, so the page can show what was
        # asked for next to what happened. The expected end state travels with it: a
        # comparison against a standard is honest only if the standard is visible.
        "tickets": [
            {
                "case_id": case.case_id,
                "ticket_text": case.ticket_text,
                "defect_axes": list(case.replay_defect_axes),
                "expected_final_state": case.expected_final_state,
                "expected_invariants": sorted(
                    item.value for item in case.expected_invariants
                ),
                "construction_reason": case.construction_reason,
            }
            for case in cases
        ],
        "repetitions": 1,
        "gate": gate.model_dump(mode="json"),
        "modes": {
            mode.value: {
                "benchmark_run_id": run.benchmark_run_id,
                "versions": run.versions.model_dump(mode="json"),
                "metrics": public_metric_summary(
                    compute_refund_metrics(cases, run.raw_runs)
                ),
                "tickets": [fact.model_dump(mode="json") for fact in run.raw_runs],
            }
            for mode, run in runs.items()
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
