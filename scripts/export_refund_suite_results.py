"""Export the whole refund comparison from the stored runs, for the console's tables.

`refund-gate-demo.json` carries three tickets in enough detail to step through. The
console's 对照评测 workspace needs the other thing: every ticket, both modes, every
repetition, as rows a reader can sort and filter.

Reads the newest completed run of each mode out of `control.refund_benchmark_run` --
the same append-only rows `refund-verify` re-checks -- and joins them with the suite for
the human labels. Nothing here recomputes a metric; the stored ones are carried through
so the page and the gate cannot disagree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rulearena_evaluation import (
    AgentMode,
    PostgresRefundBenchmarkStore,
    RefundReleaseGate,
    RefundSuiteLoader,
)
from rulearena_runtime_gate import GateDecisionType, GateReason

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "benchmarks" / "refund_agents" / "development-v1.json"
OUTPUT = ROOT / "frontend" / "public" / "frozen" / "refund-suite-results.json"

HONESTY = (
    "真实运行：每个数字都来自一次真实执行（真实 Sandbox HTTP、真实回执与快照、确定性 Oracle），"
    "本文件是那些运行的聚合与逐行记录，不是估算，也不是重新算过的一套口径——"
    "指标沿用运行落库时写入的值，门禁判定由 refund-verify 的同一套检查给出。"
)


def _row(fact: Any) -> dict[str, Any]:
    return {
        "run_id": fact.run_id,
        "case_id": fact.case_id,
        "mode": fact.mode.value,
        "repetition": fact.repetition,
        "status": fact.status.value,
        "agent_outcome": fact.agent_outcome.value if fact.agent_outcome else None,
        "claimed_complete": fact.claimed_complete,
        "escalated": fact.escalated,
        "final_state_satisfied": fact.final_state_satisfied,
        "invariants_satisfied": fact.invariants_satisfied,
        "violated_invariants": sorted(item.value for item in fact.violated_invariants),
        "loss_order_ids": list(fact.loss_order_ids),
        "loss_amount": fact.loss_amount,
        "tool_calls": fact.tool_calls,
        "refused_writes": fact.refused_writes,
        "gate_checks": fact.gate_checks,
        "elapsed_seconds": round(fact.usage.elapsed_seconds, 2),
        "sandbox_run_id": fact.sandbox_run_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the full refund comparison.")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    load_dotenv(override=False)

    url = args.database_url or os.environ.get("CONTROL_DATABASE_URL")
    if not url:
        raise SystemExit("CONTROL_DATABASE_URL is required")

    cases = RefundSuiteLoader(SUITE).load()
    case_by_id = {case.case_id: case for case in cases}
    store = PostgresRefundBenchmarkStore(url)
    try:
        runs = {}
        for mode in AgentMode:
            found = store.latest_completed(mode=mode)
            if found is None:
                raise SystemExit(f"no completed {mode.value} run to export")
            runs[mode] = found
    finally:
        store.close()

    gate = RefundReleaseGate().evaluate(runs)
    if not gate.passed:
        # Publishing a pair that fails its own gate would put the page above the check.
        raise SystemExit(f"the stored pair does not pass its gate: {gate.reasons}")

    payload = {
        "provenance": {
            "generated_by": "scripts/export_refund_suite_results.py",
            "honesty": HONESTY,
            "oracle_version": runs[AgentMode.BARE].versions.oracle_version,
            "benchmark_version": runs[AgentMode.BARE].versions.benchmark_version,
        },
        "suite": "benchmarks/refund_agents/development-v1.json",
        "gate": gate.model_dump(mode="json"),
        # The gate's own vocabulary, read off the package so the page cannot drift
        # from what the gate actually returns.
        "gate_vocabulary": {
            "decisions": [item.value for item in GateDecisionType],
            "reasons": [item.value for item in GateReason],
        },
        "tickets": [
            {
                "case_id": case.case_id,
                "ticket_text": case.ticket_text,
                "defect_axes": list(case.replay_defect_axes),
                "expects_escalation": case.expects_escalation,
                "construction_reason": case.construction_reason,
            }
            for case in cases
        ],
        "modes": {
            mode.value: {
                "benchmark_run_id": run.benchmark_run_id,
                "random_seed": run.random_seed,
                "repetitions": run.repetitions,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "versions": run.versions.model_dump(mode="json"),
                "metrics": run.metrics,
            }
            for mode, run in runs.items()
        },
        "rows": [
            _row(fact)
            for mode in (AgentMode.BARE, AgentMode.GATED)
            for fact in runs[mode].raw_runs
            if fact.case_id in case_by_id
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(payload['rows'])} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
