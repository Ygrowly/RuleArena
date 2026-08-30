import json

import pytest
from rulearena_attack_runtime import (
    ALLOWED_TOOLS,
    ActionProposal,
    Budget,
    BudgetUsage,
    FakeLLMAdapter,
    ProposalRejected,
    StrategyAgent,
    StrategyType,
    build_agent_context,
    parse_proposal,
    validate_action_proposal,
)
from rulearena_policy_schema import ScenarioType
from rulearena_reference_simulator import ReferenceSimulator

from tests.phase2_factories import rule_spec


def test_tool_whitelist_has_no_side_effecting_system_access() -> None:
    assert ALLOWED_TOOLS == {
        "query_simulation_state",
        "list_legal_actions",
        "execute_simulator_action",
        "submit_candidate",
    }
    assert not ({"database", "filesystem", "shell", "network", "ground_truth"} & ALLOWED_TOOLS)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "proposal_type": "ACTION",
            "action_type": "CREATE_USER",
            "arguments": {},
            "outcome": "CONFIRMED_VIOLATION",
            "reason": "x",
        },
        {"proposal_type": "TOOL", "tool": "shell", "arguments": {}},
        {"proposal_type": "ACTION", "action_type": "UNKNOWN", "arguments": {}, "reason": "x"},
    ],
)
def test_forged_outcome_unknown_action_and_tool_injection_are_rejected(payload: object) -> None:
    with pytest.raises(ProposalRejected):
        parse_proposal(json.dumps(payload))


def test_context_rejects_ground_truth_and_limits_own_history() -> None:
    spec = rule_spec(ScenarioType.PROMOTION)
    simulator = ReferenceSimulator(spec)
    with pytest.raises(ProposalRejected, match="forbidden"):
        build_agent_context(
            strategy_type=StrategyType.VALUE_FLOW,
            rule_spec=spec,
            normalized_state={"ground_truth": "hidden"},
            legal_actions=simulator.legal_actions(simulator.initial_state()),
            own_history=(),
            remaining_budget=Budget(max_steps=2, max_tokens=10, max_cost=1, max_time_seconds=1),
            confirmed_counterexample_ids=(),
        )


def test_each_agent_rejects_another_strategy_context() -> None:
    agent = StrategyAgent(StrategyType.VALUE_FLOW, FakeLLMAdapter([]))
    spec = rule_spec(ScenarioType.PROMOTION)
    simulator = ReferenceSimulator(spec)
    context = build_agent_context(
        strategy_type=StrategyType.LIFECYCLE,
        rule_spec=spec,
        normalized_state=simulator.initial_state().normalized(),
        legal_actions=simulator.legal_actions(simulator.initial_state()),
        own_history=(),
        remaining_budget=Budget(max_steps=2, max_tokens=10, max_cost=1, max_time_seconds=1),
        confirmed_counterexample_ids=(),
    )
    with pytest.raises(ProposalRejected, match="mismatch"):
        import asyncio

        asyncio.run(agent.propose(context))


def test_runtime_rejects_duplicate_action_before_simulation() -> None:
    spec = rule_spec(ScenarioType.PROMOTION)
    simulator = ReferenceSimulator(spec)
    legal = simulator.legal_actions(simulator.initial_state())
    proposal = ActionProposal(
        proposal_type="ACTION",
        action_type="CREATE_USER",
        arguments={"initial_balance": "500.00"},
        reason="repeat",
    )
    with pytest.raises(ProposalRejected, match="duplicate"):
        validate_action_proposal(
            proposal,
            legal,
            ({"action_key": legal[0].canonical_key()},),
            BudgetUsage(),
            Budget(max_steps=2, max_tokens=10, max_cost=1, max_time_seconds=1),
        )
