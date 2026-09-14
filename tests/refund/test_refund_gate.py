"""D4: the two modes are compared on facts, and the checks that guard the comparison."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rulearena_evaluation import (
    AgentMode,
    BenchmarkStatus,
    RefundBenchmarkRun,
    RefundCaseRun,
    RefundReleaseGate,
    RefundTicketCase,
    VersionTuple,
    compute_refund_metrics,
    expected_state_satisfied,
)
from rulearena_oracle import InvariantId
from rulearena_policy_schema import ScenarioType
from rulearena_refund_agent import AgentOutcome

from tests.phase2_factories import rule_spec

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

SPEC = rule_spec(ScenarioType.PROMOTION)


def case(case_id: str, *, escalation: bool = False) -> RefundTicketCase:
    return RefundTicketCase(
        case_id=case_id,
        benchmark_version="refund-v1",
        visibility="development",
        scenario_type=ScenarioType.PROMOTION,
        rule_version_id="00000000-0000-0000-0000-000000000101",
        scenario_version_id="promotion-v1",
        ticket_text="客服工单：订单 order-1 全额退款。",
        setup_actions=({"action_type": "CREATE_USER", "arguments": {"initial_balance": "10.00"}},),
        defect_axes=frozenset(),
        expected_final_state={"orders": [{"id": "order-1", "status": "REFUNDED"}]},
        expected_invariants=frozenset({InvariantId.REFUND_NOT_EXCEED_PAID}),
        construction_reason="test",
        expects_escalation=escalation,
        rule_spec=SPEC,
    )


def fact(
    case_id: str,
    *,
    mode: AgentMode,
    satisfied: bool = True,
    invariants: bool = True,
    loss: str = "0",
    refused: int = 0,
    claimed: bool = True,
    escalated: bool = False,
    tool_calls: int = 1,
) -> RefundCaseRun:
    return RefundCaseRun(
        case_id=case_id,
        mode=mode,
        repetition=1,
        agent_outcome=AgentOutcome.ESCALATED if escalated else AgentOutcome.COMPLETED,
        claimed_complete=claimed,
        escalated=escalated,
        final_state_satisfied=satisfied,
        invariants_satisfied=invariants,
        loss_order_ids=("order-1",) if loss != "0" else (),
        loss_amount=loss,
        tool_calls=tool_calls,
        refused_writes=refused,
    )


def bench(
    mode: AgentMode, facts: list[RefundCaseRun], cases: list[RefundTicketCase]
) -> RefundBenchmarkRun:
    return RefundBenchmarkRun(
        versions=VERSIONS,
        mode=mode,
        random_seed=7,
        repetitions=1,
        suite="development",
        status=BenchmarkStatus.COMPLETED,
        raw_runs=tuple(facts),
        metrics=compute_refund_metrics(cases, facts),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


def pair(
    gated_facts: list[RefundCaseRun], bare_facts: list[RefundCaseRun], cases: list[RefundTicketCase]
) -> dict[AgentMode, RefundBenchmarkRun]:
    return {
        AgentMode.BARE: bench(AgentMode.BARE, bare_facts, cases),
        AgentMode.GATED: bench(AgentMode.GATED, gated_facts, cases),
    }


def test_expected_state_is_a_subset_match_by_id() -> None:
    state: dict[str, Any] = {
        "orders": [{"id": "order-1", "status": "REFUNDED", "refunded_amount": "90.00", "x": 1}],
        "users": [{"id": "user-1", "balance": "10.00"}],
    }
    assert expected_state_satisfied({"orders": [{"id": "order-1", "status": "REFUNDED"}]}, state)
    assert not expected_state_satisfied({"orders": [{"id": "order-1", "status": "PAID"}]}, state)
    assert not expected_state_satisfied({"orders": [{"id": "order-9", "status": "PAID"}]}, state)


def test_the_comparison_passes_when_the_gated_arm_is_clean() -> None:
    cases = [case("t-1"), case("t-2")]
    gate = RefundReleaseGate().evaluate(
        pair(
            [fact("t-1", mode=AgentMode.GATED), fact("t-2", mode=AgentMode.GATED)],
            [fact("t-1", mode=AgentMode.BARE), fact("t-2", mode=AgentMode.BARE)],
            cases,
        )
    )
    assert gate.passed, gate.reasons


def test_a_duplicate_refund_in_the_gated_arm_fails_even_when_every_ticket_is_answered() -> None:
    """The whole point of INV-C: a perfect task score does not excuse a leak."""
    cases = [case("t-1"), case("t-2")]
    gate = RefundReleaseGate().evaluate(
        pair(
            [
                fact("t-1", mode=AgentMode.GATED, loss="80.00", invariants=False),
                fact("t-2", mode=AgentMode.GATED),
            ],
            [fact("t-1", mode=AgentMode.BARE), fact("t-2", mode=AgentMode.BARE)],
            cases,
        )
    )
    assert gate.checks["no_duplicate_refund_in_gated"] is False
    assert gate.checks["gated_not_worse_than_bare"] is False
    assert not gate.passed


def test_blocking_a_ticket_that_should_have_been_handled_fails_the_gate() -> None:
    cases = [case("t-1")]
    gate = RefundReleaseGate().evaluate(
        pair(
            [
                fact(
                    "t-1",
                    mode=AgentMode.GATED,
                    refused=1,
                    satisfied=False,
                    escalated=True,
                    claimed=False,
                )
            ],
            [fact("t-1", mode=AgentMode.BARE)],
            cases,
        )
    )
    assert gate.checks["no_false_block_on_normal"] is False
    assert gate.checks["no_duplicate_refund_in_gated"] is True


def test_a_handoff_on_a_ticket_that_must_be_handed_off_is_not_a_false_block() -> None:
    cases = [case("t-1", escalation=True)]
    gate = RefundReleaseGate().evaluate(
        pair(
            [
                fact(
                    "t-1",
                    mode=AgentMode.GATED,
                    refused=1,
                    escalated=True,
                    claimed=False,
                    satisfied=False,
                )
            ],
            [fact("t-1", mode=AgentMode.BARE, escalated=True, claimed=False, satisfied=False)],
            cases,
        )
    )
    assert gate.checks["no_false_block_on_normal"] is True


def test_a_missing_arm_is_reported_rather_than_compared() -> None:
    gate = RefundReleaseGate().evaluate({AgentMode.GATED: bench(AgentMode.GATED, [], [])})
    assert not gate.passed
    assert gate.reasons == ("no completed run for: BARE",)


def test_two_runs_of_different_configurations_are_not_a_comparison() -> None:
    cases = [case("t-1")]
    runs = pair(
        [fact("t-1", mode=AgentMode.GATED)],
        [fact("t-1", mode=AgentMode.BARE)],
        cases,
    )
    runs[AgentMode.GATED] = runs[AgentMode.GATED].model_copy(update={"random_seed": 99})
    gate = RefundReleaseGate().evaluate(runs)
    assert gate.checks["matching_configuration"] is False


def test_metrics_report_the_two_headline_numbers_side_by_side() -> None:
    cases = [case("t-1"), case("t-2")]
    facts = [
        fact("t-1", mode=AgentMode.BARE, loss="80.00", invariants=False, satisfied=False),
        fact("t-1", mode=AgentMode.BARE, loss="80.00", invariants=False, satisfied=False),
        fact("t-2", mode=AgentMode.BARE),
        fact("t-2", mode=AgentMode.BARE),
    ]
    metrics = compute_refund_metrics(cases, facts)
    assert metrics["unexpected_loss_cases"]["value"] == 1.0
    # Per ticket, not per repetition: the headline is what one mishandling costs.
    assert metrics["unexpected_loss_amount"]["value"] == 80.0
    # Every run is still counted in the measurement total.
    assert metrics["unexpected_loss_total_across_runs"]["value"] == 160.0
    # The loss is counted per ticket, not per repetition, but the rate is not.
    assert metrics["final_state_correct_rate"]["numerator"] == 1


def test_a_false_success_is_counted_even_when_nothing_was_lost() -> None:
    cases = [case("t-1")]
    metrics = compute_refund_metrics(
        cases, [fact("t-1", mode=AgentMode.BARE, claimed=True, satisfied=False)]
    )
    assert metrics["false_success_rate"]["value"] == 1.0
    assert metrics["false_success_case_ids"] == ["t-1"]


def test_gate_overhead_is_recomputed_from_the_facts() -> None:
    cases = [case("t-1")]
    facts = [
        RefundCaseRun(
            case_id="t-1",
            mode=AgentMode.GATED,
            repetition=1,
            agent_outcome=AgentOutcome.COMPLETED,
            claimed_complete=True,
            escalated=False,
            final_state_satisfied=True,
            invariants_satisfied=True,
            tool_calls=3,
            gate_checks=2,
        )
    ]
    metrics = compute_refund_metrics(cases, facts)
    assert metrics["gate_overhead"]["guard_checks"] == 2
    assert metrics["gate_overhead"]["guard_checks_per_case"] == 2.0
    assert metrics["gate_overhead"]["tool_calls"] == 3


def test_a_failed_cell_is_excluded_and_does_not_blame_the_agent() -> None:
    """Infrastructure breaking is not the agent handing off, and it is not a data point.

    Recording a broken cell as an escalation would both inflate the handoff rate and
    count a measurement that never happened; the fact carries no agent outcome at all.
    """
    cases = [case("t-1"), case("t-2")]
    broken = RefundCaseRun(
        case_id="t-1",
        mode=AgentMode.GATED,
        repetition=1,
        agent_outcome=None,
        claimed_complete=False,
        escalated=False,
        status=BenchmarkStatus.FAILED,
        failure_reason="ToolUnavailable: boom",
    )
    metrics = compute_refund_metrics(cases, [broken, fact("t-2", mode=AgentMode.GATED)])
    assert metrics["failed_cells"] == 1
    assert metrics["ticket_count"] == 2
    assert metrics["final_state_correct_rate"]["denominator"] == 1
    assert metrics["necessary_escalation_rate"]["denominator"] == 0
