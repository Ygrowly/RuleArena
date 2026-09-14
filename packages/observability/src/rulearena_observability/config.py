from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    environment: Literal["development", "test", "production"] = "development"
    redis_url: RedisDsn
    internal_service_token: SecretStr = Field(min_length=32)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class ControlSettings(BaseServiceSettings):
    database_url: PostgresDsn = Field(validation_alias="CONTROL_DATABASE_URL")
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None


class SandboxSettings(BaseServiceSettings):
    database_url: PostgresDsn = Field(validation_alias="SANDBOX_DATABASE_URL")
    # How long the sandbox holds a `REFUND_ACK_LOST` write's response open before
    # answering 504. The caller has to reach its own timeout for the defect to mean
    # "timed out, but the money moved", so this must stay above every caller's tool
    # timeout and above the platform's own request timeout.
    ack_lost_delay_seconds: float = Field(
        default=12.0, gt=0, validation_alias="SANDBOX_ACK_LOST_DELAY_SECONDS"
    )
