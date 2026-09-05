from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from rulearena_observability import ControlSettings, SandboxSettings


@pytest.fixture()
def no_env_file() -> Iterator[None]:
    """Temporarily hide the repo-root .env so settings load nothing from disk."""
    env_file = Path(".env")
    hidden = env_file.with_name(".env.hidden-by-test")
    if env_file.exists():
        env_file.rename(hidden)
        try:
            yield
        finally:
            hidden.rename(env_file)
    else:
        yield


@pytest.mark.usefixtures("no_env_file")
@pytest.mark.parametrize("settings_type", [ControlSettings, SandboxSettings])
def test_missing_required_configuration_fails_fast(
    settings_type: type[ControlSettings] | type[SandboxSettings],
) -> None:
    with pytest.raises(ValidationError) as error:
        settings_type()
    message = str(error.value)
    assert "DATABASE_URL" in message
    assert "redis_url" in message
    assert "internal_service_token" in message


def test_short_internal_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        ControlSettings(
            CONTROL_DATABASE_URL="postgresql+asyncpg://control:test@localhost/db",
            redis_url="redis://localhost:6379/0",
            internal_service_token="weak",
        )
