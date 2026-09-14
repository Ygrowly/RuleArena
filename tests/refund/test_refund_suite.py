"""D4 acceptance: the two arms differ, and they differ for the stated reason.

The checks that matter here run against the real Sandbox, real receipts, and the real
Oracle -- the same reason the search suite's acceptance does. A duplicate refund proved
against a mock proves nothing about whether the money moved.

The whole suite is run once per mode. That is slower than one ticket per mode and much
faster than the three repetitions the headline numbers use; it is enough to establish
the four claims, and the numbers that get quoted are produced by `refund-bench` and
re-checked by `refund-verify`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from rulearena_domain_contracts import AXES_BY_SCENARIO, TRANSPORT_DEFECT_AXES
from rulearena_evaluation import (
    AgentMode,
    InMemoryRefundBenchmarkStore,
    RefundBenchmarkRunner,
    RefundCaseExecutor,
    RefundReleaseGate,
    RefundSuiteLoader,
    RefundTicketCase,
    VersionTuple,
)

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "benchmarks" / "refund_agents" / "development-v1.json"

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


def _cases() -> tuple[RefundTicketCase, ...]:
    return RefundSuiteLoader(SUITE).load()


# --- the suite's own shape (no Sandbox needed) --------------------------------


def test_the_suite_loads_with_the_declared_number_of_tickets() -> None:
    assert len(_cases()) == 15


def test_a_transport_axis_is_measured_by_this_suite() -> None:
    """The mirror of the search suite's coverage check.

    `REFUND_ACK_LOST` cannot be confirmed by any Oracle finding -- the business facts
    under it are faithful -- so it is excluded from that suite's business-axis coverage
    check. The property still has to hold somewhere, and this is where.
    """
    covered = {axis for case in _cases() for axis in case.defect_axes}
    assert TRANSPORT_DEFECT_AXES <= covered


def test_only_axes_their_scenario_can_exhibit_are_declared() -> None:
    for case in _cases():
        assert case.defect_axes <= AXES_BY_SCENARIO[case.scenario_type], case.case_id


def test_expectations_agree_with_the_escalation_flag() -> None:
    for case in _cases():
        refunds = [
            order
            for order in case.expected_final_state.get("orders", [])
            if order.get("refunded_amount") not in (None, "0.00")
        ]
        if case.expects_escalation:
            assert not refunds, f"{case.case_id} expects a handoff and a refund"
        else:
            assert refunds, f"{case.case_id} expects a refund and none is written down"


def test_the_headline_failure_surface_is_declared_on_enough_tickets() -> None:
    """One ticket would make the bare arm's failure an anecdote rather than a rate."""
    lost = [case for case in _cases() if "REFUND_ACK_LOST" in case.replay_defect_axes]
    assert len(lost) >= 5


# --- the real comparison (needs the Sandbox) ----------------------------------


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_the_gated_arm_has_no_duplicate_refund_and_no_false_block(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    executor = RefundCaseExecutor(sandbox_http_url, sandbox_token)
    cases = _cases()
    runs = {}
    for mode in AgentMode:
        runs[mode] = await RefundBenchmarkRunner(
            InMemoryRefundBenchmarkStore(), executor
        ).run(cases, versions=VERSIONS, mode=mode, repetitions=1, random_seed=20260831)

    bare = runs[AgentMode.BARE].metrics
    gated = runs[AgentMode.GATED].metrics
    assert bare["unexpected_loss_cases"]["value"] >= 1, "the bare arm lost nothing to catch"
    assert gated["unexpected_loss_cases"]["value"] == 0, gated["unexpected_loss_cases"]
    assert gated["false_block_cases"]["value"] == 0, gated["false_block_cases"]
    assert gated["final_state_correct_rate"]["value"] >= bare["final_state_correct_rate"]["value"]
    assert bare["failed_cells"] == 0 and gated["failed_cells"] == 0

    gate = RefundReleaseGate().evaluate(runs)
    assert gate.passed, gate.reasons


@pytest.mark.sandbox
def test_the_default_tool_timeout_is_below_the_acknowledgement_loss_delay() -> None:
    """If the tool waited longer than the Sandbox holds the response, a lost
    acknowledgement would arrive as a 504 instead of a timeout. Both are handled, but
    only the timeout is the shape the comparison is written against."""
    from rulearena_evaluation.refund_runner import TOOL_TIMEOUT_SECONDS

    delay = float(os.getenv("SANDBOX_ACK_LOST_DELAY_SECONDS", "12"))
    assert TOOL_TIMEOUT_SECONDS < delay
