from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from rulearena_observability import SandboxSettings, configure_logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import create_engine, session_factory
from .errors import DomainError
from .schemas import ActionCommand, CreateRunRequest
from .service import SandboxService

ReadinessProbe = Callable[[], Awaitable[None]]
MAX_REQUEST_BYTES = 256 * 1024
logger = logging.getLogger(__name__)


def create_app(
    settings: SandboxSettings | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    resolved = settings or SandboxSettings()
    configure_logging(resolved.log_level)
    engine: AsyncEngine | None = None
    redis: Redis | None = None
    service: SandboxService | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal engine, redis, service, readiness_probe
        engine = create_engine(str(resolved.database_url))
        service = SandboxService(session_factory(engine))
        app.state.sandbox_service = service
        if readiness_probe is None:
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

    @app.middleware("http")
    async def request_middleware(
        request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                too_large = int(content_length) > MAX_REQUEST_BYTES
            except ValueError:
                too_large = True
            if too_large:
                oversized_response = JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"code": "REQUEST_TOO_LARGE", "message": "request body is too large"},
                )
                oversized_response.headers["X-Request-ID"] = request_id
                return oversized_response
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    async def require_internal_token(request: Request) -> None:
        supplied = request.headers.get("X-Internal-Service-Token", "")
        expected = resolved.internal_service_token.get_secret_value()
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "internal service token is invalid"},
            )

    def get_service(request: Request) -> SandboxService:
        current = getattr(request.app.state, "sandbox_service", None)
        if not isinstance(current, SandboxService):
            raise HTTPException(status_code=503, detail={"code": "SERVICE_STARTING"})
        return current

    def domain_http_error(error: DomainError) -> HTTPException:
        code_to_status = {
            "RUN_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "RECEIPT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "SCENARIO_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "IDEMPOTENCY_KEY_REQUIRED": status.HTTP_422_UNPROCESSABLE_ENTITY,
        }
        return HTTPException(
            status_code=code_to_status.get(error.code, status.HTTP_409_CONFLICT),
            detail={"code": error.code, "message": error.message, "details": error.details},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "alive", "service": "commerce-sandbox"}

    @app.get("/readyz")
    async def readyz(request: Request, response: Response) -> dict[str, str]:
        assert readiness_probe is not None
        try:
            await readiness_probe()
        except Exception:
            logger.exception(
                "readiness dependency check failed",
                extra={"request_id": getattr(request.state, "request_id", None)},
            )
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not-ready", "service": "commerce-sandbox"}
        return {"status": "ready", "service": "commerce-sandbox"}

    @app.post(
        "/internal/runs",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_internal_token)],
    )
    async def create_run(
        payload: CreateRunRequest,
        sandbox: SandboxService = Depends(get_service),  # noqa: B008
    ) -> dict[str, object]:
        try:
            response = await sandbox.create_run(payload)
        except DomainError as error:
            raise domain_http_error(error) from error
        return response.model_dump(mode="json")

    @app.post(
        "/internal/runs/{run_id}/reset",
        dependencies=[Depends(require_internal_token)],
    )
    async def reset_run(
        run_id: str,
        sandbox: SandboxService = Depends(get_service),  # noqa: B008
    ) -> dict[str, object]:
        try:
            return await sandbox.reset_run(run_id)
        except DomainError as error:
            raise domain_http_error(error) from error

    @app.post(
        "/internal/runs/{run_id}/actions",
        dependencies=[Depends(require_internal_token)],
    )
    async def execute_action(
        run_id: str,
        command: ActionCommand,
        sandbox: SandboxService = Depends(get_service),  # noqa: B008
    ) -> dict[str, object]:
        try:
            receipt = await sandbox.execute(run_id, command)
        except DomainError as error:
            raise domain_http_error(error) from error
        return receipt.model_dump(mode="json")

    @app.get(
        "/internal/runs/{run_id}/snapshot",
        dependencies=[Depends(require_internal_token)],
    )
    async def get_snapshot(
        run_id: str,
        sandbox: SandboxService = Depends(get_service),  # noqa: B008
    ) -> dict[str, object]:
        try:
            return await sandbox.get_snapshot(run_id)
        except DomainError as error:
            raise domain_http_error(error) from error

    @app.get(
        "/internal/runs/{run_id}/events",
        dependencies=[Depends(require_internal_token)],
    )
    async def get_events(
        run_id: str,
        sandbox: SandboxService = Depends(get_service),  # noqa: B008
    ) -> dict[str, object]:
        try:
            return {"events": await sandbox.get_events(run_id)}
        except DomainError as error:
            raise domain_http_error(error) from error

    @app.get(
        "/internal/runs/{run_id}/receipts/{key:path}",
        dependencies=[Depends(require_internal_token)],
    )
    async def get_receipt(
        run_id: str,
        key: str,
        sandbox: SandboxService = Depends(get_service),  # noqa: B008
    ) -> dict[str, object]:
        try:
            receipt = await sandbox.get_receipt(run_id, key)
        except DomainError as error:
            raise domain_http_error(error) from error
        return receipt.model_dump(mode="json")

    return app
