import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from rulearena_attack_runtime import (
    LLMAdapter,
    OpenAICompatibleLLMAdapter,
    PostgresRuleVersionStore,
    PostgresRuntimeStore,
    RuleCompiler,
    RuntimeStore,
    UnavailableLLMAdapter,
    VersionStore,
)
from rulearena_evaluation import BenchmarkStore, PostgresBenchmarkStore
from rulearena_observability import (
    ControlSettings,
    PostgresTraceStore,
    TraceSink,
    configure_logging,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .api import NullRunEnqueuer, PolicyService, RunEnqueuer, runtime_router
from .queue import ArqRunEnqueuer

ReadinessProbe = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


def create_app(
    settings: ControlSettings | None = None,
    readiness_probe: ReadinessProbe | None = None,
    *,
    compiler: RuleCompiler | None = None,
    runtime_store: RuntimeStore | None = None,
    version_store: VersionStore | None = None,
    run_enqueuer: RunEnqueuer | None = None,
    benchmark_store: BenchmarkStore | None = None,
    trace_store: TraceSink | None = None,
) -> FastAPI:
    resolved = settings or ControlSettings()
    configure_logging(resolved.log_level)
    engine: AsyncEngine | None = None
    redis: Redis | None = None
    owns_runtime_store = runtime_store is None
    owns_version_store = version_store is None
    owns_benchmark_store = benchmark_store is None
    owns_trace_store = trace_store is None
    enqueuer = run_enqueuer or (
        NullRunEnqueuer()
        if runtime_store is not None
        else ArqRunEnqueuer(str(resolved.redis_url))
    )
    selected_runtime_store = runtime_store or PostgresRuntimeStore(str(resolved.database_url))
    selected_version_store = version_store or PostgresRuleVersionStore(str(resolved.database_url))
    selected_benchmark_store = benchmark_store or PostgresBenchmarkStore(
        str(resolved.database_url)
    )
    selected_trace_store = trace_store or PostgresTraceStore(str(resolved.database_url))
    if compiler is None:
        if resolved.llm_base_url and resolved.llm_api_key and resolved.llm_model:
            adapter: LLMAdapter = OpenAICompatibleLLMAdapter(
                base_url=resolved.llm_base_url,
                api_key=resolved.llm_api_key.get_secret_value(),
                model=resolved.llm_model,
                timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120") or 120),
            )
        else:
            adapter = UnavailableLLMAdapter()
        compiler = RuleCompiler(adapter)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal engine, redis, readiness_probe
        if readiness_probe is None:
            engine = create_async_engine(str(resolved.database_url), pool_pre_ping=True)
            redis = Redis.from_url(str(resolved.redis_url), decode_responses=True)

            async def probe() -> None:
                assert engine is not None and redis is not None
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                await redis.ping()

            readiness_probe = probe
        yield
        if redis is not None:
            await redis.aclose()
        if engine is not None:
            await engine.dispose()
        if owns_runtime_store and isinstance(selected_runtime_store, PostgresRuntimeStore):
            selected_runtime_store.close()
        if owns_version_store and isinstance(selected_version_store, PostgresRuleVersionStore):
            selected_version_store.close()
        if owns_benchmark_store and isinstance(selected_benchmark_store, PostgresBenchmarkStore):
            selected_benchmark_store.close()
        if owns_trace_store and isinstance(selected_trace_store, PostgresTraceStore):
            selected_trace_store.close()
        if isinstance(enqueuer, ArqRunEnqueuer):
            await enqueuer.close()

    app = FastAPI(title="RuleArena Control API", version="0.1.0", lifespan=lifespan)
    allowed_origins = [
        origin.strip()
        for origin in os.getenv("PUBLIC_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Idempotency-Key", "Content-Type", "Last-Event-ID"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Any]) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(
        runtime_router(
            PolicyService(compiler, selected_version_store),
            selected_runtime_store,
            enqueuer,
            selected_benchmark_store,
            selected_trace_store,
        )
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "alive", "service": "control-api"}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict[str, str]:
        assert readiness_probe is not None
        try:
            await readiness_probe()
        except Exception:
            logger.exception("readiness dependency check failed")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not-ready", "service": "control-api"}
        return {"status": "ready", "service": "control-api"}

    return app
