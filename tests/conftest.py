import os
from collections.abc import Iterator

import pytest
from rulearena_observability import ControlSettings, SandboxSettings


@pytest.fixture
def sandbox_http_url() -> str:
    url = os.getenv("SANDBOX_HTTP_URL")
    if not url:
        pytest.skip("set SANDBOX_HTTP_URL to run real PostgreSQL-backed HTTP tests")
    return url.rstrip("/")


@pytest.fixture
def sandbox_token() -> str:
    return os.getenv("SANDBOX_TEST_TOKEN", "local-internal-token-32-characters")


@pytest.fixture
def control_settings() -> ControlSettings:
    return ControlSettings(
        CONTROL_DATABASE_URL="postgresql+asyncpg://control:test@localhost/rulearena",
        redis_url="redis://localhost:6379/0",
        internal_service_token="x" * 32,
        environment="test",
    )


@pytest.fixture
def sandbox_settings() -> SandboxSettings:
    return SandboxSettings(
        SANDBOX_DATABASE_URL="postgresql+asyncpg://sandbox:test@localhost/rulearena",
        redis_url="redis://localhost:6379/0",
        internal_service_token="x" * 32,
        environment="test",
    )


@pytest.fixture(autouse=True)
def clear_required_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "CONTROL_DATABASE_URL",
        "SANDBOX_DATABASE_URL",
        "REDIS_URL",
        "INTERNAL_SERVICE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
