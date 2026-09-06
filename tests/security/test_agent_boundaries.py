import json

import pytest
from rulearena_attack_runtime import (
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


def test_agent_has_no_tool_surface_beyond_structured_proposals() -> None:
    """The agent exposes no tool channel; everything must arrive as a validated proposal."""
    import inspect

    from rulearena_attack_runtime import StrategyAgent as Agent

    public_methods = {
        name
        for name, member in inspect.getmembers(Agent, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public_methods == {"propose"}
    assert not ({"database", "filesystem", "shell", "network", "ground_truth"} & public_methods)


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


@pytest.mark.asyncio
async def test_agent_retries_once_on_invalid_json_and_worker_sees_both_attempts() -> None:
    import json as jsonlib

    from rulearena_attack_runtime import FakeLLMAdapter

    good = jsonlib.dumps(
        {"proposal_type": "STOP", "reason": "valid stop"},
    )
    adapter = FakeLLMAdapter(['{"proposal_type": "DANCE"}', good])
    agent = StrategyAgent(StrategyType.VALUE_FLOW, adapter)
    context = build_agent_context(
        strategy_type=StrategyType.VALUE_FLOW,
        rule_spec=rule_spec(ScenarioType.PROMOTION),
        normalized_state={},
        legal_actions=(),
        own_history=(),
        remaining_budget=Budget(max_steps=2, max_tokens=10, max_cost=1, max_time_seconds=1),
        confirmed_counterexample_ids=(),
    )
    proposal = await agent.propose(context)
    assert proposal.proposal_type == "STOP"
    records = adapter.drain_call_records()
    assert len(records) == 2
    assert all(record.response_hash for record in records)


@pytest.mark.asyncio
async def test_agent_retries_transient_provider_errors_and_succeeds() -> None:
    """429/transport failures from the provider must be retried, not escape."""
    import httpx
    from rulearena_attack_runtime import FakeLLMAdapter, LLMResponse

    class FlakyAdapter(FakeLLMAdapter):
        def __init__(self) -> None:
            super().__init__([])
            self.calls = 0

        async def complete_structured(
            self, *, system: str, untrusted_input: str, max_output_tokens: int | None = None
        ) -> LLMResponse:
            self.calls += 1
            if self.calls <= 2:
                request = httpx.Request("POST", "https://model.invalid/v1/chat/completions")
                raise httpx.HTTPStatusError(
                    "429", request=request, response=httpx.Response(429, request=request)
                )
            return LLMResponse(
                content='{"proposal_type":"STOP","reason":"done after retries"}'
            )

    adapter = FlakyAdapter()
    agent = StrategyAgent(StrategyType.VALUE_FLOW, adapter)
    context = build_agent_context(
        strategy_type=StrategyType.VALUE_FLOW,
        rule_spec=rule_spec(ScenarioType.PROMOTION),
        normalized_state={},
        legal_actions=(),
        own_history=(),
        remaining_budget=Budget(max_steps=2, max_tokens=10, max_cost=1, max_time_seconds=1),
        confirmed_counterexample_ids=(),
    )
    proposal = await agent.propose(context)
    assert proposal.proposal_type == "STOP"
    assert adapter.calls == 3
