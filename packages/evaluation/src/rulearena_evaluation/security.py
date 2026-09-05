from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .models import BenchmarkCase

_MARKERS = (
    "ground_truth",
    "expected_invariant_ids",
    "hidden_action_sequence",
    "sandbox_profile",
    "expected_outcome",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def hidden_answer_fingerprints(cases: Iterable[BenchmarkCase]) -> frozenset[str]:
    values: set[str] = set()
    for case in cases:
        answers = {
            "expected_outcome": case.expected_outcome.value,
            "expected_invariant_ids": sorted(item.value for item in case.expected_invariant_ids),
            "ground_truth_actions": case.ground_truth_actions,
        }
        values.add(hashlib.sha256(_canonical(answers).encode()).hexdigest())
        if case.ground_truth_actions:
            values.add(
                hashlib.sha256(_canonical(case.ground_truth_actions).encode()).hexdigest()
            )
    return frozenset(values)


def scan_ground_truth_leakage(
    payloads: Iterable[Any], *, hidden_cases: Iterable[BenchmarkCase] = ()
) -> tuple[str, ...]:
    fingerprints = hidden_answer_fingerprints(hidden_cases)
    findings: list[str] = []
    for index, payload in enumerate(payloads):
        encoded = _canonical(payload)
        lowered = encoded.casefold()
        for marker in _MARKERS:
            if marker in lowered:
                findings.append(f"payload[{index}] contains forbidden marker {marker}")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        if digest in fingerprints:
            findings.append(f"payload[{index}] equals hidden answer fingerprint")
    return tuple(findings)


def scan_forbidden_markers(payloads: Iterable[Any]) -> tuple[str, ...]:
    """Marker-only exit scan for boundaries that never hold hidden answer material."""
    findings: list[str] = []
    for index, payload in enumerate(payloads):
        lowered = _canonical(payload).casefold()
        findings.extend(
            f"payload[{index}] contains forbidden marker {marker}"
            for marker in _MARKERS
            if marker in lowered
        )
    return tuple(findings)


def public_metric_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    """Remove run/case-level evidence before a benchmark aggregate crosses the public API."""

    forbidden = {
        "discovered_case_ids",
        "evaluable_run_ids",
        "false_positive_case_ids",
        "source_run_ids",
    }

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in forbidden}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    result = clean(metrics)
    assert isinstance(result, dict)
    return result
