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
