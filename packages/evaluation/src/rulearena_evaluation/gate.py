from __future__ import annotations

from rulearena_attack_runtime import Budget

from .models import (
    BaselineType,
    BenchmarkRun,
    BenchmarkStatus,
    GateResult,
    VersionTuple,
    Visibility,
)


def _metric(run: BenchmarkRun, key: str) -> tuple[float | None, int]:
    value = run.metrics.get(key, {})
    if not isinstance(value, dict):
        return None, 0
    raw = value.get("value")
    denominator = value.get("denominator")
    return (float(raw) if isinstance(raw, int | float) else None, int(denominator or 0))


class ReleaseGate:
    def evaluate(
        self,
        run: BenchmarkRun | None,
        *,
        expected_versions: VersionTuple,
        expected_budget: Budget,
        expected_seed: int,
    ) -> GateResult:
        if run is None:
            return GateResult(
                passed=False,
                benchmark_run_id=None,
                checks={"matching_benchmark": False},
                reasons=("no completed BenchmarkRun matches the release version tuple",),
            )
        false_positive, normal_count = _metric(
            run, "normal_confirmed_false_positive_rate"
        )
        discovery, vulnerable_count = _metric(run, "vulnerability_discovery_rate")
        stability, replay_count = _metric(run, "replay_stability_rate")
        checks = {
            "matching_versions": run.versions == expected_versions,
            "matching_budget": run.budget == expected_budget,
            "matching_seed": run.random_seed == expected_seed,
            "completed_hidden_multi": (
                run.status is BenchmarkStatus.COMPLETED
                and run.suite is Visibility.HIDDEN
                and run.baseline is BaselineType.MULTI_STRATEGY
            ),
            "normal_false_positive_zero": false_positive == 0 and normal_count > 0,
            "hidden_discovery_at_least_75_percent": (
                discovery is not None and discovery >= 0.75 and vulnerable_count > 0
            ),
            "counterexample_replay_3_of_3": (
                stability == 1 and replay_count >= 3
            ),
            "historical_p0_100_percent": run.metrics.get("historical_p0_pass_rate") == 1.0,
            "ground_truth_leakage_zero": run.metrics.get("ground_truth_leakage_count") == 0,
        }
        reasons = tuple(name for name, passed in checks.items() if not passed)
        return GateResult(
            passed=not reasons,
            benchmark_run_id=run.benchmark_run_id,
            checks=checks,
            reasons=reasons,
        )
