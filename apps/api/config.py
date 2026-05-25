"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    # Bind to all interfaces so the server is reachable from Docker / ngrok
    # in development. Production deploys put this behind a reverse proxy.
    api_host: str = "0.0.0.0"  # noqa: S104
    api_port: int = 8000

    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    redis_url: str = "redis://localhost:6379/0"

    anthropic_api_key: str = ""

    github_app_id: str = ""
    github_app_private_key_path: Path = Path("./secrets/github-app.pem")
    github_webhook_secret: str = ""

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "sentinel-api"
    otel_traces_sampler: str = "parentbased_always_on"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
