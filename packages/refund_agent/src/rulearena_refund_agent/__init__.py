"""A refund agent and the single channel it is allowed to reach the world through.

The package is deliberately blind: it holds no benchmark case, no ground truth, no
defect axis, and no runtime gate. `WriteGuard` is the only thing it knows about a gate,
and it is a structural type, so this package never imports the gate package -- see
`tests/refund/test_invariants.py`, which fails the build if it ever does.
"""

from .agent import (
    REFUNDABLE_ORDER_STATES,
    AgentOutcome,
    AgentReport,
    AgentStep,
    RefundAgent,
    RefundRequest,
    decide,
    parse_refund_request,
)
from .gateway import (
    GuardVerdict,
    ToolCallRecord,
    ToolGateway,
    ToolOutcome,
    ToolResult,
    ToolUnavailable,
    WriteGuard,
)
from .models import (
    AgentAction,
    AgentDecision,
    AgentEscalate,
    Ticket,
    agent_decision_json_schema,
    parse_agent_decision,
)

__all__ = [
    "REFUNDABLE_ORDER_STATES",
    "AgentAction",
    "AgentDecision",
    "AgentEscalate",
    "AgentOutcome",
    "AgentReport",
    "AgentStep",
    "GuardVerdict",
    "RefundAgent",
    "RefundRequest",
    "Ticket",
    "ToolCallRecord",
    "ToolGateway",
    "ToolOutcome",
    "ToolResult",
    "ToolUnavailable",
    "WriteGuard",
    "agent_decision_json_schema",
    "decide",
    "parse_agent_decision",
    "parse_refund_request",
]
