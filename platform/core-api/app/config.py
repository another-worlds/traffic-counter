"""Settings for the core-api service (pydantic-settings; env-driven)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # postgresql+psycopg://user:pass@host:5432/db  (PostGIS-enabled)
    database_url: str = "postgresql+psycopg://model:model@localhost:5434/model"
    cors_origins: str = "*"
    env: str = "dev"

    # All geometry is stored and exchanged in WGS84 lon/lat.
    srid: int = 4326


settings = Settings()
