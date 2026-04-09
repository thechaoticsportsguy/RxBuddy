"""Centralised configuration via pydantic BaseSettings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # backend/core/../../ = project root


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    ANTHROPIC_API_KEY: str = ""
    REDIS_URL: str = ""
    CORS_ORIGINS: str = ""
    GEMINI_API_KEY: str = ""

    model_config = {
        "env_file": str(_PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # ── Derived URLs ────────────────────────────────────────────────────────

    @property
    def async_database_url(self) -> str:
        """Database URL for async SQLAlchemy (asyncpg driver)."""
        url = self.DATABASE_URL
        if not url:
            raise RuntimeError(
                "DATABASE_URL is missing. Create a .env file in the project root with:\n"
                "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:6767/rxbuddy"
            )
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql+psycopg://"):
            return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sync_database_url(self) -> str:
        """Database URL for sync SQLAlchemy (psycopg driver)."""
        url = self.DATABASE_URL
        if not url:
            raise RuntimeError("DATABASE_URL is missing.")
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY.strip())

    @property
    def cors_origins_list(self) -> list[str]:
        origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        origins += ["http://localhost:3000", "http://127.0.0.1:3000"]
        return origins


settings = Settings()
