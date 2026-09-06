from __future__ import annotations

import os
from typing import Any

from arq.connections import RedisSettings
from rulearena_attack_runtime import (
    AttackWorker,
    OpenAICompatibleLLMAdapter,
    PostgresRuntimeStore,
    SandboxReplayRunner,
    StrategyAgent,
    StrategyType,
    proposal_json_schema,
)
from rulearena_observability import PostgresTraceStore

from .jobs import execute_attack


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required by the attack worker")
    return value


async def startup(context: dict[str, Any]) -> None:
    """Compose the durable Runtime; missing model credentials fail startup explicitly."""

    database_url = _required("CONTROL_DATABASE_URL")
    base_url = _required("LLM_BASE_URL")
    api_key = _required("LLM_API_KEY")
    model = _required("LLM_MODEL")
    input_cost = float(os.getenv("LLM_INPUT_COST_PER_MTOKEN", "0") or 0)
    output_cost = float(os.getenv("LLM_OUTPUT_COST_PER_MTOKEN", "0") or 0)
    timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "120") or 120)
    store = PostgresRuntimeStore(database_url)
    trace_store = PostgresTraceStore(database_url)
    agents = {
        strategy: StrategyAgent(
            strategy,
            OpenAICompatibleLLMAdapter(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt_version=f"{strategy.value.casefold()}-v1",
                response_schema=proposal_json_schema(),
                schema_name="rulearena_agent_proposal",
                input_cost_per_million_tokens=input_cost,
                output_cost_per_million_tokens=output_cost,
                timeout_seconds=timeout_seconds,
            ),
        )
        for strategy in StrategyType
    }
    context["runtime_store"] = store
    context["trace_store"] = trace_store
    context["runtime"] = AttackWorker(
        store,
        SandboxReplayRunner(
            _required("SANDBOX_HTTP_URL"), _required("INTERNAL_SERVICE_TOKEN")
        ),
        agents,
        trace_sink=trace_store,
    )


async def shutdown(context: dict[str, Any]) -> None:
    store = context.get("runtime_store")
    if isinstance(store, PostgresRuntimeStore):
        store.close()
    trace_store = context.get("trace_store")
    if isinstance(trace_store, PostgresTraceStore):
        trace_store.close()


class WorkerSettings:
    functions = [execute_attack]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = 3
    job_timeout = 900
    keep_result = 3600
    max_tries = 3
