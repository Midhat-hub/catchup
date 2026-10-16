"""Transactional persistence helpers for repository mappings."""

from typing import Any

from pgvector import Vector
from psycopg.rows import dict_row

from backend.app.db import get_db_connection
from backend.app.github.models import RepoMetadata
from backend.app.parsing.javascript import CodeChunk


def register_mapping(metadata: RepoMetadata, commit_sha: str) -> tuple[int, bool]:
    """Create/update a repository row and mark a new revision as mapping."""
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO repos (github_repo_id, name, status, mapping_error, updated_at)
                VALUES (%s, %s, 'mapping', NULL, CURRENT_TIMESTAMP)
                ON CONFLICT (name) DO UPDATE SET
                    github_repo_id = EXCLUDED.github_repo_id,
                    status = CASE
                        WHEN repos.status = 'ready' AND repos.mapped_commit_sha = %s THEN 'ready'
                        ELSE 'mapping'
                    END,
                    mapping_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id, status, mapped_commit_sha
                """,
                (metadata.id, metadata.full_name, commit_sha),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Repository row was not returned after mapping registration.")
            return row["id"], row["status"] == "ready" and row["mapped_commit_sha"] == commit_sha


def mark_mapping_failed(repository: str, error: str, repo_id: int | None = None) -> None:
    """Persist an actionable mapping failure without deleting the prior mapping."""
    safe_error = error[:4000]
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if repo_id is not None:
                cursor.execute(
                    """UPDATE repos SET status = 'failed', mapping_error = %s,
                       updated_at = CURRENT_TIMESTAMP WHERE id = %s""",
                    (safe_error, repo_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO repos (name, status, mapping_error, updated_at)
                    VALUES (%s, 'failed', %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (name) DO UPDATE SET
                        status = 'failed', mapping_error = EXCLUDED.mapping_error,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (repository, safe_error),
                )


def persist_mapping(
    repo_id: int,
    commit_sha: str,
    chunks: list[CodeChunk],
    embeddings: list[list[float]],
    references: set[tuple[int, int]],
    test_links: set[tuple[str, int]],
) -> tuple[int, int, int]:
    """Replace one repository's mapped data atomically and mark its revision ready."""
    if len(chunks) != len(embeddings):
        raise ValueError("Every stored chunk must have exactly one embedding.")

    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM chunks WHERE repo_id = %s", (repo_id,))
                chunk_ids: list[int] = []
                for chunk, embedding in zip(chunks, embeddings):
                    cursor.execute(
                        """
                        INSERT INTO chunks
                            (repo_id, file, name, kind, start_line, end_line, text, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            repo_id, chunk.file_path, chunk.name, chunk.kind,
                            chunk.start_line, chunk.end_line, chunk.text, Vector(embedding),
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError("Inserted chunk did not return its database identifier.")
                    chunk_ids.append(row[0])

                for caller_id, callee_id in references:
                    if not (0 <= caller_id < len(chunk_ids) and 0 <= callee_id < len(chunk_ids)):
                        raise ValueError("Reference points to a chunk index outside the mapping batch.")
                    cursor.execute(
                        'INSERT INTO "references" (caller_chunk_id, callee_chunk_id) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                        (chunk_ids[caller_id], chunk_ids[callee_id]),
                    )
                for test_file, chunk_id in test_links:
                    if not 0 <= chunk_id < len(chunk_ids):
                        raise ValueError("Test link points to a chunk index outside the mapping batch.")
                    cursor.execute(
                        "INSERT INTO test_links (test_file, chunk_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (test_file, chunk_ids[chunk_id]),
                    )
                cursor.execute(
                    """UPDATE repos SET status = 'ready', mapped_commit_sha = %s,
                       mapping_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = %s""",
                    (commit_sha, repo_id),
                )
                return len(chunk_ids), len(references), len(test_links)


def get_mapping_status(repository: str) -> dict[str, Any] | None:
    """Return mapping state and persisted record counts for one repository."""
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT r.name AS repository, r.status, r.mapped_commit_sha, r.mapping_error,
                    (SELECT COUNT(DISTINCT file) FROM chunks WHERE repo_id = r.id) AS files_parsed,
                    (SELECT COUNT(*) FROM chunks WHERE repo_id = r.id) AS chunks_stored,
                    (SELECT COUNT(*) FROM \"references\" ref
                        JOIN chunks c ON c.id = ref.caller_chunk_id WHERE c.repo_id = r.id) AS references_stored,
                    (SELECT COUNT(*) FROM test_links tl
                        JOIN chunks c ON c.id = tl.chunk_id WHERE c.repo_id = r.id) AS test_links_stored
                FROM repos r WHERE lower(r.name) = lower(%s)
                """,
                (repository,),
            )
            return cursor.fetchone()