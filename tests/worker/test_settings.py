import pytest
from rulearena_attack_runtime import AttackWorker, PostgresRuntimeStore
from rulearena_attack_worker.settings import shutdown, startup


@pytest.mark.asyncio
async def test_worker_startup_fails_closed_without_model_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CONTROL_DATABASE_URL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "SANDBOX_HTTP_URL",
        "INTERNAL_SERVICE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="CONTROL_DATABASE_URL"):
        await startup({})


@pytest.mark.asyncio
async def test_worker_startup_builds_three_distinct_isolated_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "CONTROL_DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/rulearena",
        "LLM_BASE_URL": "https://model.invalid/v1",
        "LLM_API_KEY": "secret",
        "LLM_MODEL": "structured-model",
        "SANDBOX_HTTP_URL": "http://sandbox.internal",
        "INTERNAL_SERVICE_TOKEN": "x" * 32,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    context: dict[str, object] = {}
    await startup(context)
    runtime = context["runtime"]
    assert isinstance(runtime, AttackWorker)
    assert len({id(agent) for agent in runtime.agents.values()}) == 3
    assert isinstance(context["runtime_store"], PostgresRuntimeStore)
    await shutdown(context)


@pytest.mark.asyncio
async def test_execute_attack_accepts_json_serialized_rule_spec() -> None:
    """The ARQ payload arrives JSON-encoded (Decimal as string); the worker must
    validate it in JSON mode, matching the queue serialization."""
    import json as jsonlib

    from rulearena_attack_worker.jobs import execute_attack
    from rulearena_policy_schema import ScenarioType as _ScenarioType

    from tests.phase2_factories import rule_spec as _rule_spec

    seen: list[tuple[str, object]] = []

    class RecordingRuntime:
        async def run(self, run_id: str, rule_spec: object) -> None:
            seen.append((run_id, rule_spec))

    spec = _rule_spec(_ScenarioType.PROMOTION)
    payload = jsonlib.loads(spec.model_dump_json())
    await execute_attack({"runtime": RecordingRuntime()}, "run-json-1", payload)
    assert seen[0][0] == "run-json-1"
    assert seen[0][1] == spec
