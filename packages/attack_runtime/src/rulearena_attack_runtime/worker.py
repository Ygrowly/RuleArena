from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from rulearena_domain_contracts import ActionType
from rulearena_oracle import DeterministicOracle, InvariantId, OracleStatus
from rulearena_policy_schema import RuleSpec, ScenarioType
from rulearena_reference_simulator import ReferenceSimulator, SimAction, SimulationState

from .agents import (
    ActionProposal,
    ProposalRejected,
    StopProposal,
    StrategyAgent,
    build_agent_context,
    validate_action_proposal,
)
from .models import MinimizationResult, ReplayClassification, ReplayResult
from .workflow import (
    AttackOutcome,
    AttackStatus,
    Budget,
    CounterexampleRecord,
    RuntimeStore,
    StrategyRun,
    StrategyStatus,
    StrategyType,
)


class ReplayGateway(Protocol):
    async def replay(
        self,
        rule_spec: RuleSpec,
        actions: Sequence[SimAction],
        target_invariant: InvariantId,
        *,
        sandbox_version: str = "fixed",
    ) -> ReplayResult: ...

    async def minimize(
        self,
        rule_spec: RuleSpec,
        actions: Sequence[SimAction],
        target_invariant: InvariantId,
        *,
        sandbox_version: str = "fixed",
    ) -> MinimizationResult: ...


class FaultPoint(StrEnum):
    BEFORE_CHECKPOINT = "BEFORE_CHECKPOINT"
    AFTER_CHECKPOINT = "AFTER_CHECKPOINT"
    BEFORE_ORACLE_PERSIST = "BEFORE_ORACLE_PERSIST"
    AFTER_ORACLE_PERSIST = "AFTER_ORACLE_PERSIST"


class InjectedWorkerCrash(RuntimeError):
    pass


FaultInjector = Callable[[FaultPoint], None]

_SCENARIO_INVARIANTS: dict[ScenarioType, frozenset[InvariantId]] = {
    ScenarioType.PROMOTION: frozenset(
        {
            InvariantId.NET_PAID_NON_NEGATIVE,
            InvariantId.REFUND_NOT_EXCEED_PAID,
            InvariantId.COUPON_SINGLE_CONSUMPTION,
            InvariantId.ORDER_TERMINAL_MONOTONICITY,
            InvariantId.IDEMPOTENT_EFFECT,
        }
    ),
    ScenarioType.REFUND_POINTS: frozenset(
        {
            InvariantId.NET_PAID_NON_NEGATIVE,
            InvariantId.REFUND_NOT_EXCEED_PAID,
            InvariantId.POINTS_VALUE_CONSERVATION,
            InvariantId.ORDER_TERMINAL_MONOTONICITY,
            InvariantId.IDEMPOTENT_EFFECT,
        }
    ),
    ScenarioType.MEMBERSHIP_ENTITLEMENT: frozenset(
        {
            InvariantId.ENTITLEMENT_NON_NEGATIVE,
            InvariantId.ENTITLEMENT_REFUND_CONSISTENCY,
            InvariantId.IDEMPOTENT_EFFECT,
        }
    ),
}


def _serialize_action(action: SimAction) -> dict[str, object]:
    return {
        "action_type": action.action_type.value,
        "actor_id": action.actor_id,
        "target_id": action.target_id,
        "arguments": dict(action.arguments),
        "idempotency_key": action.idempotency_key,
    }


def _deserialize_action(value: Mapping[str, object]) -> SimAction:
    arguments = value.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise ValueError("checkpoint action arguments are invalid")
    safe_arguments: dict[str, str | int | bool] = {}
    for key, item in arguments.items():
        if (
            not isinstance(key, str)
            or isinstance(item, float)
            or not isinstance(item, str | int | bool)
        ):
            raise ValueError("checkpoint action argument has an invalid type")
        safe_arguments[key] = item
    return SimAction.build(
        ActionType(str(value["action_type"])),
        actor_id=str(value.get("actor_id", "user-1")),
        target_id=str(value["target_id"]) if value.get("target_id") is not None else None,
        idempotency_key=(
            str(value["idempotency_key"]) if value.get("idempotency_key") is not None else None
        ),
        **safe_arguments,
    )


def _resume_state(
    simulator: ReferenceSimulator, actions: Sequence[SimAction]
) -> tuple[SimulationState, list[dict[str, object]]]:
    state = simulator.initial_state()
    snapshots: list[dict[str, object]] = [
        {"state": state.normalized(), "state_hash": state.state_hash()}
    ]
    for action in actions:
        transition = simulator.transition(state, action)
        state = transition.state
        snapshots.append({"state": state.normalized(), "state_hash": state.state_hash()})
    return state, snapshots


class AttackWorker:
    """Deterministic orchestration; agents can only propose simulator actions."""

    def __init__(
        self,
        store: RuntimeStore,
        replay: ReplayGateway,
        agents: Mapping[StrategyType, StrategyAgent],
        *,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        missing = set(StrategyType).difference(agents)
        if missing:
            names = sorted(item.value for item in missing)
            raise ValueError(f"missing isolated strategy agents: {names}")
        if len({id(agent) for agent in agents.values()}) != len(StrategyType):
            raise ValueError("each strategy must use a distinct agent instance")
        self.store = store
        self.replay = replay
        self.agents = dict(agents)
        self.oracle = DeterministicOracle()
        self.fault_injector = fault_injector

    def _fault(self, point: FaultPoint) -> None:
        if self.fault_injector:
            self.fault_injector(point)

    async def run(self, run_id: str, rule_spec: RuleSpec) -> None:
        run = self.store.get_run(run_id)
        if run.status in {AttackStatus.COMPLETED, AttackStatus.CANCELLED}:
            return
        if run.status is AttackStatus.CANCEL_REQUESTED:
            self.store.compare_and_set_status(
                run_id,
                AttackStatus.CANCEL_REQUESTED,
                AttackStatus.CANCELLED,
                outcome=AttackOutcome.CANCELLED,
            )
            return
        if run.status is AttackStatus.READY:
            if not self.store.compare_and_set_status(
                run_id, AttackStatus.READY, AttackStatus.SEARCHING
            ):
                return
        elif run.status is AttackStatus.FAILED:
            if not self.store.compare_and_set_status(
                run_id, AttackStatus.FAILED, AttackStatus.RECOVERING
            ):
                return
            if not self.store.compare_and_set_status(
                run_id, AttackStatus.RECOVERING, AttackStatus.SEARCHING
            ):
                return
        elif run.status not in {AttackStatus.SEARCHING, AttackStatus.REPLAYING}:
            return

        if run.status is AttackStatus.REPLAYING and self.store.counterexamples(run_id):
            self.store.compare_and_set_status(
                run_id,
                AttackStatus.REPLAYING,
                AttackStatus.COMPLETED,
                outcome=AttackOutcome.CONFIRMED_VIOLATION,
            )
            return

        saw_unconfirmed = False
        try:
            for strategy_type in StrategyType:
                total_elapsed = (datetime.now(UTC) - run.created_at).total_seconds()
                if total_elapsed >= run.budget.max_time_seconds:
                    break
                if self.store.is_cancel_requested(run_id):
                    self.store.compare_and_set_status(
                        run_id,
                        AttackStatus.CANCEL_REQUESTED,
                        AttackStatus.CANCELLED,
                        outcome=AttackOutcome.CANCELLED,
                    )
                    return
                strategy = self.store.ensure_strategy(run_id, strategy_type, run.budget)
                current_status = self.store.get_run(run_id).status
                checkpoint = self.store.load_checkpoint(strategy.strategy_run_id)
                pending_invariant = (
                    checkpoint.state.get("candidate_invariant") if checkpoint else None
                )
                candidate: tuple[tuple[SimAction, ...], InvariantId] | None
                if current_status is AttackStatus.REPLAYING and isinstance(
                    pending_invariant, str
                ):
                    raw_actions = checkpoint.state.get("actions", []) if checkpoint else []
                    candidate = (
                        tuple(_deserialize_action(item) for item in raw_actions),
                        InvariantId(pending_invariant),
                    )
                else:
                    candidate = await self._search_strategy(
                        run_id, strategy, rule_spec
                    )
                if candidate is None:
                    continue
                actions, invariant = candidate
                current = self.store.get_run(run_id).status
                if current is AttackStatus.SEARCHING:
                    self.store.compare_and_set_status(
                        run_id, AttackStatus.SEARCHING, AttackStatus.REPLAYING
                    )
                replayed = await self.replay.replay(
                    rule_spec,
                    actions,
                    invariant,
                    sandbox_version=run.sandbox_version,
                )
                self._fault(FaultPoint.BEFORE_ORACLE_PERSIST)
                if replayed.classification is ReplayClassification.CONFIRMED_VIOLATION:
                    minimized = await self.replay.minimize(
                        rule_spec,
                        actions,
                        invariant,
                        sandbox_version=run.sandbox_version,
                    )
                    candidate_key = hashlib.sha256(
                        json.dumps(
                            [item.canonical_key() for item in actions], separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                    self.store.save_counterexample(
                        CounterexampleRecord(
                            counterexample_id=str(uuid4()),
                            attack_run_id=run_id,
                            candidate_key=f"{invariant.value}:{candidate_key}",
                            invariant_id=invariant.value,
                            original_actions=tuple(_serialize_action(item) for item in actions),
                            minimized_actions=tuple(
                                _serialize_action(item) for item in minimized.minimized_actions
                            ),
                            replay_run_id=replayed.run_id,
                            created_at=datetime.now(UTC),
                        )
                    )
                    self._fault(FaultPoint.AFTER_ORACLE_PERSIST)
                    self.store.compare_and_set_status(
                        run_id,
                        AttackStatus.REPLAYING,
                        AttackStatus.COMPLETED,
                        outcome=AttackOutcome.CONFIRMED_VIOLATION,
                    )
                    return
                saw_unconfirmed = True
                self.store.compare_and_set_status(
                    run_id, AttackStatus.REPLAYING, AttackStatus.SEARCHING
                )
            current = self.store.get_run(run_id).status
            if current is AttackStatus.SEARCHING:
                self.store.compare_and_set_status(
                    run_id,
                    AttackStatus.SEARCHING,
                    AttackStatus.COMPLETED,
                    outcome=(
                        AttackOutcome.UNCONFIRMED_CANDIDATE
                        if saw_unconfirmed
                        else AttackOutcome.NO_VIOLATION_WITHIN_BUDGET
                    ),
                )
        except InjectedWorkerCrash:
            raise
        except Exception:
            current = self.store.get_run(run_id).status
            if current in {AttackStatus.SEARCHING, AttackStatus.REPLAYING}:
                self.store.compare_and_set_status(
                    run_id,
                    current,
                    AttackStatus.FAILED,
                    outcome=AttackOutcome.INFRA_FAILED,
                )
            raise

    async def _search_strategy(
        self,
        run_id: str,
        strategy: StrategyRun,
        rule_spec: RuleSpec,
    ) -> tuple[tuple[SimAction, ...], InvariantId] | None:
        simulator = ReferenceSimulator(rule_spec)
        checkpoint = self.store.load_checkpoint(strategy.strategy_run_id)
        actions = (
            [_deserialize_action(item) for item in checkpoint.state.get("actions", [])]
            if checkpoint
            else []
        )
        state, snapshots = _resume_state(simulator, actions)
        history = tuple(
            {"action_key": action.canonical_key(), "status": "APPLIED"} for action in actions
        )
        usage = strategy.usage
        strategy = strategy.model_copy(update={"status": StrategyStatus.SEARCHING})
        self.store.update_strategy(strategy)
        checkpoint_version = checkpoint.version if checkpoint else 0
        segment_started = time.monotonic()
        prior_elapsed = usage.elapsed_seconds
        while True:
            elapsed = prior_elapsed + (time.monotonic() - segment_started)
            usage = usage.model_copy(update={"elapsed_seconds": elapsed})
            if not usage.within(strategy.budget) or usage.steps >= strategy.budget.max_steps:
                break
            if self.store.is_cancel_requested(run_id):
                break
            legal = simulator.legal_actions(state)
            if not legal:
                break
            remaining = Budget(
                max_steps=strategy.budget.max_steps - usage.steps,
                max_tokens=strategy.budget.max_tokens - usage.tokens,
                max_cost=max(0, strategy.budget.max_cost - usage.cost),
                max_time_seconds=max(
                    0.000001, strategy.budget.max_time_seconds - usage.elapsed_seconds
                ),
            )
            context = build_agent_context(
                strategy_type=strategy.strategy_type,
                rule_spec=rule_spec,
                normalized_state=state.normalized(),
                legal_actions=legal,
                own_history=history,
                remaining_budget=remaining,
                confirmed_counterexample_ids=tuple(
                    item.counterexample_id for item in self.store.counterexamples(run_id)
                ),
            )
            proposal = await self.agents[strategy.strategy_type].propose(context)
            call = self.agents[strategy.strategy_type].adapter.last_call
            call_tokens = (call.input_tokens + call.output_tokens) if call else 0
            call_cost = call.cost if call else 0
            next_usage = usage.model_copy(
                update={"tokens": usage.tokens + call_tokens, "cost": usage.cost + call_cost}
            )
            if not next_usage.within(strategy.budget):
                strategy = strategy.model_copy(update={"usage": next_usage})
                self.store.update_strategy(strategy)
                break
            usage = next_usage
            if isinstance(proposal, StopProposal):
                strategy = strategy.model_copy(update={"usage": usage})
                self.store.update_strategy(strategy)
                if proposal.candidate_invariant is not None and actions:
                    if proposal.candidate_invariant not in _SCENARIO_INVARIANTS[
                        rule_spec.scenario_type
                    ]:
                        raise ProposalRejected(
                            "candidate invariant does not belong to the selected scenario"
                        )
                    checkpoint = self.store.load_checkpoint(strategy.strategy_run_id)
                    if checkpoint is None:
                        raise RuntimeError("candidate must have a durable checkpoint")
                    candidate_state = dict(checkpoint.state)
                    candidate_state["candidate_invariant"] = (
                        proposal.candidate_invariant.value
                    )
                    self.store.save_checkpoint(
                        strategy.strategy_run_id,
                        candidate_state,
                        expected_version=checkpoint.version,
                    )
                    self.store.update_strategy(
                        strategy.model_copy(update={"status": StrategyStatus.COMPLETED})
                    )
                    return tuple(actions), proposal.candidate_invariant
                break
            if not isinstance(proposal, ActionProposal):
                raise ProposalRejected("unknown proposal type")
            action = validate_action_proposal(proposal, legal, history, usage, strategy.budget)
            stable_action = SimAction(
                action_type=action.action_type,
                actor_id=action.actor_id,
                target_id=action.target_id,
                arguments=action.arguments,
                idempotency_key=(
                    f"{run_id}:{strategy.strategy_run_id}:step:{usage.steps + 1}"
                ),
            )
            transition = simulator.transition(state, stable_action)
            state = transition.state
            actions.append(stable_action)
            history += ({"action_key": action.canonical_key(), "status": transition.status.value},)
            snapshots.append({"state": state.normalized(), "state_hash": state.state_hash()})
            usage = usage.model_copy(update={"steps": usage.steps + 1})
            strategy = strategy.model_copy(update={"usage": usage})
            self.store.update_strategy(strategy)
            self._fault(FaultPoint.BEFORE_CHECKPOINT)
            last_call = self.agents[strategy.strategy_type].adapter.last_call
            saved = self.store.save_checkpoint(
                strategy.strategy_run_id,
                {
                    "actions": [_serialize_action(item) for item in actions],
                    "usage": usage.model_dump(mode="json"),
                    "model_config_hash": hashlib.sha256(
                        strategy.strategy_type.value.encode()
                    ).hexdigest(),
                    "prompt_version": last_call.prompt_version if last_call else "unknown",
                },
                expected_version=checkpoint_version,
            )
            checkpoint_version = saved.version
            self._fault(FaultPoint.AFTER_CHECKPOINT)
            report = self.oracle.evaluate(rule_spec, snapshots=snapshots)
            violated = next(
                (item for item in report.findings if item.status is OracleStatus.VIOLATED), None
            )
            if violated:
                checkpoint = self.store.load_checkpoint(strategy.strategy_run_id)
                if checkpoint is None:
                    raise RuntimeError("candidate must have a durable checkpoint")
                candidate_state = dict(checkpoint.state)
                candidate_state["candidate_invariant"] = violated.invariant_id.value
                self.store.save_checkpoint(
                    strategy.strategy_run_id,
                    candidate_state,
                    expected_version=checkpoint.version,
                )
                self.store.update_strategy(
                    strategy.model_copy(update={"status": StrategyStatus.COMPLETED})
                )
                return tuple(actions), violated.invariant_id
            await asyncio.sleep(0)
        self.store.update_strategy(strategy.model_copy(update={"status": StrategyStatus.COMPLETED}))
        return None
