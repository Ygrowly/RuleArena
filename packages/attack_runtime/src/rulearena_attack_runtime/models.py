from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from rulearena_oracle import InvariantId, OracleReport
from rulearena_reference_simulator import SimAction


class ReplayClassification(StrEnum):
    CONFIRMED_VIOLATION = "CONFIRMED_VIOLATION"
    MODEL_DIVERGENCE = "MODEL_DIVERGENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    classification: ReplayClassification
    target_invariant: InvariantId
    run_id: str
    actions: tuple[SimAction, ...]
    report: OracleReport
    snapshots: tuple[dict[str, object], ...]
    receipts: tuple[dict[str, object], ...]
    events: tuple[dict[str, object], ...]


class MinimizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    invariant_id: InvariantId
    original_length: int
    minimized_actions: tuple[SimAction, ...]
    trials: int
    one_minimal: bool
