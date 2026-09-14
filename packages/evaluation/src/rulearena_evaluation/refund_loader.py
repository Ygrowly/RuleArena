"""Loading the refund suite. The same document shape as the search suite, so a reader
who knows one knows the other: a `rule_specs` map, and cases that name one by reference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from rulearena_policy_schema import RuleSpec

from .models import Visibility
from .refund_models import RefundTicketCase

_CASES = TypeAdapter(tuple[RefundTicketCase, ...])

# What the suite is expected to contain. Named rather than inferred so a truncated file
# fails at load instead of silently measuring nine tickets as if they were fifteen.
EXPECTED_TICKETS = 15


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _expand_document(document: Any) -> Any:
    if not isinstance(document, dict):
        return document
    cases = document.get("cases")
    rule_specs = document.get("rule_specs")
    if not isinstance(cases, list) or not isinstance(rule_specs, dict):
        raise ValueError("a refund suite requires cases and rule_specs")
    expanded: list[dict[str, Any]] = []
    for raw in cases:
        if not isinstance(raw, dict):
            raise ValueError("case rows must be objects")
        row = dict(raw)
        reference = row.pop("rule_spec_ref", None)
        if not isinstance(reference, str) or reference not in rule_specs:
            raise ValueError("case has an unknown rule_spec_ref")
        row["rule_spec"] = RuleSpec.model_validate_json(
            json.dumps(rule_specs[reference], ensure_ascii=False)
        )
        expanded.append(row)
    return expanded


class RefundSuiteLoader:
    def __init__(self, path: str | Path, *, expected_count: int = EXPECTED_TICKETS) -> None:
        self.path = Path(path).resolve()
        self.expected_count = expected_count

    def load(self) -> tuple[RefundTicketCase, ...]:
        cases = _CASES.validate_python(_expand_document(_load_json(self.path)))
        if len(cases) != self.expected_count:
            raise ValueError(
                f"the refund suite must contain exactly {self.expected_count} tickets"
            )
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("ticket IDs must be unique")
        if any(case.visibility is not Visibility.DEVELOPMENT for case in cases):
            raise ValueError("the refund suite is a development suite")
        # A generated ticket can invent its own version pair, which the runner then
        # rejects long after the suite was written.
        for scenario in {case.scenario_type for case in cases}:
            pairs = {
                (case.rule_version_id, case.scenario_version_id)
                for case in cases
                if case.scenario_type is scenario
            }
            if len(pairs) != 1:
                raise ValueError(
                    f"{scenario.value} tickets must share one rule and scenario version"
                )
        return cases
