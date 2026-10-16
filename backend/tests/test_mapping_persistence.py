"""Tests for mapping replacement transaction boundaries and conflict handling."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from pgvector import Vector

from backend.app.github.models import RepoMetadata
from backend.app.index import persistence
from backend.app.parsing.javascript import CodeChunk


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.last_sql = normalized
        self.connection.statements.append((normalized, params))
        if self.connection.fail_chunk_insert and normalized.startswith("INSERT INTO chunks"):
            raise RuntimeError("simulated insert failure")

    def fetchone(self):
        self.connection.next_id += 1
        return (100 + self.connection.next_id,)


class FakeConnection:
    def __init__(self, fail_chunk_insert=False):
        self.statements = []
        self.next_id = 0
        self.fail_chunk_insert = fail_chunk_insert
        self.transaction_result = None

    def cursor(self, **kwargs):
        return FakeCursor(self)

    @contextmanager
    def transaction(self):
        self.transaction_result = "committed"
        try:
            yield
        except Exception:
            self.transaction_result = "rolled_back"
            raise


@contextmanager
def fake_connection_context(connection):
    yield connection


def _chunks():
    return [
        CodeChunk("src/a.js", "first", "function_declaration", 1, 2, "function first() {}"),
        CodeChunk("src/a.js", "second", "function_declaration", 4, 5, "function second() {}"),
    ]


def test_mapping_data_replacement_is_atomic_and_conflict_safe(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(persistence, "get_db_connection", lambda: fake_connection_context(connection))

    counts = persistence.persist_mapping(
        7,
        "b" * 40,
        _chunks(),
        [[0.1] * 384, [0.2] * 384],
        {(0, 1)},
        {("src/a.test.js", 1)},
    )

    statements = [sql for sql, _ in connection.statements]
    assert counts == (2, 1, 1)
    assert connection.transaction_result == "committed"
    assert statements[0] == "DELETE FROM chunks WHERE repo_id = %s"
    assert any("ON CONFLICT DO NOTHING" in sql and '"references"' in sql for sql in statements)
    assert any("ON CONFLICT DO NOTHING" in sql and "test_links" in sql for sql in statements)
    assert any(params == (101, 102) for sql, params in connection.statements if '"references"' in sql)
    assert any(params == ("src/a.test.js", 102) for sql, params in connection.statements if "test_links" in sql)
    vector_parameters = [
        params[-1] for sql, params in connection.statements if sql.startswith("INSERT INTO chunks")
    ]
    assert len(vector_parameters) == 2
    assert all(isinstance(vector, Vector) for vector in vector_parameters)
    assert statements[-1].startswith("UPDATE repos SET status = 'ready'")


def test_insert_failure_rolls_back_before_ready_status(monkeypatch):
    connection = FakeConnection(fail_chunk_insert=True)
    monkeypatch.setattr(persistence, "get_db_connection", lambda: fake_connection_context(connection))

    with pytest.raises(RuntimeError, match="simulated insert failure"):
        persistence.persist_mapping(7, "b" * 40, _chunks(), [[0.0] * 384] * 2, set(), set())

    assert connection.transaction_result == "rolled_back"
    assert not any(sql.startswith("UPDATE repos SET status = 'ready'") for sql, _ in connection.statements)


def test_registration_preserves_ready_state_only_for_same_revision(monkeypatch):
    connection = FakeConnection()
    cursor = FakeCursor(connection)
    cursor.fetchone = lambda: {"id": 12, "status": "ready", "mapped_commit_sha": "c" * 40}
    connection.cursor = lambda **kwargs: cursor
    monkeypatch.setattr(persistence, "get_db_connection", lambda: fake_connection_context(connection))
    metadata = RepoMetadata(
        id=4,
        name="repo",
        full_name="owner/repo",
        owner="owner",
        is_private=False,
    )

    repo_id, already_mapped = persistence.register_mapping(metadata, "c" * 40)

    assert (repo_id, already_mapped) == (12, True)
    assert "ON CONFLICT (name) DO UPDATE" in connection.statements[0][0]
    assert "repos.mapped_commit_sha = %s" in connection.statements[0][0]


def test_status_query_aliases_api_repository_field_and_ignores_case(monkeypatch):
    connection = FakeConnection()
    cursor = FakeCursor(connection)
    cursor.fetchone = lambda: {
        "repository": "Owner/Repo",
        "status": "ready",
        "mapped_commit_sha": "d" * 40,
        "mapping_error": None,
        "files_parsed": 1,
        "chunks_stored": 2,
        "references_stored": 0,
        "test_links_stored": 0,
    }
    connection.cursor = lambda **kwargs: cursor
    monkeypatch.setattr(persistence, "get_db_connection", lambda: fake_connection_context(connection))

    result = persistence.get_mapping_status("owner/repo")

    assert result["repository"] == "Owner/Repo"
    sql = connection.statements[0][0]
    assert "r.name AS repository" in sql
    assert "lower(r.name) = lower(%s)" in sql