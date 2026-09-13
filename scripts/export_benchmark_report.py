"""Export a reproducible benchmark report from persisted BenchmarkRuns.

Reads the latest completed runs per (suite, baseline) for one benchmark
version/seed/model tuple, recomputes nothing (metrics were recomputed from
raw runs at save time; this tool surfaces them with full Run ID provenance),
and emits `docs/benchmark-results.md`:

- development data includes per-case outcomes (public suite);
- hidden data is aggregated only (discovery rate / false positives), never
  per-case answers, so the public report cannot leak hidden expectations.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rulearena_evaluation import BaselineType, PostgresBenchmarkStore, Visibility

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "benchmark-results.md"
BASELINES = (
    BaselineType.RANDOM,
    BaselineType.BFS,
    BaselineType.SINGLE_AGENT,
    BaselineType.MULTI_STRATEGY,
)

RATE_KEYS = (
    "vulnerability_discovery_rate",
    "normal_confirmed_false_positive_rate",
    "candidate_confirmation_rate",
    "replay_stability_rate",
)


def _rate(metrics: dict[str, Any], key: str) -> str:
    value = metrics.get(key, {})
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not denominator:
        return "N/A"
    lower, upper = value.get("lower"), value.get("upper")
    if lower is None or upper is None:
        return f"{numerator}/{denominator}"
    # Carried into the published table so two point estimates are never read as a
    # difference the sample size cannot support.
    return f"{numerator}/{denominator} [{lower * 100:.0f}–{upper * 100:.0f}%]"


def _rate_pct(metrics: dict[str, Any], key: str) -> str:
    value = metrics.get(key, {})
    denominator = value.get("denominator")
    if not denominator:
        return "N/A"
    return f"{value.get('value') * 100:.0f}%"


def _summary(metrics: dict[str, Any], key: str) -> str:
    block = metrics.get(key, {})
    parts = []
    for stat in ("mean", "median", "p95"):
        entry = block.get(stat, {})
        value = entry.get("value")
        parts.append("N/A" if value is None else f"{value:.2f}")
    return " / ".join(parts)


def _latest_version(connection: Any) -> str:
    import sqlalchemy as sa

    row = connection.execute(
        sa.text(
            "SELECT benchmark_version FROM control.benchmark_run "
            "WHERE status = 'COMPLETED' ORDER BY started_at DESC LIMIT 1"
        )
    ).scalar_one_or_none()
    if row is None:
        raise SystemExit("no completed benchmark run to report on")
    return str(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-version",
        default=None,
        help="which suite version to report; defaults to the newest completed run's. "
        "Every cell comes from this version only -- a table that mixes versions "
        "compares numbers measured under different environments.",
    )
    args = parser.parse_args()

    load_dotenv()
    store = PostgresBenchmarkStore(os.environ["CONTROL_DATABASE_URL"])
    by_suite: dict[Visibility, dict[BaselineType, list[Any]]] = defaultdict(
        lambda: defaultdict(list)
    )
    try:
        # Walk completed runs and group by suite/baseline; the report covers the
        # most recent matching group per cell (same benchmark_version/seed/model
        # tuple is enforced by the runner at save time).
        connection = store.engine.connect()
        import sqlalchemy as sa

        version = args.benchmark_version or _latest_version(connection)
        rows = connection.execute(
            sa.text(
                "SELECT id, suite, baseline, benchmark_version, random_seed, repetitions "
                "FROM control.benchmark_run WHERE status = 'COMPLETED' "
                "AND benchmark_version = :version "
                "ORDER BY started_at DESC"
            ),
            {"version": version},
        ).mappings().all()
        seen_groups: set[tuple[str, str, str, int]] = set()
        for row in rows:
            group = (
                row["suite"],
                row["baseline"],
                row["benchmark_version"],
                int(row["random_seed"]),
            )
            if group in seen_groups:
                continue
            run = store.get(str(row["id"]))
            if run.raw_runs and any(
                item.case_id.startswith("dev-")
                for item in run.raw_runs
                if hasattr(item, "case_id")
            ) or run.suite is Visibility.DEVELOPMENT:
                suite = Visibility.DEVELOPMENT
            else:
                suite = Visibility.HIDDEN
            by_suite[suite][BaselineType(row["baseline"])].append(run)
            seen_groups.add(group)
            if len(seen_groups) >= 16:
                break
    finally:
        store.close()

    lines: list[str] = []
    lines.append(f"# Benchmark 实测报告（{version}）")
    lines.append("")
    lines.append(
        "> 数据全部来自 PostgreSQL 中 append-only 的原始 BenchmarkRun；"
        "每个聚合项可在 `control.benchmark_run` 中按 Run ID 复算。"
        "hidden suite 只输出聚合指标，不披露任何单 Case 期望答案。"
        f"本表**只取 {version}**：跨版本的 case 环境不同，数字不可混排。"
    )
    lines.append("")

    for suite in (Visibility.DEVELOPMENT, Visibility.HIDDEN):
        lines.append(f"## {suite.value} suite")
        lines.append("")
        lines.append(
            "| Baseline | reps | 发现率 | 误报 | 候选确认 | 重放稳定 | "
            "elapsed mean/med/p95 (s) | tokens mean | cost mean ($) |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for baseline in BASELINES:
            runs = by_suite.get(suite, {}).get(baseline, [])
            if not runs:
                lines.append(f"| {baseline.value} | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |")
                continue
            latest = runs[0]
            metrics = latest.metrics
            lines.append(
                f"| {baseline.value} | {latest.repetitions} "
                f"| {_rate(metrics, 'vulnerability_discovery_rate')} "
                f"({_rate_pct(metrics, 'vulnerability_discovery_rate')}) "
                f"| {_rate(metrics, 'normal_confirmed_false_positive_rate')} "
                f"| {_rate(metrics, 'candidate_confirmation_rate')} "
                f"| {_rate(metrics, 'replay_stability_rate')} "
                f"| {_summary(metrics, 'elapsed_seconds')} "
                f"| {metrics['tokens']['mean']['value']:.0f} "
                f"| {metrics['cost']['mean']['value']:.4f} |"
            )
        lines.append("")

        if suite is Visibility.DEVELOPMENT:
            lines.append("### development 每 Case 明细（公开 suite）")
            lines.append("")
            lines.append("| Case | Random | BFS | Single | Multi |")
            lines.append("| --- | --- | --- | --- | --- |")
            case_rows: dict[str, dict[str, str]] = defaultdict(dict)
            for baseline in BASELINES:
                runs = by_suite.get(suite, {}).get(baseline, [])
                if not runs:
                    continue
                for fact in runs[0].raw_runs:
                    outcome = "✅" if fact.confirmed_invariant_ids else "—"
                    if fact.failure_kind.value != "NONE":
                        outcome = "INFRA"
                    case_rows[fact.case_id][baseline.value] = outcome
            for case_id in sorted(case_rows):
                cells = case_rows[case_id]
                lines.append(
                    f"| {case_id} | {cells.get('RANDOM', '—')} | {cells.get('BFS', '—')} "
                    f"| {cells.get('SINGLE_AGENT', '—')} | {cells.get('MULTI_STRATEGY', '—')} |"
                )
            lines.append("")

        lines.append("### Run ID 溯源")
        lines.append("")
        for baseline in BASELINES:
            runs = by_suite.get(suite, {}).get(baseline, [])
            if runs:
                lines.append(f"- {baseline.value}: `{runs[0].benchmark_run_id}` "
                             f"(reps={runs[0].repetitions}, seed={runs[0].random_seed})")
        lines.append("")

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
