"""Offline worker tests for ordering, retries, and exact-commit remapping."""

from types import SimpleNamespace
from unittest.mock import Mock

from backend.app.config import Settings
from backend.app.delivery import worker
from backend.app.delivery.queue import JobRecord


def _job(attempts=1):
    return JobRecord(
        id=22,
        attempts=attempts,
        payload={
            "type": "explain_push",
            "repo_id": 8,
            "repository": "owner/repo",
            "before_commit": "a" * 40,
            "after_commit": "b" * 40,
        },
    )


def test_worker_explains_remaps_exact_commit_then_advances_cursor(monkeypatch):
    calls = []
    job = _job()
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None))
    result = worker.process_job(
        job,
        explain_function=lambda *args, **kwargs: calls.append(("explain", args, kwargs)),
        map_function=lambda *args, **kwargs: calls.append(("map", args, kwargs)) or SimpleNamespace(commit_sha="b" * 40),
        complete_function=lambda claimed: calls.append(("complete", claimed.id)) or True,
        retry_function=Mock(),
    )

    assert result is True
    assert [call[0] for call in calls] == ["explain", "map", "complete"]
    assert calls[0][1] == ("owner/repo", "a" * 40, "b" * 40)
    assert calls[1] == ("map", ("owner/repo",), {"revision": "b" * 40})


def test_worker_failure_schedules_bounded_retry_and_does_not_advance(monkeypatch):
    retry = Mock(return_value="pending")
    complete = Mock()
    remap = Mock()
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None, job_max_attempts=4))

    result = worker.process_job(
        _job(attempts=2),
        explain_function=Mock(side_effect=RuntimeError("temporary")),
        map_function=remap,
        complete_function=complete,
        retry_function=retry,
    )

    assert result is False
    retry.assert_called_once()
    assert retry.call_args.args[:3] == (22, 2, 4)
    remap.assert_not_called()
    complete.assert_not_called()


def test_worker_does_not_advance_cursor_if_remapping_fails(monkeypatch):
    retry = Mock(return_value="pending")
    complete = Mock()
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None))

    result = worker.process_job(
        _job(),
        explain_function=Mock(),
        map_function=Mock(side_effect=RuntimeError("mapping failed")),
        complete_function=complete,
        retry_function=retry,
    )

    assert result is False
    complete.assert_not_called()
    assert retry.called


def test_worker_claims_only_one_job_per_cycle(monkeypatch):
    job = _job()
    claim = Mock(return_value=job)
    completed = Mock(return_value=True)
    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(worker, "process_job", Mock(return_value=True))

    assert worker.process_next_job() is True
    claim.assert_called_once_with(300, 4)