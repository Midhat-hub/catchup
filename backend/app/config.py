"""Configuration and settings management for Catchup.

Centralizes environment variable loading using Pydantic Settings.
Never hard-codes secrets or credentials.
"""

from functools import lru_cache
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application and database settings loaded from environment variables."""

    # Application settings
    app_name: str = Field(default="Catchup", description="Name of the application")
    app_env: str = Field(default="development", description="Environment: development, test, production")
    log_level: str = Field(default="INFO", description="Logging level")
    app_host: str = Field(default="0.0.0.0", description="Application bind host")
    app_port: int = Field(default=8000, description="Application bind port")

    # PostgreSQL & pgvector Database settings
    postgres_user: str = Field(default="catchup", description="PostgreSQL database user")
    postgres_password: str = Field(default="catchup_dev_secret", description="PostgreSQL database password")
    postgres_host: str = Field(default="localhost", description="PostgreSQL database host")
    postgres_port: int = Field(default=5433, description="PostgreSQL database port")
    postgres_db: str = Field(default="catchup", description="PostgreSQL database name")
    database_url: Optional[str] = Field(default=None, description="Full database URL override")

    # Future integration settings (placeholders per PROJECT2.md)
    github_token: Optional[str] = Field(default=None, description="GitHub personal access token (read-only)")
    gemini_api_key: Optional[str] = Field(default=None, description="Gemini Flash API key")
    notification_email: Optional[str] = Field(default=None, description="Notification recipient email")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("app_port", "postgres_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate network port numbers."""
        if not (1 <= v <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate logging levels."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper_v = v.upper()
        if upper_v not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return upper_v

    def get_database_url(self) -> str:
        """Return the PostgreSQL connection string.

        Uses explicit DATABASE_URL if provided; otherwise constructs from components.
        """
        if self.database_url:
            return self.database_url
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
