"""The four invariants, as checks that fail the build rather than as conventions.

`INV-A` (the agent cannot tell a gate exists) and `INV-B` (the gate does no semantic
judgement and calls no model) are the two that decay silently: nothing about a working
system looks wrong the moment the agent imports the gate "just for the receipt type", or
the gate grows a model call "just to classify the refund". Both are checked by reading
the packages' own imports.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from rulearena_refund_agent import WriteGuard
from rulearena_runtime_gate import RuntimeGate

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "packages" / "refund_agent" / "src"
GATE_SRC = ROOT / "packages" / "runtime_gate" / "src"


def imported_modules(root: Path) -> dict[str, set[str]]:
    """Every absolute module named by an import statement, per file.

    `ast` rather than a text search on purpose: this catches imports under
    `TYPE_CHECKING`, which is exactly the loophole the invariant names. Relative imports
    are skipped -- `from .gate import ...` cannot escape the package it lives in.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module)
        found[str(path.relative_to(root))] = names
    if not found:
        # A scan that finds nothing to scan passes every assertion below it, which is
        # the one way a guard like this fails silently. A moved package must break the
        # build rather than quietly stop checking.
        raise AssertionError(f"no Python files under {root}; the scan checked nothing")
    return found


def test_inv_a_the_agent_cannot_reach_the_gate() -> None:
    """Not one symbol, at runtime or under TYPE_CHECKING."""
    forbidden = ("rulearena_runtime_gate", "rulearena_evaluation", "rulearena_attack_runtime")
    for path, modules in imported_modules(AGENT_SRC).items():
        hit = sorted(name for name in modules if name.startswith(forbidden))
        assert not hit, f"{path} imports {hit}"


def test_inv_a_the_agent_knows_no_benchmark_vocabulary() -> None:
    """A ticket carries the request; it must not be able to carry the answer."""
    forbidden = (
        "RefundBenchCase",
        "expected_final_state",
        "expected_invariants",
        "defect_axes",
        "construction_reason",
        "ground_truth",
    )
    for path in sorted(AGENT_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        hit = sorted(token for token in forbidden if token in source)
        assert not hit, f"{path} mentions {hit}"


def test_inv_a_the_gate_does_not_reach_back_into_the_agent() -> None:
    for path, modules in imported_modules(GATE_SRC).items():
        hit = sorted(name for name in modules if name.startswith("rulearena_refund_agent"))
        assert not hit, f"{path} imports {hit}"


def test_inv_b_the_gate_imports_nothing_that_could_judge_semantics() -> None:
    """An allowlist, not a denylist: a new dependency is a decision, not an accident."""
    allowed = {
        "__future__",
        "collections",
        "decimal",
        "enum",
        "typing",
        "urllib",
        "httpx",
        "pydantic",
        "rulearena_domain_contracts",
        "rulearena_reference_simulator",
    }
    for path, modules in imported_modules(GATE_SRC).items():
        for name in modules:
            top = name.split(".", 1)[0]
            assert top in allowed, f"{path} imports {name}, which is not on the allowlist"


def test_inv_b_the_gate_has_no_write_surface() -> None:
    """Three read-only checks. No method that could change business state."""
    public = {
        name
        for name, member in vars(RuntimeGate).items()
        if not name.startswith("_") and callable(member)
    }
    assert public == {"pre_check", "retry_check", "post_check"}


def test_the_gate_satisfies_the_agent_side_protocol_structurally() -> None:
    """The seam works without either package naming the other."""
    assert isinstance(RuntimeGate("http://sandbox", "x" * 32), WriteGuard)


@pytest.mark.parametrize("name", ["pre_check", "retry_check", "post_check"])
def test_the_gate_checks_are_awaitable(name: str) -> None:
    assert inspect.iscoroutinefunction(getattr(RuntimeGate, name))
