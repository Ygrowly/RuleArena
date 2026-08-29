import ast
from pathlib import Path


def test_simulator_and_oracle_do_not_import_sandbox() -> None:
    roots = (Path("packages/reference_simulator/src"), Path("packages/oracle/src"))
    forbidden = ("rulearena_commerce_sandbox", "services.commerce_sandbox")
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            assert not any(name.startswith(forbidden) for name in imports), path


def test_oracle_source_has_no_profile_ground_truth_branch() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("packages/oracle/src").rglob("*.py")
    ).casefold()
    assert "sandbox_profile" not in source
    assert "sandbox_version" not in source
    assert "vulnerable" not in source
