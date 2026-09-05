from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import BenchmarkCase, ExpectedOutcome, FailureKind, RawCaseRun


class MetricValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float | None
    numerator: int | float | None = None
    denominator: int | None = Field(default=None, ge=0)
    source_run_ids: tuple[str, ...] = ()


def _ratio(numerator: int, denominator: int, run_ids: Sequence[str]) -> MetricValue:
    return MetricValue(
        value=(numerator / denominator if denominator else None),
        numerator=numerator,
        denominator=denominator,
        source_run_ids=tuple(run_ids),
    )


def pass_at_k(successes_by_case: Mapping[str, Sequence[bool]], k: int) -> MetricValue:
    if k <= 0:
        raise ValueError("k must be positive")
    eligible = [tuple(values[:k]) for values in successes_by_case.values() if len(values) >= k]
    return _ratio(sum(any(values) for values in eligible), len(eligible), ())


def pass_to_k(successes_by_case: Mapping[str, Sequence[bool]], k: int) -> MetricValue:
    """pass^k: fraction of cases where all first k independent runs succeed."""
    if k <= 0:
        raise ValueError("k must be positive")
    eligible = [tuple(values[:k]) for values in successes_by_case.values() if len(values) >= k]
    return _ratio(sum(all(values) for values in eligible), len(eligible), ())


def _summary(values: Sequence[float], run_ids: Sequence[str]) -> dict[str, MetricValue]:
    if not values:
        empty = MetricValue(value=None, denominator=0)
        return {"mean": empty, "median": empty, "p95": empty}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "mean": MetricValue(
            value=statistics.fmean(values), denominator=len(values), source_run_ids=tuple(run_ids)
        ),
        "median": MetricValue(
            value=statistics.median(values),
            denominator=len(values),
            source_run_ids=tuple(run_ids),
        ),
        "p95": MetricValue(
            value=ordered[p95_index], denominator=len(values), source_run_ids=tuple(run_ids)
        ),
    }


def compute_metrics(
    cases: Sequence[BenchmarkCase],
    raw_runs: Sequence[RawCaseRun],
    *,
    k: int = 1,
    leakage_findings: Sequence[str] = (),
    historical_p0_pass_rate: float | None = None,
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("case IDs must be unique")
    if any(run.case_id not in case_by_id for run in raw_runs):
        raise ValueError("raw run references an unknown case")

    eligible = [run for run in raw_runs if run.failure_kind is FailureKind.NONE]
    by_case: dict[str, list[RawCaseRun]] = defaultdict(list)
    for run in eligible:
        by_case[run.case_id].append(run)
    for runs in by_case.values():
        runs.sort(key=lambda item: item.repetition)

    vulnerable = [case for case in cases if case.expected_outcome is ExpectedOutcome.VULNERABLE]
    normal = [case for case in cases if case.expected_outcome is ExpectedOutcome.NORMAL]

    vulnerable_evaluable = [case for case in vulnerable if by_case[case.case_id]]
    normal_evaluable = [case for case in normal if by_case[case.case_id]]
    discovered: list[str] = []
    false_positive: list[str] = []
    success_groups: dict[str, list[bool]] = {}
    for case in vulnerable_evaluable:
        successes = [
            bool(run.confirmed_invariant_ids & case.expected_invariant_ids)
            for run in by_case[case.case_id]
        ]
        success_groups[case.case_id] = successes
        if any(successes):
            discovered.append(case.case_id)
    for case in normal_evaluable:
        if any(run.confirmed_invariant_ids for run in by_case[case.case_id]):
            false_positive.append(case.case_id)

    valid_ids = [run.run_id for run in eligible]
    compile_runs = [run for run in eligible if run.compile_attempted]
    schema_valid = sum(run.rule_spec_schema_valid is True for run in compile_runs)
    replayed = sum(run.replayed_candidates for run in eligible)
    confirmed = sum(run.confirmed_candidates for run in eligible)
    replay_attempts = sum(run.replay_attempts for run in eligible)
    replay_successes = sum(run.replay_successes for run in eligible)

    return {
        "rule_spec_schema_pass_rate": _ratio(
            schema_valid, len(compile_runs), [run.run_id for run in compile_runs]
        ).model_dump(mode="json"),
        "vulnerability_discovery_rate": _ratio(
            len(discovered),
            len(vulnerable_evaluable),
            [run.run_id for case in vulnerable_evaluable for run in by_case[case.case_id]],
        ).model_dump(mode="json"),
        "normal_confirmed_false_positive_rate": _ratio(
            len(false_positive),
            len(normal_evaluable),
            [run.run_id for case in normal_evaluable for run in by_case[case.case_id]],
        ).model_dump(mode="json"),
        "candidate_confirmation_rate": _ratio(
            confirmed, replayed, valid_ids
        ).model_dump(mode="json"),
        "replay_stability_rate": _ratio(
            replay_successes, replay_attempts, valid_ids
        ).model_dump(mode="json"),
        "elapsed_seconds": {
            key: value.model_dump(mode="json")
            for key, value in _summary(
                [run.usage.elapsed_seconds for run in eligible], valid_ids
            ).items()
        },
        "steps": {
            key: value.model_dump(mode="json")
            for key, value in _summary(
                [float(run.usage.steps) for run in eligible], valid_ids
            ).items()
        },
        "tokens": {
            key: value.model_dump(mode="json")
            for key, value in _summary(
                [float(run.usage.tokens) for run in eligible], valid_ids
            ).items()
        },
        "cost": {
            key: value.model_dump(mode="json")
            for key, value in _summary([run.usage.cost for run in eligible], valid_ids).items()
        },
        f"pass@{k}": pass_at_k(success_groups, k).model_dump(mode="json"),
        f"pass^{k}": pass_to_k(success_groups, k).model_dump(mode="json"),
        "ground_truth_leakage_count": len(leakage_findings),
        "historical_p0_pass_rate": historical_p0_pass_rate,
        "failure_counts": {
            kind.value: sum(run.failure_kind is kind for run in raw_runs)
            for kind in FailureKind
            if kind is not FailureKind.NONE
        },
        "evaluable_run_ids": valid_ids,
        "discovered_case_ids": discovered,
        "false_positive_case_ids": false_positive,
    }
