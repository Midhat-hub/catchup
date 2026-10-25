"""Optional Resend-backed email outbox for completed push explanations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import escape
import logging
from urllib.parse import quote, urlsplit

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from psycopg.rows import dict_row

from backend.app.db import get_db_connection

logger = logging.getLogger(__name__)
RESEND_EMAILS_URL = "https://api.resend.com/emails"
MAX_NOTIFICATION_ATTEMPTS = 5
NOTIFICATION_LEASE_SECONDS = 300


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool
    api_key: str | None
    sender: str | None
    recipient: str | None
    app_base_url: str | None


class _EmailEnvironment(BaseSettings):
    email_notifications_enabled: str = "false"
    email_api_key: str | None = None
    email_from: str | None = None
    email_to: str | None = None
    app_base_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def get_email_settings() -> EmailSettings:
    environment = _EmailEnvironment()
    return EmailSettings(
        enabled=environment.email_notifications_enabled.strip().lower() in {"1", "true", "yes", "on"},
        api_key=environment.email_api_key.strip() if environment.email_api_key else None,
        sender=environment.email_from.strip() if environment.email_from else None,
        recipient=environment.email_to.strip() if environment.email_to else None,
        app_base_url=environment.app_base_url.strip().rstrip("/") if environment.app_base_url else None,
    )


def notifications_configured(settings: EmailSettings | None = None) -> bool:
    config = settings or get_email_settings()
    return bool(config.enabled and config.api_key and config.sender and config.recipient)


def claim_notification() -> dict | None:
    """Claim one outbox row, reclaiming rows abandoned by a dead worker."""
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE push_email_notifications
                    SET status = 'failed', updated_at = CURRENT_TIMESTAMP,
                        error = COALESCE(error, 'Email delivery lease expired after maximum attempts.')
                    WHERE status = 'running'
                      AND updated_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                      AND attempts >= %s
                    """,
                    (NOTIFICATION_LEASE_SECONDS, MAX_NOTIFICATION_ATTEMPTS),
                )
                cur.execute(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM push_email_notifications
                        WHERE attempts < %s
                          AND (
                            (status = 'pending' AND next_attempt_at <= CURRENT_TIMESTAMP)
                            OR (status = 'running' AND updated_at < CURRENT_TIMESTAMP
                                - (%s * INTERVAL '1 second'))
                          )
                        ORDER BY created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE push_email_notifications AS notification
                    SET status = 'running', attempts = notification.attempts + 1,
                        error = NULL, updated_at = CURRENT_TIMESTAMP
                    FROM candidate
                    WHERE notification.id = candidate.id
                    RETURNING notification.*
                    """,
                    (MAX_NOTIFICATION_ATTEMPTS, NOTIFICATION_LEASE_SECONDS),
                )
                return cur.fetchone()


def send_notification(item: dict, settings: EmailSettings) -> str | None:
    """Send using Resend's HTTP API; the stable key protects uncertain retries."""
    if not notifications_configured(settings):
        raise RuntimeError("Email notifications are not fully configured.")
    repository = item["repository"]
    before_commit = item["before_commit"]
    after_commit = item["after_commit"]
    compare_url = (
        f"https://github.com/{repository}/compare/"
        f"{quote(before_commit, safe='')}...{quote(after_commit, safe='')}"
    )
    branch = item.get("branch")
    app_url = None
    base_url = urlsplit(settings.app_base_url or "")
    if base_url.scheme in {"https", "http"} and base_url.hostname and not base_url.query and not base_url.fragment:
        app_url = f"{settings.app_base_url}/workspace?repo={quote(repository, safe='/')}"

    details = [
        f"Repository: {repository}",
        f"Branch: {branch}" if branch else None,
        f"Previous commit: {before_commit}",
        f"New commit: {after_commit}",
        "",
        item["summary"],
        "",
        f"GitHub comparison: {compare_url}",
        f"Catchup workspace: {app_url}" if app_url else None,
    ]
    text_body = "\n".join(line for line in details if line is not None)
    html_body = (
        f"<h2>{escape(repository)} push explained</h2>"
        f"<p><b>Branch:</b> {escape(branch or 'Not provided')}<br>"
        f"<b>Previous commit:</b> <code>{escape(before_commit)}</code><br>"
        f"<b>New commit:</b> <code>{escape(after_commit)}</code></p>"
        f"<p>{escape(item['summary'])}</p>"
        f'<p><a href="{escape(compare_url, quote=True)}">View GitHub comparison</a></p>'
        + (
            f'<p><a href="{escape(app_url, quote=True)}">Open Catchup</a></p>'
            if app_url
            else ""
        )
    )
    response = httpx.post(
        RESEND_EMAILS_URL,
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"catchup-push-job-{item['job_id']}",
        },
        json={
            "from": settings.sender,
            "to": [settings.recipient],
            "subject": f"Catchup: {repository} push explained",
            "text": text_body,
            "html": html_body,
        },
        timeout=20.0,
    )
    response.raise_for_status()
    body = response.json()
    return body.get("id") if isinstance(body, dict) else None


def mark_notification_sent(notification_id: int, provider_id: str | None) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE push_email_notifications
                   SET status = 'sent', provider_message_id = %s, error = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s AND status = 'running'""",
                (provider_id, notification_id),
            )


def mark_notification_failed(notification_id: int, attempts: int, error: Exception) -> None:
    safe_error = f"{type(error).__name__}: {error}"[:1000]
    next_status = "pending" if attempts < MAX_NOTIFICATION_ATTEMPTS else "failed"
    retry_seconds = min(3600, 30 * (2 ** max(0, attempts - 1)))
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE push_email_notifications
                   SET status = %s, error = %s,
                       next_attempt_at = CASE WHEN %s = 'pending'
                         THEN CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                         ELSE next_attempt_at END,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s AND status = 'running'""",
                (next_status, safe_error, next_status, retry_seconds, notification_id),
            )


def process_next_notification(
    *,
    settings: EmailSettings | None = None,
    claim_function=claim_notification,
    send_function=send_notification,
    sent_function=mark_notification_sent,
    failed_function=mark_notification_failed,
) -> bool:
    config = settings or get_email_settings()
    if not notifications_configured(config):
        return False
    item = claim_function()
    if item is None:
        return False
    try:
        provider_id = send_function(item, config)
        sent_function(item["id"], provider_id)
    except Exception as exc:
        failed_function(item["id"], item["attempts"], exc)
        logger.warning(
            "Email notification delivery failed outbox_id=%s attempt=%s/%s error=%s",
            item["id"], item["attempts"], MAX_NOTIFICATION_ATTEMPTS, type(exc).__name__,
        )
        return True
    logger.info("Sent push explanation email outbox_id=%s", item["id"])
    return True


async def email_notification_loop(idle_seconds: float = 5.0) -> None:
    """Deliver outbox notifications separately from the explanation worker."""
    while True:
        try:
            processed = await asyncio.to_thread(process_next_notification)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Email notification worker cycle failed")
            processed = False
        if not processed:
            await asyncio.sleep(idle_seconds)
