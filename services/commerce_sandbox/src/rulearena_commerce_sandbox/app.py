import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from redis.asyncio import Redis
from rulearena_observability import SandboxSettings, configure_logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ReadinessProbe = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


def create_app(
    settings: SandboxSettings | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    resolved = settings or SandboxSettings()
    configure_logging(resolved.log_level)
    engine: AsyncEngine | None = None
    redis: Redis | None = None

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

    app = FastAPI(title="RuleArena Commerce Sandbox", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "alive", "service": "commerce-sandbox"}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict[str, str]:
        assert readiness_probe is not None
        try:
            await readiness_probe()
        except Exception:
            logger.exception("readiness dependency check failed")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not-ready", "service": "commerce-sandbox"}
        return {"status": "ready", "service": "commerce-sandbox"}

    return app
