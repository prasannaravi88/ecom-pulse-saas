from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Railway/Render hand out `postgres://` or `postgresql://`; SQLAlchemy needs the psycopg 3 driver."""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (or backend/.env locally)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL in production. Falls back to a local SQLite file for no-Docker development.
    database_url: str = "sqlite:///./ecom_pulse.db"
    # Comma-separated exact origins allowed to call the API, e.g.
    # "https://ecom-pulse.vercel.app,https://pulse.example.com". Empty = no browser origins.
    allowed_origins: str = ""
    # Optional regex for origins that can't be listed up front (Vercel preview URLs), e.g.
    # "https://ecom-pulse-[a-z0-9-]+-yourteam\\.vercel\\.app".
    allowed_origin_regex: str | None = None
    # Public demo: seeded tenants are read-only; visitor uploads go to a private, expiring sandbox.
    demo_mode: bool = False
    sandbox_ttl_hours: int = 24
    max_sandboxes: int = 200
    seed_on_startup: bool = True
    max_upload_bytes: int = 20 * 1024 * 1024
    # Display-only conversion rate for the ₹/$ toggle (1 USD = N INR). Stored data is never converted.
    usd_inr_rate: float = 84.0

    @field_validator("database_url")
    @classmethod
    def _db_url(cls, v: str) -> str:
        return normalize_database_url(v)

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
