"""Whether a benchmark case is measurable at all.

``AttackWorker`` lets a strategy propose only what ``validate_action_proposal``
accepts, and that gate is the agent's real action space. Ground-Truth verification
proved the Sandbox side and the Simulator tests proved the Simulator side, but
nothing joined them: a case whose defect needs an action the agent cannot propose is
unreachable for *every* strategy, which caps the achievable discovery rate without
any strategy being at fault.

The walk below runs the ground truth through the same validation and transition
calls the worker makes, so the verdict cannot drift from production behaviour. A
transition that rejects is not a failure: the worker records rejected actions in the
path it replays, and a rejection the real system does not share is exactly the
divergence under search.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rulearena_attack_runtime import (
    ActionProposal,
    ProposalRejected,
    SandboxReplayRunner,
    validate_action_proposal,
)
from rulearena_attack_runtime.workflow import Budget, BudgetUsage
from rulearena_domain_contracts import ActionType
from rulearena_evaluation import (
    BenchmarkCase,
    DevelopmentCaseLoader,
    EvaluationAccess,
    ExpectedOutcome,
    HiddenCaseLoader,
    Visibility,
    parse_ground_truth_actions,
)
from rulearena_oracle import OracleStatus
from rulearena_reference_simulator import ReferenceSimulator

ROOT = Path(__file__).resolve().parents[2]
_DEV_SUITE = ROOT / "benchmarks" / "development-v1.json"


def _unblocked_budget() -> tuple[Budget, BudgetUsage]:
    """A budget large enough that only the action contract can block the walk."""
    return (
        Budget(max_steps=64, max_tokens=10**6, max_cost=1000.0, max_time_seconds=10**6),
        BudgetUsage(),
    )


def _proposals(case: BenchmarkCase) -> tuple[ActionProposal, ...]:
    return tuple(
        ActionProposal(
            proposal_type="ACTION",
            action_type=ActionType(str(raw["action_type"])),
            target_id=(str(raw["target_id"]) if raw.get("target_id") else None),
            arguments=dict(raw.get("arguments") or {}),
            reason="ground truth",
        )
        for raw in case.ground_truth_actions
    )


def _unproposable_step(case: BenchmarkCase) -> str | None:
    """Return the first ground-truth step the agent could not propose, or None."""
    proposals = _proposals(case)
    simulator = ReferenceSimulator(case.rule_spec)
    state = simulator.initial_state()
    history: tuple[dict[str, object], ...] = ()
    budget, usage = _unblocked_budget()
    for index, proposal in enumerate(proposals, start=1):
        legal = simulator.legal_actions(state)
        try:
            action = validate_action_proposal(proposal, legal, history, usage, budget)
        except ProposalRejected as rejected:
            offered = sorted({f"{item.action_type.value}:{item.target_id}" for item in legal})
            return (
                f"step {index}/{len(proposals)}: {proposal.action_type.value} "
                f"target={proposal.target_id} arguments={dict(proposal.arguments)} "
                f"-> {rejected.kind.value} | offered={offered or 'nothing'}"
            )
        transition = simulator.transition(state, action)
        state = transition.state
        history += (
            {"action_key": action.canonical_key(), "status": transition.status.value},
        )
        usage = usage.model_copy(update={"steps": usage.steps + 1})
    return None


def _suite_cases() -> tuple[tuple[str, tuple[BenchmarkCase, ...]], ...]:
    suites = [("development", DevelopmentCaseLoader(_DEV_SUITE).load())]
    try:
        suites.append(("hidden", HiddenCaseLoader(EvaluationAccess.from_environment()).load()))
    except PermissionError:
        # The private hidden payload is a deployment asset; its absence only means
        # this module has less to check, never that a check passed.
        pass
    return tuple(suites)


def test_vulnerable_ground_truth_is_expressible_in_the_agent_action_model() -> None:
    blocked: list[str] = []
    for suite, cases in _suite_cases():
        for case in cases:
            if case.expected_outcome is not ExpectedOutcome.VULNERABLE:
                continue
            failure = _unproposable_step(case)
            if failure is not None:
                blocked.append(f"[{suite}] {case.case_id}: {failure}")
    assert not blocked, (
        f"{len(blocked)} ground-truth step(s) cannot be proposed by any strategy, so "
        "those cases are unmeasurable regardless of model quality:\n  " + "\n  ".join(blocked)
    )


def _redacted(case: BenchmarkCase, values: set[str]) -> str:
    """Hidden expectations stay out of output, including failure messages."""
    if case.visibility is Visibility.HIDDEN:
        return f"{len(values)} invariant(s) [redacted]"
    return str(sorted(values)) or "nothing"


@pytest.mark.sandbox
async def test_ground_truth_confirms_only_on_the_profile_it_belongs_to(
    sandbox_http_url: str, sandbox_token: str
) -> None:
    """A proposable ground truth makes the discovery ceiling 100%; it must also discriminate.

    Confirmation on the owning profile proves the ceiling, because the case is
    reachable (previous test) and a reachable path confirms. Confirmation on the
    opposite profile would mean the counterexample says nothing about the profile
    it is scored against.
    """
    runner = SandboxReplayRunner(sandbox_http_url, sandbox_token)
    not_confirmed: list[str] = []
    indistinguishable: list[str] = []
    for suite, cases in _suite_cases():
        for case in cases:
            if case.expected_outcome is not ExpectedOutcome.VULNERABLE:
                continue
            actions = parse_ground_truth_actions(case)
            for invariant in sorted(case.expected_invariant_ids, key=lambda item: item.value):
                for sandbox_version in (case.sandbox_version, _opposite(case.sandbox_version)):
                    result = await runner.replay(
                        case.rule_spec,
                        actions,
                        invariant,
                        sandbox_version=sandbox_version,
                    )
                    violated = {
                        item.invariant_id.value
                        for item in result.report.findings
                        if item.status is OracleStatus.VIOLATED
                    }
                    if sandbox_version == case.sandbox_version:
                        if result.classification.value != "CONFIRMED_VIOLATION":
                            not_confirmed.append(
                                f"[{suite}] {case.case_id} on {sandbox_version}: "
                                f"expected a confirmed violation, observed "
                                f"{_redacted(case, violated)}"
                            )
                    elif invariant.value in violated:
                        indistinguishable.append(
                            f"[{suite}] {case.case_id}: the {sandbox_version} profile "
                            "violates the invariant this case is scored on"
                        )
    assert not not_confirmed, (
        "the discovery ceiling is below the vulnerable case count:\n  " + "\n  ".join(not_confirmed)
    )
    assert not indistinguishable, (
        "these cases do not distinguish the two profiles:\n  " + "\n  ".join(indistinguishable)
    )


def _opposite(sandbox_version: str) -> str:
    return "fixed" if sandbox_version == "vulnerable" else "vulnerable"
