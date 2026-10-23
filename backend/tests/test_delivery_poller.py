"""Offline polling tests for commit order, baseline, and durable deduplication."""

from unittest.mock import Mock

from backend.app.delivery import poller
from backend.app.github.exceptions import GitHubError
from backend.app.github.models import CommitItem, RepoMetadata


class FakeGitHubClient:
    def __init__(self, commits=None, error=None):
        self.commits = commits or []
        self.error = error
        self.branches = []

    def get_repo(self, owner, name):
        return RepoMetadata(
            id=1,
            name=name,
            full_name=f"{owner}/{name}",
            owner=owner,
            default_branch="release",
            is_private=False,
            language="JavaScript",
        )

    def get_commits_after(self, owner, name, branch, after_sha):
        self.branches.append((branch, after_sha))
        if self.error:
            raise self.error
        return self.commits


def test_poller_enqueues_multiple_commits_chronologically(monkeypatch):
    baseline = "a" * 40
    commit_b = "b" * 40
    commit_c = "c" * 40
    client = FakeGitHubClient([
        CommitItem(sha=commit_b, message="first", parents=[baseline]),
        CommitItem(sha=commit_c, message="second", parents=[commit_b]),
    ])
    monkeypatch.setattr(poller, "tracked_repositories", lambda: [{
        "id": 8,
        "name": "owner/repo",
        "mapped_commit_sha": baseline,
        "last_explained_commit": baseline,
    }])
    enqueue = Mock(side_effect=[101, 102])
    monkeypatch.setattr(poller, "enqueue_push_job", enqueue)

    result = poller.poll_once(github_client=client)

    assert result.repositories_checked == 1
    assert result.commits_discovered == 2
    assert result.jobs_created == 2
    assert client.branches == [("release", baseline)]
    assert enqueue.call_args_list[0].args == (8, "owner/repo", baseline, commit_b)
    assert enqueue.call_args_list[1].args == (8, "owner/repo", commit_b, commit_c)


def test_duplicate_jobs_are_not_counted_as_new_jobs(monkeypatch):
    baseline = "a" * 40
    client = FakeGitHubClient([CommitItem(sha="b" * 40, message="one", parents=[baseline])])
    monkeypatch.setattr(poller, "tracked_repositories", lambda: [{
        "id": 8,
        "name": "owner/repo",
        "mapped_commit_sha": baseline,
        "last_explained_commit": baseline,
    }])
    enqueue = Mock(return_value=None)
    monkeypatch.setattr(poller, "enqueue_push_job", enqueue)

    result = poller.poll_once(github_client=client)

    assert result.commits_discovered == 1
    assert result.jobs_created == 0
    enqueue.assert_called_once()


def test_newly_mapped_repository_uses_saved_commit_as_baseline(monkeypatch):
    baseline = "a" * 40
    monkeypatch.setattr(poller, "tracked_repositories", lambda: [{
        "id": 9,
        "name": "owner/new-repo",
        "mapped_commit_sha": baseline,
        "last_explained_commit": None,
    }])
    set_baseline = Mock(return_value=baseline)
    monkeypatch.setattr(poller, "establish_tracking_baseline", set_baseline)
    client = FakeGitHubClient()

    result = poller.poll_once(github_client=client)

    set_baseline.assert_called_once_with(9)
    assert client.branches == []
    assert result.jobs_created == 0


def test_history_error_is_reported_without_enqueuing_or_advancing(monkeypatch):
    baseline = "a" * 40
    client = FakeGitHubClient(error=GitHubError("base not in branch history"))
    monkeypatch.setattr(poller, "tracked_repositories", lambda: [{
        "id": 8,
        "name": "owner/repo",
        "mapped_commit_sha": baseline,
        "last_explained_commit": baseline,
    }])
    enqueue = Mock()
    monkeypatch.setattr(poller, "enqueue_push_job", enqueue)

    result = poller.poll_once(github_client=client)

    assert result.repositories_with_errors == ["owner/repo"]
    enqueue.assert_not_called()