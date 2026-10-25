"""Offline tests for optional, retryable push explanation email delivery."""

from unittest.mock import Mock
from pathlib import Path

from backend.app.delivery import email_notifications as notifications


def _settings(**overrides):
    values = {
        "enabled": True,
        "api_key": "test-key",
        "sender": "catchup@example.com",
        "recipient": "developer@example.com",
        "app_base_url": "https://catchup.example.com",
    }
    values.update(overrides)
    return notifications.EmailSettings(**values)


def _item():
    return {
        "id": 4,
        "job_id": 23,
        "repository": "owner/repo",
        "branch": "main",
        "before_commit": "a" * 40,
        "after_commit": "b" * 40,
        "summary": "The update adds checkout validation.",
        "attempts": 1,
    }


def test_successful_delivery_sends_expected_content_and_stable_idempotency_key(monkeypatch):
    response = Mock()
    response.json.return_value = {"id": "email-123"}
    http_post = Mock(return_value=response)
    monkeypatch.setattr(notifications.httpx, "post", http_post)

    provider_id = notifications.send_notification(_item(), _settings())

    assert provider_id == "email-123"
    args, kwargs = http_post.call_args
    assert args == (notifications.RESEND_EMAILS_URL,)
    assert kwargs["headers"]["Idempotency-Key"] == "catchup-push-job-23"
    assert "owner/repo" in kwargs["json"]["text"]
    assert "main" in kwargs["json"]["text"]
    assert "a" * 40 in kwargs["json"]["text"]
    assert "b" * 40 in kwargs["json"]["text"]
    assert "github.com/owner/repo/compare" in kwargs["json"]["text"]
    assert "catchup.example.com/workspace" in kwargs["json"]["text"]
    response.raise_for_status.assert_called_once()


def test_disabled_or_incomplete_email_configuration_does_not_claim(monkeypatch):
    claim = Mock(side_effect=AssertionError("must not claim when disabled"))

    result = notifications.process_next_notification(
        settings=_settings(enabled=False),
        claim_function=claim,
    )
    missing_credentials = notifications.process_next_notification(
        settings=_settings(api_key=None),
        claim_function=claim,
    )

    assert result is False
    assert missing_credentials is False
    claim.assert_not_called()


def test_email_configuration_reads_environment_variables(monkeypatch):
    monkeypatch.setenv("EMAIL_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("EMAIL_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_FROM", "catchup@example.com")
    monkeypatch.setenv("EMAIL_TO", "developer@example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://catchup.example.com/")

    settings = notifications.get_email_settings()

    assert notifications.notifications_configured(settings)
    assert settings.app_base_url == "https://catchup.example.com"


def test_delivery_failure_is_recorded_for_retry_without_marking_sent():
    sent = Mock()
    failed = Mock()

    processed = notifications.process_next_notification(
        settings=_settings(),
        claim_function=lambda: _item(),
        send_function=Mock(side_effect=RuntimeError("provider unavailable")),
        sent_function=sent,
        failed_function=failed,
    )

    assert processed is True
    sent.assert_not_called()
    failed.assert_called_once()
    assert failed.call_args.args[:2] == (4, 1)
    assert str(failed.call_args.args[2]) == "provider unavailable"


def test_successful_delivery_marks_the_outbox_row_sent():
    sent = Mock()
    failed = Mock()

    processed = notifications.process_next_notification(
        settings=_settings(),
        claim_function=lambda: _item(),
        send_function=Mock(return_value="email-123"),
        sent_function=sent,
        failed_function=failed,
    )

    assert processed is True
    sent.assert_called_once_with(4, "email-123")
    failed.assert_not_called()


def test_outbox_schema_enforces_one_row_per_completed_job():
    schema_path = Path(__file__).parents[1] / "app" / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")

    table_sql = schema.split("CREATE TABLE IF NOT EXISTS push_email_notifications", 1)[1]
    table_sql = table_sql.split(";", 1)[0]
    assert "job_id INTEGER NOT NULL UNIQUE" in table_sql
