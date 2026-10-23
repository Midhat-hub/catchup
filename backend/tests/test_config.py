"""Unit tests for configuration loading and validation."""

import pytest
from pydantic import ValidationError
from backend.app.config import Settings


def test_default_settings(monkeypatch):
    """Verify default settings values when no env overrides are present."""
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.app_name == "Catchup"
    assert settings.app_env in {"development", "test", "production"}
    assert settings.postgres_port == 5433
    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert settings.github_poll_interval_seconds == 180
    assert settings.job_worker_idle_seconds == 2.0
    assert settings.job_lease_seconds == 300
    assert settings.job_max_attempts == 4
    assert "postgresql://" in settings.get_database_url()


def test_port_validation():
    """Verify invalid port raises validation error."""
    with pytest.raises(ValidationError):
        Settings(postgres_port=0)

    with pytest.raises(ValidationError):
        Settings(postgres_port=70000)


def test_log_level_validation():
    """Verify invalid log level raises validation error."""
    with pytest.raises(ValidationError):
        Settings(log_level="INVALID_LEVEL")

    valid = Settings(log_level="debug")
    assert valid.log_level == "DEBUG"


def test_custom_database_url():
    """Verify explicit database_url override is respected."""
    custom_url = "postgresql://user:pass@remote:5433/custom_db"
    settings = Settings(database_url=custom_url)
    assert settings.get_database_url() == custom_url
