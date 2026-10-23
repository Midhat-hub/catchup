"""Read-only route contracts that power the Step 6 repository/push views."""

from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.explain.models import PushHistoryItem
from backend.app.index.mapping_models import RepositoryListItem
from backend.app.main import app

client = TestClient(app)


def test_repository_list_route_returns_existing_mapping_records():
    record = RepositoryListItem(
        repository="owner/repo",
        status="ready",
        mapped_commit_sha="a" * 40,
        files_parsed=4,
        chunks_stored=12,
        references_stored=5,
        test_links_stored=3,
        created_at="2026-10-16T10:00:00+00:00",
        updated_at="2026-10-16T10:01:00+00:00",
    )
    with patch("backend.app.main.list_repository_mappings", return_value=[record.model_dump()]):
        response = client.get("/api/repos")

    assert response.status_code == 200
    assert response.json()[0]["repository"] == "owner/repo"
    assert response.json()[0]["chunks_stored"] == 12


def test_push_history_route_returns_saved_explanation_and_evidence_newest_first():
    first = PushHistoryItem(
        push_id=2,
        before_commit="b" * 40,
        after_commit="c" * 40,
        status="completed",
        created_at=datetime(2026, 10, 16, 10, 2, tzinfo=UTC),
        explanation={
            "one_line": "Updates book validation.",
            "what_changed": "The price is checked for finite values.",
            "meta": {"model": "gemini-3.5-flash-lite"},
        },
        evidence={"similar_code": [{"file": "src/books.js", "name": "saveBook", "score": 0.92}]},
        tokens_in=400,
        tokens_out=90,
        seconds=2.4,
        model="gemini-3.5-flash-lite",
        extra_evidence=True,
    )
    second = first.model_copy(update={"push_id": 1, "after_commit": "b" * 40})
    with patch("backend.app.main.list_repository_pushes", return_value=[first, second]) as list_pushes:
        response = client.get("/api/repos/owner/repo/pushes?limit=2")

    assert response.status_code == 200
    assert [item["push_id"] for item in response.json()] == [2, 1]
    assert response.json()[0]["explanation"]["one_line"] == "Updates book validation."
    assert response.json()[0]["evidence"]["similar_code"][0]["score"] == 0.92
    list_pushes.assert_called_once_with("owner/repo", limit=2)


def test_push_history_route_validates_repository_and_limit():
    invalid_repo = client.get("/api/repos/not%20owner/repo/pushes")
    invalid_limit = client.get("/api/repos/owner/repo/pushes?limit=101")

    assert invalid_repo.status_code == 422
    assert invalid_limit.status_code == 422