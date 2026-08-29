import pytest
from pydantic import ValidationError
from rulearena_observability import ControlSettings, SandboxSettings


@pytest.mark.parametrize("settings_type", [ControlSettings, SandboxSettings])
def test_missing_required_configuration_fails_fast(settings_type: type[object]) -> None:
    with pytest.raises(ValidationError) as error:
        settings_type()  # type: ignore[call-arg]
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

