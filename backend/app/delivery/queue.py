"""PostgreSQL job creation, claiming, retry, and tracking-cursor operations."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db import get_db_connection

JOB_TYPE_EXPLAIN_PUSH = "explain_push"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobRecord:
    id: int
    payload: dict[str, Any]
    attempts: int


def tracked_repositories() -> list[dict[str, Any]]:
    """Return ready repositories with their durable commit tracking positions."""
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """SELECT id, name, mapped_commit_sha, last_explained_commit
                   FROM repos WHERE status = 'ready' ORDER BY id"""
            )
            return list(cursor.fetchall())


def establish_tracking_baseline(repo_id: int) -> str | None:
    """Initialize a legacy repository's cursor from its mapped SHA exactly once."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """UPDATE repos
                   SET last_explained_commit = mapped_commit_sha, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s AND status = 'ready'
                     AND last_explained_commit IS NULL AND mapped_commit_sha IS NOT NULL
                   RETURNING last_explained_commit""",
                (repo_id,),
            )
            row = cursor.fetchone()
            if row:
                return row[0]
            cursor.execute("SELECT last_explained_commit FROM repos WHERE id = %s", (repo_id,))
            current = cursor.fetchone()
            return current[0] if current else None


def enqueue_push_job(
    repo_id: int,
    repository: str,
    before_commit: str,
    after_commit: str,
    branch: str | None = None,
) -> int | None:
    """Durably enqueue one commit; the unique job index makes retries idempotent."""
    payload = {
        "type": JOB_TYPE_EXPLAIN_PUSH,
        "repo_id": repo_id,
        "repository": repository,
        "before_commit": before_commit,
        "after_commit": after_commit,
    }
    if branch:
        payload["branch"] = branch
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO jobs (payload, status)
                   VALUES (%s, 'pending') ON CONFLICT DO NOTHING RETURNING id""",
                (Jsonb(payload),),
            )
            row = cursor.fetchone()
            return row[0] if row else None

def claim_next_job(
    lease_seconds: int,
    max_attempts: int,
) -> JobRecord | None:
    """Atomically claim the next available job."""

    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET
                        status = 'failed',
                        error = COALESCE(
                            error,
                            'Worker lease expired after maximum attempts.'
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE status = 'running'
                      AND updated_at < CURRENT_TIMESTAMP
                          - (%s * INTERVAL '1 second')
                      AND attempts >= %s
                    """,
                    (lease_seconds, max_attempts),
                )

                cur.execute(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM jobs
                        WHERE attempts < %s
                          AND (
                              (
                                  status = 'pending'
                                  AND next_attempt_at <= CURRENT_TIMESTAMP
                              )
                              OR
                              (
                                  status = 'running'
                                  AND updated_at < CURRENT_TIMESTAMP
                                      - (%s * INTERVAL '1 second')
                              )
                          )
                        ORDER BY created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE jobs AS j
                    SET
                        status = 'running',
                        attempts = j.attempts + 1,
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    FROM candidate
                    WHERE j.id = candidate.id
                    RETURNING j.id, j.payload, j.attempts
                    """,
                    (max_attempts, lease_seconds),
                )

                row = cur.fetchone()

                if row is None:
                    return None

                return JobRecord(
                    id=row["id"],
                    payload=row["payload"],
                    attempts=row["attempts"],
                )



def retry_or_fail_job(
    job_id: int,
    attempts: int,
    max_attempts: int,
    error: str,
    retry_after_seconds: int = 30,
) -> str:
    """Schedule a retry or permanently fail a job after exhausting attempts."""

    safe_error = re.sub(
        r"(?i)\b(?:Bearer\s+)?AQ\.[A-Za-z0-9._~+/=-]+",
        "[REDACTED]",
        error,
    )
    safe_error = re.sub(
        r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1[REDACTED]",
        safe_error,
    )
    safe_error = safe_error[:2000]

    next_status = "pending" if attempts < max_attempts else "failed"
    delay_seconds = max(1, min(retry_after_seconds, 3600))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET
                    status = %s,
                    error = %s,
                    next_attempt_at = CASE
                        WHEN %s = 'pending'
                        THEN CURRENT_TIMESTAMP
                            + (%s * INTERVAL '1 second')
                        ELSE next_attempt_at
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status = 'running'
                """,
                (
                    next_status,
                    safe_error,
                    next_status,
                    delay_seconds,
                    job_id,
                ),
            )

            if cur.rowcount != 1:
                raise RuntimeError(
                    f"Could not update job {job_id}: it is no longer running."
                )

    return next_status



def complete_job(job: JobRecord) -> bool:
    """Complete a push job and advance its repository cursor atomically."""

    payload = job.payload

    if payload.get("type") != JOB_TYPE_EXPLAIN_PUSH:
        raise ValueError("Cannot complete an unsupported job type.")

    repo_id = int(payload["repo_id"])
    before_commit = payload["before_commit"]
    after_commit = payload["after_commit"]

    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE repos
                    SET last_explained_commit = %s
                    WHERE id = %s
                      AND status = 'ready'
                      AND last_explained_commit = %s
                    """,
                    (after_commit, repo_id, before_commit),
                )

                if cur.rowcount != 1:
                    return False

                cur.execute(
                    """
                    UPDATE jobs
                    SET
                        status = 'completed',
                        error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND status = 'running'
                    """,
                    (job.id,),
                )

                if cur.rowcount != 1:
                    raise RuntimeError(
                        f"Could not complete job {job.id}: "
                        "it is no longer running."
                    )

    return True
