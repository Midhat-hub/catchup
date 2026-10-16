"""Offline persistence tests for Step 4 pushes and explanations."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from psycopg.types.json import Jsonb

from backend.app.explain import persistence
from backend.app.explain.persistence import MappingRevisionMismatchError, RepositoryNotMappedError
from backend.app.models import Evidence, Explanation, ExplanationMeta


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.connection.statements.append((self.last_sql, params))

    def fetchone(self):
        if "FROM repos WHERE lower(name)" in self.last_sql:
            return self.connection.repository_row
        if "FROM repos repo" in self.last_sql:
            return self.connection.cached_row
        if "FROM pushes push" in self.last_sql:
            return self.connection.push_row
        if "INSERT INTO pushes" in self.last_sql:
            return {"id": self.connection.push_id}
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self, repository_row=None, cached_row=None, push_row=None, push_id=40):
        self.repository_row = repository_row
        self.cached_row = cached_row
        self.push_row = push_row
        self.push_id = push_id
        self.statements = []
        self.transaction_opened = False

    def cursor(self, **kwargs):
        return FakeCursor(self)

    @contextmanager
    def transaction(self):
        self.transaction_opened = True
        yield


@contextmanager
def _connection_context(connection):
    yield connection


def _explanation():
    return Explanation(
        one_line="Updates book lookup behavior.",
        what_changed="A lookup now checks the record identifier.",
        meta=ExplanationMeta(model="gemini-test", tokens_in=100, tokens_out=30, seconds=1.5),
    )


def test_begin_push_validates_mapped_base_and_creates_processing_row(monkeypatch):
    connection = FakeConnection(
        repository_row={"id": 7, "status": "ready", "mapped_commit_sha": "a" * 40},
    )
    monkeypatch.setattr(persistence, "get_db_connection", lambda: _connection_context(connection))

    repo_id, push_id, cached = persistence.begin_push("owner/repo", "a" * 40, "b" * 40)

    assert (repo_id, push_id, cached) == (7, 40, None)
    assert connection.transaction_opened
    assert any("FOR UPDATE" in sql for sql, _ in connection.statements)
    assert any("INSERT INTO pushes" in sql for sql, _ in connection.statements)


@pytest.mark.parametrize(
    ("repository_row", "exception"),
    [
        (None, RepositoryNotMappedError),
        ({"id": 7, "status": "mapping", "mapped_commit_sha": "a" * 40}, RepositoryNotMappedError),
        ({"id": 7, "status": "ready", "mapped_commit_sha": "c" * 40}, MappingRevisionMismatchError),
    ],
)
def test_begin_push_rejects_unready_or_wrong_revision(monkeypatch, repository_row, exception):
    connection = FakeConnection(repository_row=repository_row)
    monkeypatch.setattr(persistence, "get_db_connection", lambda: _connection_context(connection))

    with pytest.raises(exception):
        persistence.begin_push("owner/repo", "a" * 40, "b" * 40)


def test_completed_push_is_idempotently_returned(monkeypatch):
    explanation = _explanation()
    connection = FakeConnection(
        repository_row={"id": 7, "status": "ready", "mapped_commit_sha": "a" * 40},
        push_row={"id": 41, "status": "completed", "content": explanation.model_dump(mode="json")},
    )
    monkeypatch.setattr(persistence, "get_db_connection", lambda: _connection_context(connection))

    repo_id, push_id, cached = persistence.begin_push("owner/repo", "a" * 40, "b" * 40)

    assert repo_id == 7
    assert push_id == 41
    assert cached == explanation
    assert not any("INSERT INTO pushes" in sql for sql, _ in connection.statements)


def test_save_explanation_persists_json_metrics_and_completion(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(persistence, "get_db_connection", lambda: _connection_context(connection))
    evidence = Evidence(flags=["touches the database"], token_count=600)
    explanation = _explanation()

    persistence.save_explanation(7, 40, "b" * 40, explanation, evidence, True)

    insert = next(item for item in connection.statements if item[0].startswith("INSERT INTO explanations"))
    assert isinstance(insert[1][1], Jsonb)
    assert isinstance(insert[1][2], Jsonb)
    assert insert[1][3:8] == (100, 30, 1.5, "gemini-test", True)
    assert any("UPDATE pushes SET status = 'completed'" in sql for sql, _ in connection.statements)
    assert any("last_explained_commit = %s" in sql for sql, _ in connection.statements)