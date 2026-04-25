from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    service_name: str = "identity_service"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8010
    log_level: str = "INFO"

    internal_service_secret: str = ""
    database_url: str = ""
    redis_url: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
