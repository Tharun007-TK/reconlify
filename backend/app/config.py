"""
Application configuration via Pydantic Settings.
All values sourced from environment variables / Doppler secrets.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── App ─────────────────────────────────────────────────────────────────
    APP_VERSION: str = "4.0.0"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    DEBUG: bool = False

    # ── Supabase ─────────────────────────────────────────────────────────────
    SUPABASE_URL: AnyHttpUrl
    SUPABASE_SERVICE_ROLE_KEY: str          # Full access, bypasses RLS
    SUPABASE_ANON_KEY: str                  # For client-facing storage signed URLs
    SUPABASE_JWT_SECRET: str                # For JWT verification

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str                       # asyncpg DSN: postgresql+asyncpg://...
    DB_POOL_MIN_SIZE: int = 5
    DB_POOL_MAX_SIZE: int = 20
    DB_COMMAND_TIMEOUT: float = 30.0

    # ── Redis / ARQ ──────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    WORKER_CONCURRENCY: int = 5

    # ── Security ─────────────────────────────────────────────────────────────
    INTERNAL_API_SECRET: str                # Service-to-service shared secret
    CORS_ORIGINS: str | list[str] = ["http://localhost:3000"]

    # ── Storage ──────────────────────────────────────────────────────────────
    STORAGE_BUCKET_UPLOADS: str = "uploads"
    STORAGE_BUCKET_PROCESSED: str = "processed"
    STORAGE_BUCKET_REPORTS: str = "reports"
    SIGNED_URL_EXPIRY_SECONDS: int = 900    # 15 minutes

    # ── Upload limits ─────────────────────────────────────────────────────────
    MAX_UPLOAD_SIZE_MB: int = 25
    ALLOWED_MIME_TYPES: list[str] = Field(default=[
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/csv",
        "application/json",
    ])

    # ── Reconlify CLI ─────────────────────────────────────────────────────────
    RECONLIFY_CLI_PATH: str = "/usr/local/bin/reconlify"
    RECONLIFY_LICENSE_KEY: str = ""
    RECONLIFY_TMP_DIR: str = "/tmp/reconlify"
    RECONLIFY_TIMEOUT_SECONDS: int = 1200  # 20 minutes

    # ── Email ─────────────────────────────────────────────────────────────────
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "noreply@recko.app"
    EMAIL_FROM_NAME: str = "Recko Platform"

    # ── Observability ─────────────────────────────────────────────────────────
    SENTRY_DSN: str = ""
    LOG_LEVEL: str = "INFO"

    # ── Report settings ───────────────────────────────────────────────────────
    REPORT_EXPIRY_DAYS: int = 90
    REPORT_DOWNLOAD_TOKEN_EXPIRY_MINUTES: int = 15

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def database_url_sync(self) -> str:
        """Sync version for Alembic migrations."""
        return self.DATABASE_URL.replace("asyncpg", "psycopg2")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings: Settings = get_settings()
