"""Live integration test against the public GitHub repository: Midhat-hub/bookstore-api.

Marked with @pytest.mark.integration and @pytest.mark.github.
"""

import pytest
from backend.app.github.client import GitHubClient
from backend.app.github.cache import ETagCache


@pytest.mark.integration
@pytest.mark.github
def test_live_bookstore_api_metadata():
    """Verify live repository metadata for Midhat-hub/bookstore-api."""
    with GitHubClient(cache=ETagCache()) as client:
        metadata = client.get_repo("Midhat-hub", "bookstore-api")
        assert metadata.full_name == "Midhat-hub/bookstore-api"
        assert metadata.name == "bookstore-api"
        assert metadata.owner == "Midhat-hub"
        assert metadata.default_branch == "main"
        assert metadata.is_private is False
        assert metadata.id > 0


@pytest.mark.integration
@pytest.mark.github
def test_live_bookstore_api_files():
    """Verify recursive file-tree retrieval for Midhat-hub/bookstore-api."""
    with GitHubClient(cache=ETagCache()) as client:
        files = client.list_files("Midhat-hub", "bookstore-api")
        assert len(files) > 0

        file_paths = {f.path for f in files}
        # Check expected JavaScript files in bookstore-api
        assert "package.json" in file_paths
        assert "src/app.js" in file_paths
        assert "src/server.js" in file_paths
        assert "src/routes/bookRoutes.js" in file_paths
        assert "tests/bookRoutes.test.js" in file_paths

        # Verify blob item properties
        pkg_json = next(f for f in files if f.path == "package.json")
        assert pkg_json.type == "blob"
        assert pkg_json.size is not None and pkg_json.size > 0


@pytest.mark.integration
@pytest.mark.github
def test_live_bookstore_api_commits():
    """Verify commit history retrieval for Midhat-hub/bookstore-api."""
    with GitHubClient(cache=ETagCache()) as client:
        commits = client.get_latest_commits("Midhat-hub", "bookstore-api", limit=5)
        assert len(commits) >= 1

        latest = commits[0]
        assert len(latest.sha) == 40
        assert latest.message != ""
        assert latest.author_name != ""


@pytest.mark.integration
@pytest.mark.github
def test_live_bookstore_api_caching():
    """Verify live ETag caching behavior against GitHub API."""
    cache = ETagCache()
    with GitHubClient(cache=cache) as client:
        # First call: populates cache
        repo1 = client.get_repo("Midhat-hub", "bookstore-api")
        assert cache.size >= 1

        # Second call: leverages cached ETag (304 Not Modified)
        repo2 = client.get_repo("Midhat-hub", "bookstore-api")
        assert repo2.id == repo1.id
        assert repo2.full_name == repo1.full_name
