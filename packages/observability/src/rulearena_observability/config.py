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
