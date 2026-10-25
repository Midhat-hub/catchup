"""Offline tests for durable job queue semantics and recovery queries."""

from contextlib import contextmanager

from psycopg.types.json import Jsonb

from backend.app.delivery import queue
from backend.app.delivery.queue import JobRecord


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 1
        self.last_sql = ""
        self.last_params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False
    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params
        self.connection.statements.append((self.last_sql, params))

        if "last_explained_commit = mapped_commit_sha" in self.last_sql:
            self.connection.fetchone_value = (self.connection.baseline,)

        elif self.last_sql.startswith("INSERT INTO jobs"):
            self.connection.fetchone_value = self.connection.inserted_job

        elif (
            "WITH candidate AS" in self.last_sql
            and "RETURNING j.id, j.payload, j.attempts" in self.last_sql
        ):
            claimed = self.connection.claimed_job

            if claimed is not None:
                self.connection.fetchone_value = {
                    **claimed,
                    "attempts": claimed["attempts"] + 1,
                }
            else:
                self.connection.fetchone_value = None

        elif self.last_sql.startswith("UPDATE repos SET last_explained_commit"):
            self.rowcount = self.connection.repo_update_count

        elif self.last_sql.startswith("UPDATE jobs SET status = 'completed'"):
            self.rowcount = self.connection.job_update_count
    def fetchone(self):
        value = self.connection.fetchone_value
        self.connection.fetchone_value = None
        return value


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.fetchone_value = None
        self.inserted_job = (21,)
        self.claimed_job = None
        self.baseline = "a" * 40
        self.repo_update_count = 1
        self.job_update_count = 1
        self.transaction_opened = False

    def cursor(self, **kwargs):
        return FakeCursor(self)

    @contextmanager
    def transaction(self):
        self.transaction_opened = True
        yield


@contextmanager
def connection_context(connection):
    yield connection


def test_job_enqueue_is_unique_and_payload_identifies_repo_commit(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(queue, "get_db_connection", lambda: connection_context(connection))

    job_id = queue.enqueue_push_job(7, "owner/repo", "a" * 40, "b" * 40, branch="main")

    assert job_id == 21
    sql, params = connection.statements[0]
    assert "ON CONFLICT DO NOTHING" in sql
    assert isinstance(params[0], Jsonb)
    assert params[0].obj == {
        "type": "explain_push",
        "repo_id": 7,
        "repository": "owner/repo",
        "before_commit": "a" * 40,
        "after_commit": "b" * 40,
        "branch": "main",
    }


def test_claim_uses_skip_locked_and_includes_expired_running_jobs(monkeypatch):
    connection = FakeConnection()
    connection.claimed_job = {
        "id": 21,
        "payload": {"type": "explain_push", "repo_id": 7, "repository": "owner/repo"},
        "attempts": 1,
    }
    monkeypatch.setattr(queue, "get_db_connection", lambda: connection_context(connection))

    job = queue.claim_next_job(lease_seconds=300, max_attempts=4)

    assert job == JobRecord(21, connection.claimed_job["payload"], 2)
    claim_sql = next(
    sql
    for sql, _ in connection.statements
    if "WITH candidate AS" in sql
    and "RETURNING j.id, j.payload, j.attempts" in sql
)
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "status = 'running'" in claim_sql
    assert connection.transaction_opened


def test_retry_or_fail_job_is_bounded_and_redacts_secret_patterns(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(queue, "get_db_connection", lambda: connection_context(connection))

    retry_status = queue.retry_or_fail_job(21, attempts=2, max_attempts=4, error="temporary error")
    failed_status = queue.retry_or_fail_job(21, attempts=4, max_attempts=4, error="failed AQ.secret-token")

    assert retry_status == "pending"
    assert failed_status == "failed"
    fail_sql, fail_params = connection.statements[-1]
    assert "status = %s" in fail_sql
    assert "AQ.secret-token" not in fail_params[1]
    assert "[REDACTED]" in fail_params[1]


def test_completion_advances_cursor_and_marks_job_in_transaction(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(queue, "get_db_connection", lambda: connection_context(connection))
    job = JobRecord(
        21,
        {
            "type": "explain_push",
            "repo_id": 7,
            "repository": "owner/repo",
            "before_commit": "a" * 40,
            "after_commit": "b" * 40,
        },
        1,
    )
    assert queue.complete_job(job) is True

    assert connection.transaction_opened
    assert any("last_explained_commit = %s" in sql for sql, _ in connection.statements)
    assert any("status = 'completed'" in sql for sql, _ in connection.statements)


def test_baseline_initialization_only_fills_missing_cursor(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(queue, "get_db_connection", lambda: connection_context(connection))

    baseline = queue.establish_tracking_baseline(7)

    assert baseline == "a" * 40
    sql = connection.statements[0][0]
    assert "last_explained_commit IS NULL" in sql
    assert "mapped_commit_sha" in sql
