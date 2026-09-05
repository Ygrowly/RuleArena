from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from rulearena_policy_schema import ScenarioType

from .metrics import compute_metrics
from .models import (
    BaselineType,
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkStatus,
    RawCaseRun,
    VersionTuple,
    Visibility,
)
from .security import scan_ground_truth_leakage
from .store import BenchmarkStore


class CaseExecutor(Protocol):
    async def execute(
        self,
        case: BenchmarkCase,
        *,
        baseline: BaselineType,
        repetition: int,
        random_seed: int,
    ) -> RawCaseRun: ...


class BenchmarkRunner:
    def __init__(self, store: BenchmarkStore, executor: CaseExecutor) -> None:
        self.store = store
        self.executor = executor

    async def run(
        self,
        cases: tuple[BenchmarkCase, ...],
        *,
        versions: VersionTuple,
        baseline: BaselineType,
        repetitions: int,
        random_seed: int,
        historical_p0_pass_rate: float | None = None,
    ) -> BenchmarkRun:
        if not cases:
            raise ValueError("benchmark suite cannot be empty")
        if repetitions < 1:
            raise ValueError("repetitions must be positive")
        suite = cases[0].visibility
        budget = cases[0].budget
        if any(case.visibility is not suite for case in cases):
            raise ValueError("a BenchmarkRun cannot mix development and hidden cases")
        if any(case.benchmark_version != versions.benchmark_version for case in cases):
            raise ValueError("case benchmark version does not match run version")
        if any(case.budget != budget for case in cases):
            raise ValueError("all baselines must use one normalized case budget")
        if any(case.oracle_version != versions.oracle_version for case in cases):
            raise ValueError("case oracle version does not match run version")
        versions_by_scenario: dict[ScenarioType, tuple[str, str]] = {}
        for case in cases:
            identity = (case.rule_version_id, case.scenario_version_id)
            known = versions_by_scenario.setdefault(case.scenario_type, identity)
            if known != identity:
                raise ValueError(
                    "cases of one scenario type must share one rule and scenario version"
                )

        started = datetime.now(UTC)
        raw: list[RawCaseRun] = []
        for case in cases:
            for repetition in range(1, repetitions + 1):
                fact = await self.executor.execute(
                    case,
                    baseline=baseline,
                    repetition=repetition,
                    random_seed=random_seed + repetition - 1,
                )
                if (
                    fact.case_id != case.case_id
                    or fact.visibility is not suite
                    or fact.baseline is not baseline
                    or fact.repetition != repetition
                ):
                    raise ValueError("executor returned a fact for a different benchmark cell")
                raw.append(fact)
        leakage_findings = scan_ground_truth_leakage(
            [fact.model_dump(mode="json") for fact in raw], hidden_cases=cases
        )
        if leakage_findings:
            raise ValueError(
                "ground truth leakage detected in benchmark facts: "
                + "; ".join(leakage_findings)
            )
        result = BenchmarkRun(
            versions=versions,
            baseline=baseline,
            random_seed=random_seed,
            budget=budget,
            repetitions=repetitions,
            suite=Visibility(suite),
            status=BenchmarkStatus.COMPLETED,
            raw_runs=tuple(raw),
            metrics=compute_metrics(
                cases,
                raw,
                k=repetitions,
                leakage_findings=leakage_findings,
                historical_p0_pass_rate=historical_p0_pass_rate,
            ),
            started_at=started,
            finished_at=datetime.now(UTC),
        )
        self.store.save(result)
        return result
