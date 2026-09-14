"""A runtime gate over the Commerce Sandbox: three deterministic checks, nothing else.

The gate sits between a caller and the sandbox and answers one question per stage --
may this write go ahead, has an earlier attempt already landed, did this one land. It
does arithmetic on the sandbox's own state and receipts. It does not interpret policy,
call a model, read a RuleSpec, or write business state, and it never decides what is
*correct*: the Oracle does that, on the authoritative record, afterwards.

Three properties are load-bearing and are enforced by tests rather than by convention:

* the gate is invisible to the agent (`tests/refund/test_invariants.py`);
* the gate imports no model adapter (same file);
* an undecidable check is `UNKNOWN`, never a pass. `UNKNOWN` is a stop, not a shrug.
"""

from .gate import GateDecision, GateDecisionType, GateReason, RuntimeGate

__all__ = ["GateDecision", "GateDecisionType", "GateReason", "RuntimeGate"]
