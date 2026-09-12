from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from rulearena_attack_runtime import AttackOutcome
from rulearena_policy_schema import ScenarioType

from .metrics import compute_metrics
from .models import (
    BaselineType,
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkStatus,
    FailureKind,
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
        concurrency: int = 1,
        resume: bool = False,
    ) -> BenchmarkRun:
        if not cases:
            raise ValueError("benchmark suite cannot be empty")
        if repetitions < 1:
            raise ValueError("repetitions must be positive")
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
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

        # A killed run keeps every case it already finished: the run row exists while it
        # is RUNNING and each case fact is appended the moment it completes, so a
        # multi-hour benchmark survives an interruption instead of losing everything.
        resumed = (
            self.store.running(versions=versions, baseline=baseline, suite=Visibility(suite))
            if resume
            else None
        )
        collected: dict[tuple[str, int], RawCaseRun] = {}
        if resumed is not None:
            run_id = resumed.benchmark_run_id
            started = resumed.started_at
            for fact in self.store.completed_cases(run_id):
                collected[(fact.case_id, fact.repetition)] = fact
        else:
            run_id = str(uuid4())
            self.store.start_run(
                BenchmarkRun(
                    benchmark_run_id=run_id,
                    versions=versions,
                    baseline=baseline,
                    random_seed=random_seed,
                    budget=budget,
                    repetitions=repetitions,
                    suite=Visibility(suite),
                    status=BenchmarkStatus.RUNNING,
                    raw_runs=(),
                    metrics={},
                    started_at=started,
                    finished_at=None,
                )
            )

        async def persist(fact: RawCaseRun) -> RawCaseRun:
            # Checked before the write, so a leaking fact is never made durable.
            findings = scan_ground_truth_leakage([fact.model_dump(mode="json")], hidden_cases=cases)
            if findings:
                raise ValueError(
                    "ground truth leakage detected in a benchmark fact: " + "; ".join(findings)
                )
            collected[(fact.case_id, fact.repetition)] = fact
            self.store.append_case(run_id, fact)
            return fact

        async def execute_cell(case: BenchmarkCase, repetition: int) -> RawCaseRun:
            print(
                f"[{baseline.value}] {case.case_id} rep{repetition} start",
                file=sys.stderr,
                flush=True,
            )
            try:
                fact = await self.executor.execute(
                    case,
                    baseline=baseline,
                    repetition=repetition,
                    random_seed=random_seed + repetition - 1,
                )
            except Exception as error:
                # One broken cell must not abort the whole benchmark; it is
                # accounted honestly as INFRA_FAILED (excluded from metrics
                # denominators) with its cause on stderr.
                print(
                    f"[{baseline.value}] {case.case_id} rep{repetition} INFRA_FAILED: "
                    f"{type(error).__name__}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
                fact = RawCaseRun(
                    case_id=case.case_id,
                    visibility=suite,
                    baseline=baseline,
                    repetition=repetition,
                    outcome=AttackOutcome.INFRA_FAILED,
                    failure_kind=FailureKind.INFRA_FAILED,
                )
            if (
                fact.case_id != case.case_id
                or fact.visibility is not suite
                or fact.baseline is not baseline
                or fact.repetition != repetition
            ):
                raise ValueError("executor returned a fact for a different benchmark cell")
            print(
                f"[{baseline.value}] {case.case_id} rep{repetition} done "
                f"outcome={fact.outcome.value} failure={fact.failure_kind.value} "
                f"steps={fact.usage.steps} elapsed={fact.usage.elapsed_seconds:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            return fact

        if concurrency <= 1:
            for case in cases:
                for repetition in range(1, repetitions + 1):
                    if (case.case_id, repetition) in collected:
                        continue
                    await persist(await execute_cell(case, repetition))
        else:
            semaphore = asyncio.Semaphore(concurrency)

            async def bounded(case: BenchmarkCase, repetition: int) -> RawCaseRun:
                async with semaphore:
                    return await persist(await execute_cell(case, repetition))

            await asyncio.gather(
                *(
                    bounded(case, repetition)
                    for case in cases
                    for repetition in range(1, repetitions + 1)
                    if (case.case_id, repetition) not in collected
                )
            )
        # Deterministic order regardless of completion order under concurrency.
        raw = [
            collected[(case.case_id, repetition)]
            for case in cases
            for repetition in range(1, repetitions + 1)
            if (case.case_id, repetition) in collected
        ]
        metrics = compute_metrics(
            cases,
            raw,
            k=repetitions,
            leakage_findings=(),
            historical_p0_pass_rate=historical_p0_pass_rate,
        )
        finished = datetime.now(UTC)
        self.store.finalize_run(
            run_id,
            status=BenchmarkStatus.COMPLETED,
            raw_runs=tuple(raw),
            metrics=metrics,
            finished_at=finished,
        )
        return BenchmarkRun(
            benchmark_run_id=run_id,
            versions=versions,
            baseline=baseline,
            random_seed=random_seed,
            budget=budget,
            repetitions=repetitions,
            suite=Visibility(suite),
            status=BenchmarkStatus.COMPLETED,
            raw_runs=tuple(raw),
            metrics=metrics,
            started_at=started,
            finished_at=finished,
        )

