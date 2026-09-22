from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Secrets and connection strings load from environment / .env — never from source."""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env", BACKEND_ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Fleet Command Services API"
    environment: str = Field(default="development", validation_alias=AliasChoices("ENVIRONMENT", "environment"))
    secret_key: str = Field(
        default="dev-only-change-me-in-production",
        validation_alias=AliasChoices("SECRET_KEY", "secret_key"),
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(
        default=60 * 8,
        validation_alias=AliasChoices("ACCESS_TOKEN_EXPIRE_MINUTES", "access_token_expire_minutes"),
    )
    database_url: str = Field(
        default="sqlite:///./fleet.db",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )
    # Demo seeding is opt-in. It creates well-known accounts, so it must never
    # be the default and never run against production.
    seed_demo_data: bool = Field(
        default=False, validation_alias=AliasChoices("SEED_DEMO_DATA", "seed_demo_data")
    )
    skip_seed: bool = Field(default=False, validation_alias=AliasChoices("SKIP_SEED", "skip_seed"))
    # Accounts that may never be deleted or deactivated, comma-separated.
    protected_admin_emails: str = Field(
        default="",
        validation_alias=AliasChoices("PROTECTED_ADMIN_EMAILS", "protected_admin_emails"),
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"),
    )
    storage_backend: str = Field(
        default="local",
        validation_alias=AliasChoices("STORAGE_BACKEND", "storage_backend"),
    )
    storage_root: str | None = Field(default=None, validation_alias=AliasChoices("STORAGE_ROOT", "storage_root"))
    s3_endpoint_url: str | None = Field(default=None, validation_alias=AliasChoices("S3_ENDPOINT_URL", "s3_endpoint_url"))
    s3_bucket: str | None = Field(default=None, validation_alias=AliasChoices("S3_BUCKET", "s3_bucket"))
    s3_access_key_id: str | None = Field(default=None, validation_alias=AliasChoices("S3_ACCESS_KEY_ID", "s3_access_key_id"))
    s3_secret_access_key: str | None = Field(
        default=None, validation_alias=AliasChoices("S3_SECRET_ACCESS_KEY", "s3_secret_access_key")
    )
    s3_region: str = Field(default="auto", validation_alias=AliasChoices("S3_REGION", "s3_region"))
    s3_prefix: str = Field(default="fleet-uploads", validation_alias=AliasChoices("S3_PREFIX", "s3_prefix"))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def protected_admin_email_set(self) -> set[str]:
        return {email.strip().lower() for email in self.protected_admin_emails.split(",") if email.strip()}

    def should_seed_demo_data(self) -> bool:
        """Demo data requires an explicit opt-in and never runs in production."""
        return self.seed_demo_data and not self.skip_seed and not self.is_production

    @model_validator(mode="after")
    def reject_placeholder_secrets_in_production(self):
        if self.is_production:
            key = self.secret_key.lower()
            if not self.secret_key or key.startswith("dev-only") or "change-me" in key or key.startswith("local-dev"):
                raise ValueError("SECRET_KEY must be a strong production secret when ENVIRONMENT=production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("DATABASE_URL must point at PostgreSQL when ENVIRONMENT=production")
            if self.seed_demo_data:
                raise ValueError("SEED_DEMO_DATA must be false when ENVIRONMENT=production")
            if "*" in self.cors_origin_list:
                raise ValueError("CORS_ORIGINS must list explicit origins when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
