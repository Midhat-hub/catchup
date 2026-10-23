"""Offline unit tests for the GitHub REST API client using mocked HTTP responses."""

from unittest.mock import patch
import httpx
import pytest

from backend.app.config import Settings
from backend.app.github.auth import get_auth_headers, get_token
from backend.app.github.cache import ETagCache
from backend.app.github.client import GitHubClient
from backend.app.github.exceptions import (
    GitHubAPIUnavailableError,
    GitHubAuthError,
    GitHubError,
    PrivateRepoError,
    RateLimitExceededError,
    RepoNotFoundError,
)
from backend.app.models import PushData


def test_get_token_resolution():
    """Verify get_token retrieves stripped token or None when empty."""
    with patch("backend.app.github.auth.get_settings") as mock_settings:
        mock_settings.return_value = Settings(github_token="  ghp_test_token123  ", _env_file=None)
        assert get_token() == "ghp_test_token123"

        mock_settings.return_value = Settings(github_token="", _env_file=None)
        assert get_token() is None

        mock_settings.return_value = Settings(github_token=None, _env_file=None)
        assert get_token() is None


def test_get_auth_headers(monkeypatch):
    """Verify headers include Bearer token only when configured or provided."""
    import backend.app.github.auth as auth

    monkeypatch.setattr(auth, "get_token", lambda: None)

    headers_no_token = get_auth_headers(token=None)
    assert "Authorization" not in headers_no_token

    headers_with_token = get_auth_headers(token="test-token")
    assert headers_with_token["Authorization"] == "Bearer test-token"

def test_get_repo_public_success():
    """Verify fetching public repository metadata."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/owner/public-repo"
        return httpx.Response(
            200,
            json={
                "id": 1001,
                "name": "public-repo",
                "full_name": "owner/public-repo",
                "owner": {"login": "owner"},
                "default_branch": "main",
                "private": False,
                "description": "A public repo",
                "html_url": "https://github.com/owner/public-repo",
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        repo = client.get_repo("owner", "public-repo")
        assert repo.id == 1001
        assert repo.name == "public-repo"
        assert repo.default_branch == "main"
        assert repo.is_private is False


def test_get_repo_private_refused():
    """Verify strict refusal when a repository is marked private."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 1002,
                "name": "secret-repo",
                "full_name": "owner/secret-repo",
                "owner": {"login": "owner"},
                "default_branch": "main",
                "private": True,
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        with pytest.raises(PrivateRepoError) as exc_info:
            client.get_repo("owner", "secret-repo")
        assert "private" in str(exc_info.value).lower()
        assert "strictly supports public repositories" in str(exc_info.value)


def test_get_repo_not_found():
    """Verify RepoNotFoundError on 404 response."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        with pytest.raises(RepoNotFoundError):
            client.get_repo("owner", "non-existent")


def test_get_repo_auth_error():
    """Verify GitHubAuthError on 401 Unauthorized response."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        with pytest.raises(GitHubAuthError):
            client.get_repo("owner", "repo")


def test_list_files_recursive_tree():
    """Verify recursive file-tree retrieval and model mapping."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "recursive=1" in str(request.url)
        return httpx.Response(
            200,
            json={
                "sha": "main",
                "tree": [
                    {"path": "package.json", "type": "blob", "size": 350, "sha": "sha1"},
                    {"path": "src/app.js", "type": "blob", "size": 1200, "sha": "sha2"},
                    {"path": "src", "type": "tree", "sha": "sha3"},
                ],
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        files = client.list_files("owner", "repo", tree_sha="main")
        assert len(files) == 3
        assert files[0].path == "package.json"
        assert files[0].type == "blob"
        assert files[1].size == 1200


def test_get_blob_content_decodes_exact_revision_blob():
    """Verify blob retrieval uses the tree-provided SHA and decodes source bytes."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/owner/repo/git/blobs/blob-sha"
        return httpx.Response(200, json={"encoding": "base64", "content": "Y29uc3QgYSA9IDE7"})

    with GitHubClient(transport=httpx.MockTransport(mock_handler), cache=ETagCache()) as client:
        assert client.get_blob_content("owner", "repo", "blob-sha") == b"const a = 1;"


def test_truncated_tree_is_rejected():
    """A partial recursive tree must not be used for a supposedly complete map."""
    from backend.app.github.exceptions import GitHubError

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"truncated": True, "tree": []})

    with GitHubClient(transport=httpx.MockTransport(mock_handler), cache=ETagCache()) as client:
        with pytest.raises(GitHubError, match="truncated"):
            client.list_files("owner", "repo", tree_sha="a" * 40)


def test_get_latest_commits():
    """Verify retrieval and parsing of recent commits."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "c1a2b3c4d5e6f7",
                    "commit": {
                        "message": "Initial commit",
                        "author": {"name": "Midhat", "email": "midhat@example.com", "date": "2026-10-16T10:00:00Z"},
                    },
                    "parents": [],
                    "html_url": "https://github.com/owner/repo/commit/c1a2b3c4d5e6f7",
                }
            ],
        )

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        commits = client.get_latest_commits("owner", "repo", limit=5)
        assert len(commits) == 1
        assert commits[0].sha == "c1a2b3c4d5e6f7"
        assert commits[0].author_name == "Midhat"
        assert commits[0].message == "Initial commit"


def test_get_commits_after_paginates_and_returns_chronological_order():
    requests = []

    def commit(sha, parent=None):
        return {
            "sha": sha,
            "commit": {"message": sha, "author": {"name": "Author", "date": "2026-10-16T10:00:00Z"}},
            "parents": [{"sha": parent}] if parent else [],
        }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requests.append(page)
        if page == 1:
            return httpx.Response(200, json=[commit("new", "middle"), commit("middle", "base")])
        if page == 2:
            return httpx.Response(200, json=[commit("base", "older")])
        return httpx.Response(200, json=[])

    with GitHubClient(transport=httpx.MockTransport(handler), cache=ETagCache()) as client:
        commits = client.get_commits_after("owner", "repo", "main", "base", page_size=2)

    assert [item.sha for item in commits] == ["middle", "new"]
    assert requests == [1, 2]


def test_get_commits_after_fails_closed_if_tracking_sha_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with GitHubClient(transport=httpx.MockTransport(handler), cache=ETagCache()) as client:
        with pytest.raises(GitHubError, match="Tracked commit"):
            client.get_commits_after("owner", "repo", "main", "missing")


def test_get_compare_returns_push_data():
    """Verify commit comparison maps cleanly into Step 1 PushData model."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/owner/repo/compare/commitA...commitB"
        return httpx.Response(
            200,
            json={
                "status": "ahead",
                "ahead_by": 1,
                "files": [
                    {
                        "filename": "src/server.js",
                        "status": "modified",
                        "patch": "@@ -1,3 +1,4 @@\n+const express = require('express');",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        push_data = client.get_compare("owner", "repo", base="commitA", head="commitB")
        assert isinstance(push_data, PushData)
        assert push_data.repo == "owner/repo"
        assert push_data.before_commit == "commitA"
        assert push_data.after_commit == "commitB"
        assert len(push_data.files) == 1
        assert push_data.files[0].path == "src/server.js"
        assert push_data.files[0].status == "modified"


def test_rate_limit_exceeded_error():
    """Verify RateLimitExceededError is raised when x-ratelimit-remaining is 0."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "1999999999",
            },
            json={"message": "API rate limit exceeded"},
        )

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        with pytest.raises(RateLimitExceededError) as exc_info:
            client.get_repo("owner", "repo")
        assert exc_info.value.reset_epoch == 1999999999


def test_in_memory_etag_caching():
    """Verify ETag attached on second request and 304 response returns cached data."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert "If-None-Match" not in request.headers
            return httpx.Response(
                200,
                headers={"etag": '"etag-val-123"'},
                json={"id": 100, "name": "my-repo", "private": False},
            )
        else:
            # Second call should provide If-None-Match header
            assert request.headers.get("If-None-Match") == '"etag-val-123"'
            return httpx.Response(
                304,
                headers={"etag": '"etag-val-123"'},
            )

    transport = httpx.MockTransport(mock_handler)
    cache = ETagCache()
    with GitHubClient(transport=transport, cache=cache) as client:
        # First request: Populates cache
        repo1 = client.get_repo("owner", "my-repo")
        assert repo1.name == "my-repo"
        assert cache.size == 1

        # Second request: Receives 304 and returns cached data
        repo2 = client.get_repo("owner", "my-repo")
        assert repo2.name == "my-repo"
        assert call_count == 2


def test_service_unavailable_on_timeout():
    """Verify GitHubAPIUnavailableError is raised on network timeouts."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out", request=request)

    transport = httpx.MockTransport(mock_handler)
    with GitHubClient(transport=transport, cache=ETagCache()) as client:
        with pytest.raises(GitHubAPIUnavailableError):
            client.get_repo("owner", "repo")
