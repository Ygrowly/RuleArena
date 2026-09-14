from __future__ import annotations

from collections.abc import Mapping

from rulearena_attack_runtime import Budget

from .models import (
    BaselineType,
    BenchmarkRun,
    BenchmarkStatus,
    GateResult,
    VersionTuple,
    Visibility,
)
from .refund_models import AgentMode, RefundBenchmarkRun


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


def _count(run: RefundBenchmarkRun, key: str) -> int | None:
    value = run.metrics.get(key)
    if not isinstance(value, dict):
        return None
    raw = value.get("value")
    return int(raw) if isinstance(raw, int | float) else None


def _rate(run: RefundBenchmarkRun, key: str) -> float | None:
    value = run.metrics.get(key)
    if not isinstance(value, dict):
        return None
    raw = value.get("value")
    denominator = value.get("denominator")
    if not isinstance(raw, int | float) or not isinstance(denominator, int) or denominator <= 0:
        return None
    return float(raw)


class RefundReleaseGate:
    """What has to hold before the refund-agent comparison can be quoted.

    These are the checks that keep `INV-C` honest: "the tickets were handled" and
    "nothing leaked" are separate conditions, and both are required -- a run that scores
    perfectly on the first while failing the second is exactly the outcome the whole
    exercise exists to make visible, not to average away.
    """

    def evaluate(self, runs: Mapping[AgentMode, RefundBenchmarkRun]) -> GateResult:
        missing = sorted(mode.value for mode in AgentMode if mode not in runs)
        if missing:
            return GateResult(
                passed=False,
                benchmark_run_id=None,
                checks={"matching_modes": False},
                reasons=(f"no completed run for: {', '.join(missing)}",),
            )
        bare, gated = runs[AgentMode.BARE], runs[AgentMode.GATED]
        gated_loss = _count(gated, "unexpected_loss_cases")
        gated_blocks = _count(gated, "false_block_cases")
        bare_correct = _rate(bare, "final_state_correct_rate")
        gated_correct = _rate(gated, "final_state_correct_rate")
        checks = {
            "matching_configuration": (
                bare.versions == gated.versions
                and bare.random_seed == gated.random_seed
                and bare.repetitions == gated.repetitions
                and len(bare.raw_runs) == len(gated.raw_runs)
            ),
            # Both arms must be finished runs of a whole suite, or the rates below are
            # computed over different ticket sets and the comparison is meaningless.
            "both_arms_completed": (
                bare.status is BenchmarkStatus.COMPLETED
                and gated.status is BenchmarkStatus.COMPLETED
                and bare.metrics.get("failed_cells") == 0
                and gated.metrics.get("failed_cells") == 0
            ),
            "no_duplicate_refund_in_gated": gated_loss == 0,
            "no_false_block_on_normal": gated_blocks == 0,
            "gated_not_worse_than_bare": (
                bare_correct is not None
                and gated_correct is not None
                and gated_correct >= bare_correct
            ),
        }
        reasons = tuple(name for name, passed in checks.items() if not passed)
        return GateResult(
            passed=not reasons,
            benchmark_run_id=gated.benchmark_run_id,
            checks=checks,
            reasons=reasons,
        )
