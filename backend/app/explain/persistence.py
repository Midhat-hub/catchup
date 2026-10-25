"""Transactional push and explanation persistence using the Step 1 schema."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db import get_db_connection
from backend.app.models import Evidence, Explanation
from backend.app.explain.models import PushHistoryItem


class RepositoryNotMappedError(RuntimeError):
    """The repository has no ready mapping for the requested push base."""


class MappingRevisionMismatchError(RuntimeError):
    """The stored mapping does not represent the push's before revision."""


def get_cached_explanation(repository: str, before_commit: str, after_commit: str) -> Explanation | None:
    """Return an already-completed explanation for this exact push, if present."""
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """SELECT explanation.content
                   FROM repos repo
                   JOIN pushes push ON push.repo_id = repo.id
                   JOIN explanations explanation ON explanation.push_id = push.id
                   WHERE lower(repo.name) = lower(%s)
                     AND push.before_commit = %s AND push.after_commit = %s
                     AND push.status = 'completed'
                   ORDER BY push.id DESC LIMIT 1""",
                (repository, before_commit, after_commit),
            )
            row = cursor.fetchone()
            return Explanation.model_validate(row["content"]) if row else None


def begin_push(repository: str, before_commit: str, after_commit: str) -> tuple[int, int, Explanation | None]:
    """Lock the repository row, validate its mapped base, and create/reuse a push row."""
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """SELECT id, status, mapped_commit_sha FROM repos
                       WHERE lower(name) = lower(%s) FOR UPDATE""",
                    (repository,),
                )
                repo = cursor.fetchone()
                if repo is None or repo["status"] != "ready":
                    raise RepositoryNotMappedError(f"Repository '{repository}' does not have a ready mapping.")
                if repo["mapped_commit_sha"] != before_commit:
                    raise MappingRevisionMismatchError(
                        "Repository mapping must match the push's before commit before explanation can run."
                    )

                cursor.execute(
                    """SELECT push.id, push.status, explanation.content
                       FROM pushes push
                       LEFT JOIN explanations explanation ON explanation.push_id = push.id
                       WHERE push.repo_id = %s AND push.before_commit = %s AND push.after_commit = %s
                       ORDER BY push.id DESC LIMIT 1""",
                    (repo["id"], before_commit, after_commit),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if existing["status"] == "completed" and existing["content"] is not None:
                        return repo["id"], existing["id"], Explanation.model_validate(existing["content"])
                    cursor.execute(
                        "UPDATE pushes SET status = 'processing' WHERE id = %s",
                        (existing["id"],),
                    )
                    return repo["id"], existing["id"], None

                cursor.execute(
                    """INSERT INTO pushes (repo_id, before_commit, after_commit, status)
                       VALUES (%s, %s, %s, 'processing') RETURNING id""",
                    (repo["id"], before_commit, after_commit),
                )
                push_row = cursor.fetchone()
                if push_row is None:
                    raise RuntimeError("Push row was not returned after insertion.")
                return repo["id"], push_row["id"], None


def save_explanation(
    repo_id: int,
    push_id: int,
    after_commit: str,
    explanation: Explanation,
    evidence: Evidence,
    use_extra_evidence: bool,
) -> None:
    """Persist the explanation without advancing the tracking cursor."""
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO explanations
                    (
                        push_id,
                        content,
                        evidence_used,
                        tokens_in,
                        tokens_out,
                        seconds,
                        model,
                        extra_evidence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (push_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        evidence_used = EXCLUDED.evidence_used,
                        tokens_in = EXCLUDED.tokens_in,
                        tokens_out = EXCLUDED.tokens_out,
                        seconds = EXCLUDED.seconds,
                        model = EXCLUDED.model,
                        extra_evidence = EXCLUDED.extra_evidence
                    """,
                    (
                        push_id,
                        Jsonb(explanation.model_dump(mode="json")),
                        Jsonb(evidence.model_dump(mode="json")),
                        explanation.meta.tokens_in,
                        explanation.meta.tokens_out,
                        explanation.meta.seconds,
                        explanation.meta.model,
                        use_extra_evidence,
                    ),
                )

                cursor.execute(
                    "UPDATE pushes SET status = 'completed' WHERE id = %s",
                    (push_id,),
                )

def mark_push_failed(push_id: int) -> None:
    """Record a failed explanation attempt without touching any previous explanation."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE pushes SET status = 'failed' WHERE id = %s", (push_id,))


def current_mapping(repository: str) -> dict[str, Any] | None:
    """Read repository id, readiness, and mapped SHA for workflow validation."""
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id, status, mapped_commit_sha FROM repos WHERE lower(name) = lower(%s)",
                (repository,),
            )
            return cursor.fetchone()


def list_repository_pushes(repository: str, limit: int = 50) -> list[PushHistoryItem]:
    """Return persisted push explanations and evidence, newest first."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """SELECT push.id AS push_id, push.before_commit, push.after_commit,
                    push.status, push.created_at,
                    explanation.content AS explanation,
                    explanation.evidence_used AS evidence,
                    COALESCE(explanation.tokens_in, 0) AS tokens_in,
                    COALESCE(explanation.tokens_out, 0) AS tokens_out,
                    COALESCE(explanation.seconds, 0.0) AS seconds,
                    explanation.model, explanation.extra_evidence
                   FROM repos repo
                   JOIN pushes push ON push.repo_id = repo.id
                   LEFT JOIN explanations explanation ON explanation.push_id = push.id
                   WHERE lower(repo.name) = lower(%s)
                   ORDER BY push.created_at DESC, push.id DESC
                   LIMIT %s""",
                (repository, limit),
            )
            return [PushHistoryItem.model_validate(row) for row in cursor.fetchall()]
