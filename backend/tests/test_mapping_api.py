"""HTTP validation and response tests for repository mapping endpoints."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.github.exceptions import PrivateRepoError, RateLimitExceededError, RepoNotFoundError
from backend.app.index.mapping import RepositoryIdentifierError, UnsupportedRepositoryError
from backend.app.index.mapping_models import MappingStatistics, MappingStatus
from backend.app.main import app

client = TestClient(app)


def test_map_repository_endpoint_returns_statistics():
    statistics = MappingStatistics(
        repository="owner/repo",
        commit_sha="a" * 40,
        source_files_examined=3,
        files_parsed=2,
        chunks_stored=4,
        embeddings_generated=4,
        references_stored=1,
        test_links_stored=1,
        duration_seconds=0.1,
    )
    with patch("backend.app.main.map_repository", return_value=statistics):
        response = client.post("/api/repos/map", json={"repository": "owner/repo"})

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["commit_sha"] == "a" * 40
    assert response.json()["chunks_stored"] == 4


def test_map_repository_endpoint_maps_validation_and_visibility_errors():
    with patch("backend.app.main.map_repository", side_effect=RepositoryIdentifierError("bad identifier")):
        invalid = client.post("/api/repos/map", json={"repository": "not-a-repo"})
    with patch("backend.app.main.map_repository", side_effect=PrivateRepoError("private repository")):
        private = client.post("/api/repos/map", json={"repository": "owner/secret"})
    with patch("backend.app.main.map_repository", side_effect=RepoNotFoundError("missing")):
        missing = client.post("/api/repos/map", json={"repository": "owner/missing"})
    with patch("backend.app.main.map_repository", side_effect=UnsupportedRepositoryError("JavaScript only")):
        unsupported = client.post("/api/repos/map", json={"repository": "owner/source"})

    assert invalid.status_code == 422
    assert private.status_code == 403
    assert missing.status_code == 404
    assert unsupported.status_code == 422


def test_map_repository_rate_limit_and_payload_validation():
    with patch(
        "backend.app.main.map_repository",
        side_effect=RateLimitExceededError("rate limited", retry_after=60),
    ):
        limited = client.post("/api/repos/map", json={"repository": "owner/repo"})
    malformed = client.post("/api/repos/map", json={"unexpected": "shape"})

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert malformed.status_code == 422


def test_mapping_status_endpoint_returns_status_or_not_found():
    ready = MappingStatus(
        repository="owner/repo",
        status="ready",
        mapped_commit_sha="a" * 40,
        files_parsed=3,
        chunks_stored=5,
    )
    with patch("backend.app.main.get_repository_mapping_status", return_value=ready):
        response = client.get("/api/repos/owner/repo/mapping")
    with patch("backend.app.main.get_repository_mapping_status", return_value=None):
        missing = client.get("/api/repos/owner/missing/mapping")

    assert response.status_code == 200
    assert response.json()["mapped_commit_sha"] == "a" * 40
    assert response.json()["chunks_stored"] == 5
    assert missing.status_code == 404