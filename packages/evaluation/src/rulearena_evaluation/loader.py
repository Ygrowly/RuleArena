from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from rulearena_policy_schema import RuleSpec

from .models import BenchmarkCase, PublicCaseMetadata, Visibility

_CASES = TypeAdapter(tuple[BenchmarkCase, ...])
_PUBLIC_CASES = TypeAdapter(tuple[PublicCaseMetadata, ...])


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _expand_document(document: Any) -> Any:
    if not isinstance(document, dict):
        return document
    cases = document.get("cases")
    rule_specs = document.get("rule_specs")
    if not isinstance(cases, list) or not isinstance(rule_specs, dict):
        raise ValueError("benchmark document requires cases and rule_specs")
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


def _validate_suite(
    cases: tuple[BenchmarkCase, ...], visibility: Visibility, expected_count: int
) -> tuple[BenchmarkCase, ...]:
    if len(cases) != expected_count:
        raise ValueError(f"{visibility.value} suite must contain exactly {expected_count} cases")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("benchmark case IDs must be unique")
    if any(case.visibility is not visibility for case in cases):
        raise ValueError("case visibility does not match loader")
    return cases


class DevelopmentCaseLoader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def load(self) -> tuple[BenchmarkCase, ...]:
        return _validate_suite(
            _CASES.validate_python(_expand_document(_load_json(self.path))),
            Visibility.DEVELOPMENT,
            16,
        )


class EvaluationAccess:
    """Capability needed to open private hidden data; only the evaluation entrypoint creates it."""

    __slots__ = ("private_path",)

    def __init__(self, private_path: Path, marker: object) -> None:
        if marker is not _ACCESS_MARKER:
            raise PermissionError("hidden evaluation capability cannot be constructed directly")
        self.private_path = private_path

    @classmethod
    def from_environment(cls) -> EvaluationAccess:
        if os.getenv("RULEARENA_PROCESS_ROLE") != "evaluation":
            raise PermissionError("hidden cases are available only to the evaluation process role")
        raw = os.getenv("RULEARENA_HIDDEN_SUITE_PATH")
        if not raw:
            raise PermissionError("RULEARENA_HIDDEN_SUITE_PATH is required")
        path = Path(raw).resolve(strict=True)
        if path.name.casefold() in {"hidden-manifest.json", "development-v1.json"}:
            raise PermissionError("a public manifest cannot be used as hidden Ground Truth")
        return cls(path, _ACCESS_MARKER)


_ACCESS_MARKER = object()


class HiddenCaseLoader:
    def __init__(self, access: EvaluationAccess) -> None:
        self._access = access

    def load(self) -> tuple[BenchmarkCase, ...]:
        return _validate_suite(
            _CASES.validate_python(_expand_document(_load_json(self._access.private_path))),
            Visibility.HIDDEN,
            8,
        )


def load_hidden_manifest(path: str | Path) -> tuple[PublicCaseMetadata, ...]:
    cases = _PUBLIC_CASES.validate_python(_load_json(Path(path).resolve()))
    if len(cases) != 8 or any(case.visibility is not Visibility.HIDDEN for case in cases):
        raise ValueError("hidden public manifest must contain exactly 8 hidden metadata rows")
    return cases
