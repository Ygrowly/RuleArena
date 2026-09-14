"""A deterministic refund agent: parse a ticket, refund the order, retry when told nothing.

v1 deliberately contains no model. The policy is a pure function of the ticket and the
snapshot the tool layer returned, so every run of a suite is exactly reproducible and a
difference between two modes can only come from what stands behind the gateway.

Two places where this agent is *not* smart, on purpose:

* it executes the ticket's amount as written, even when the ticket disagrees with what
  the order was actually paid;
* it retries a refund that reported nothing back under a **fresh** idempotency key.

Both are the failure surfaces this suite exists to expose, and bailing the agent out of
either would make the comparison measure nothing. The second one is the load-bearing
one: retrying an unanswered write under a new key is how one refund becomes two, and no
amount of local cleverness in the agent can rule it out -- only something that can see
the durable receipt can, which is what the gate does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import ConfigDict
from rulearena_domain_contracts import ActionType
from rulearena_reference_simulator import SimAction

from .gateway import ToolGateway, ToolOutcome
from .models import AgentAction, AgentDecision, AgentEscalate, StrictModel, Ticket

__all__ = [
    "AgentOutcome",
    "AgentReport",
    "AgentStep",
    "RefundAgent",
    "RefundRequest",
    "parse_refund_request",
]

# What the tool layer treats as refundable. Spelled out here rather than shared with the
# gate: if the two ever disagreed, a shared constant would hide it, and the whole point
# of the gate is that it is a second opinion on a different footing.
REFUNDABLE_ORDER_STATES = frozenset({"PAID", "PARTIALLY_REFUNDED"})

_ORDER_ID = re.compile(r"\border-\d+\b")
_REFUND_AMOUNT = re.compile(r"退款\s*([0-9]+(?:\.[0-9]{1,2})?)")
_FULL_REFUND_MARKERS = ("全额", "全款", "整单")


class AgentOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"


class RefundRequest(StrictModel):
    """What the ticket asks for. `amount is None` means "the order's full paid amount"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    amount: str | None = None


class AgentStep(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_type: str
    action_type: ActionType | None = None
    idempotency_key: str | None = None
    tool_outcome: ToolOutcome | None = None
    tool_error_code: str | None = None
    tool_error_message: str | None = None


class AgentReport(StrictModel):
    """What the agent says happened.

    `claimed_complete` is the agent's own claim and is kept apart from the authoritative
    outcome on purpose: the two disagreeing is one of the things this suite measures.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: str
    outcome: AgentOutcome
    claimed_complete: bool
    escalation_reason: str | None = None
    steps: tuple[AgentStep, ...] = ()


def parse_refund_request(text: str) -> RefundRequest | None:
    """Read a ticket the same way every time. No model, no heuristics, no fallback."""
    order = _ORDER_ID.search(text)
    if order is None:
        return None
    if any(marker in text for marker in _FULL_REFUND_MARKERS):
        return RefundRequest(order_id=order.group())
    amount = _REFUND_AMOUNT.search(text)
    if amount is None:
        return None
    return RefundRequest(order_id=order.group(), amount=amount.group(1))


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, float | bool) or not isinstance(value, str | int | Decimal):
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _orders(snapshot: Mapping[str, object]) -> dict[str, Mapping[str, object]] | None:
    state = snapshot.get("state")
    if not isinstance(state, Mapping):
        return None
    orders = state.get("orders")
    if not isinstance(orders, list) or not all(isinstance(item, Mapping) for item in orders):
        return None
    return {
        str(order["id"]): order for order in orders if isinstance(order.get("id"), str)
    }


def decide(ticket: Ticket, snapshot: Mapping[str, object]) -> AgentDecision:
    """The whole policy, as a pure function of a ticket and a snapshot."""
    request = parse_refund_request(ticket.text)
    if request is None:
        return _escalate("工单没有给出可执行的退款请求（订单号或金额缺失）")
    orders = _orders(snapshot)
    if orders is None:
        return _escalate("工具返回的快照里没有可读的订单集合")
    order = orders.get(request.order_id)
    if order is None:
        return _escalate(f"订单 {request.order_id} 不在当前可见状态中")
    status = order.get("status")
    if status not in REFUNDABLE_ORDER_STATES:
        return _escalate(f"订单 {request.order_id} 的状态是 {status}，当前不可退款")
    amount = request.amount if request.amount is not None else order.get("paid_amount")
    if amount is None or _decimal(amount) is None:
        return _escalate(f"无法确定订单 {request.order_id} 的退款金额")
    return AgentAction(
        proposal_type="ACTION",
        action_type=ActionType.REFUND_ORDER,
        target_id=request.order_id,
        arguments={"amount": str(amount)},
        reason=f"按工单为订单 {request.order_id} 退款 {amount}",
    )


def _escalate(reason: str) -> AgentEscalate:
    return AgentEscalate(proposal_type="ESCALATE", reason=reason)


def _sim_action(decision: AgentAction) -> SimAction:
    return SimAction(
        action_type=decision.action_type,
        target_id=decision.target_id,
        arguments=tuple(sorted(decision.arguments.items())),
    )


class RefundAgent:
    """Handles one ticket at a time through the gateway it was given."""

    def __init__(self, gateway: ToolGateway, *, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.gateway = gateway
        self.max_attempts = max_attempts

    async def handle(self, ticket: Ticket) -> AgentReport:
        steps: list[AgentStep] = []
        snapshot = await self._read_state(ticket, steps)
        if snapshot is None:
            return self._escalate(ticket, steps, "无法通过工具读取订单快照")
        decision = decide(ticket, snapshot)
        if isinstance(decision, AgentEscalate):
            steps.append(AgentStep(proposal_type="ESCALATE"))
            return self._escalate(ticket, steps, decision.reason)
        action = _sim_action(decision)
        for attempt in range(1, self.max_attempts + 1):
            keyed = replace(action, idempotency_key=f"{ticket.ticket_id}:refund:{attempt}")
            result = await self.gateway.call(keyed)
            steps.append(
                AgentStep(
                    proposal_type="ACTION",
                    action_type=keyed.action_type,
                    idempotency_key=keyed.idempotency_key,
                    tool_outcome=result.outcome,
                    tool_error_code=result.error_code,
                    tool_error_message=result.error_message,
                )
            )
            if result.succeeded:
                return AgentReport(
                    ticket_id=ticket.ticket_id,
                    outcome=AgentOutcome.COMPLETED,
                    claimed_complete=True,
                    steps=tuple(steps),
                )
            if not result.retryable:
                return self._escalate(
                    ticket,
                    steps,
                    f"工具给出 {result.outcome.value}（{result.error_code}），已停止该分支",
                )
        return self._escalate(
            ticket, steps, f"重试 {self.max_attempts} 次后工具仍未给出确定结果"
        )

    async def _read_state(
        self, ticket: Ticket, steps: list[AgentStep]
    ) -> Mapping[str, object] | None:
        result = await self.gateway.call(
            SimAction.build(ActionType.INSPECT_STATE, scope="RUN")
        )
        steps.append(
            AgentStep(
                proposal_type="ACTION",
                action_type=ActionType.INSPECT_STATE,
                tool_outcome=result.outcome,
                tool_error_code=result.error_code,
            )
        )
        if not result.succeeded or result.receipt is None:
            return None
        payload = result.receipt.get("result")
        if not isinstance(payload, Mapping):
            return None
        snapshot = payload.get("snapshot")
        return snapshot if isinstance(snapshot, Mapping) else None

    @staticmethod
    def _escalate(
        ticket: Ticket, steps: Iterable[AgentStep], reason: str
    ) -> AgentReport:
        return AgentReport(
            ticket_id=ticket.ticket_id,
            outcome=AgentOutcome.ESCALATED,
            claimed_complete=False,
            escalation_reason=reason,
            steps=tuple(steps),
        )
