"""A benchmark case measures the defect it declares, and only that one.

The development suite used to hand every case in a scenario the same environment, able
to exhibit every defect that scenario has. A search path could therefore trip case B's
defect while being scored against case A's label -- which is how two Oracle-confirmed
violations were counted as misses in the golden-v3 run.

The checks below are the acceptance criterion for the fix, and the ones that matter run
against the real Sandbox rather than a mock: for each vulnerable case, its own Ground
Truth must confirm the invariant it is labelled with *in its declared environment*, must
NOT confirm it in a faithful environment, and must NOT confirm it under a sibling axis.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest
from rulearena_attack_runtime import ReplayClassification, SandboxReplayRunner
from rulearena_domain_contracts import AXES_BY_SCENARIO, TRANSPORT_DEFECT_AXES
from rulearena_evaluation.ground_truth import parse_ground_truth_actions
from rulearena_evaluation.loader import DevelopmentCaseLoader
from rulearena_evaluation.models import BenchmarkCase, ExpectedOutcome

ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT = ROOT / "benchmarks" / "development-v1.json"


def _development_cases() -> tuple[BenchmarkCase, ...]:
    return DevelopmentCaseLoader(DEVELOPMENT).load()


def _vulnerable(cases: Iterable[BenchmarkCase]) -> list[BenchmarkCase]:
    return [case for case in cases if case.expected_outcome is ExpectedOutcome.VULNERABLE]


def test_every_declared_axis_is_measured_by_some_case() -> None:
    """A defect axis with no case behind it is a capability nothing reports on.

    Business axes only. A transport axis (the acknowledgement is lost, the business fact
    is faithful) cannot be confirmed by any Oracle finding, so no search case can carry
    it; the refund-agent suite measures it by outcome instead and asserts that coverage
    itself -- see `tests/refund/test_refund_suite.py`.
    """
    covered = {axis for case in _development_cases() for axis in case.defect_axes}
    measured = set().union(*AXES_BY_SCENARIO.values()) - TRANSPORT_DEFECT_AXES
    assert measured - covered == set(), sorted(axis.value for axis in measured - covered)


def test_only_vulnerable_cases_declare_an_axis() -> None:
    for case in _development_cases():
        if case.expected_outcome is ExpectedOutcome.NORMAL:
            assert case.defect_axes == frozenset(), case.case_id
        else:
            assert case.defect_axes, case.case_id


@pytest.fixture
def replay(sandbox_http_url: str) -> SandboxReplayRunner:
    return SandboxReplayRunner(
        sandbox_http_url,
        os.getenv("SANDBOX_TEST_TOKEN", "local-internal-token-32-characters"),
        timeout=30.0,
    )


async def _confirms(
    replay: SandboxReplayRunner,
    case: BenchmarkCase,
    axes: Sequence[str] | None,
) -> set[str]:
    """Which of the case's expected invariants this environment actually violates."""
    actions = parse_ground_truth_actions(case)
    confirmed = set()
    for invariant in sorted(case.expected_invariant_ids, key=lambda item: item.value):
        result = await replay.replay(
            case.rule_spec,
            actions,
            invariant,
            sandbox_version=case.sandbox_version,
            defect_axes=axes,
        )
        if result.classification is ReplayClassification.CONFIRMED_VIOLATION:
            confirmed.add(invariant.value)
    return confirmed


@pytest.mark.sandbox
async def test_each_case_confirms_only_under_its_own_axis(
    replay: SandboxReplayRunner,
) -> None:
    cases = _vulnerable(_development_cases())
    assert cases, "the development suite has no vulnerable cases to check"

    expected = {
        case.case_id: {item.value for item in case.expected_invariant_ids} for case in cases
    }
    semaphore = asyncio.Semaphore(4)

    async def check(case: BenchmarkCase) -> tuple[str, dict[str, set[str]]]:
        declared = case.replay_defect_axes
        siblings = sorted(
            axis.value
            for axis in AXES_BY_SCENARIO[case.scenario_type]
            if axis.value not in (declared or ())
        )
        # The case's own Ground Truth, replayed against environments differing only in
        # which defects the implementation is allowed to exhibit.
        environments = [
            ("its declared axis", declared),
            ("a faithful environment", []),
            *((f"sibling axis {axis}", [axis]) for axis in siblings),
        ]
        observed: dict[str, set[str]] = {}
        async with semaphore:
            for label, axes in environments:
                observed[label] = await _confirms(replay, case, axes)
        return case.case_id, observed

    results = dict(await asyncio.gather(*(check(case) for case in cases)))
    failures: list[str] = []
    for case_id, observed in results.items():
        want = expected[case_id]
        if observed["its declared axis"] != want:
            failures.append(
                f"{case_id}: its own environment confirmed "
                f"{sorted(observed['its declared axis'])}, expected {sorted(want)}"
            )
        for label, confirmed in observed.items():
            if label != "its declared axis" and confirmed:
                failures.append(
                    f"{case_id}: {label} also confirmed {sorted(confirmed)} -- the case "
                    "would be scored against a defect it never meant to measure"
                )
    assert failures == [], "\n".join(failures)
