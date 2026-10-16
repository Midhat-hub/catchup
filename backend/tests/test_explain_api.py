"""HTTP contract tests for the Step 4 push explanation endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import Explanation, ExplanationMeta

client = TestClient(app)


def _body():
    return {
        "repo": "owner/repo",
        "before_commit": "a" * 40,
        "after_commit": "b" * 40,
        "use_extra_evidence": False,
    }


def test_explain_push_route_accepts_locked_request_and_returns_explanation():
    explanation = Explanation(
        one_line="Changes book lookup behavior.",
        what_changed="A book lookup now checks the identifier.",
        meta=ExplanationMeta(model="gemini-test"),
    )
    with patch("backend.app.main.explain_push", return_value=explanation) as explain:
        response = client.post("/api/pushes/explain", json=_body())

    assert response.status_code == 200
    assert response.json()["one_line"] == explanation.one_line
    explain.assert_called_once_with("owner/repo", "a" * 40, "b" * 40, False)


def test_explain_push_route_returns_204_for_non_meaningful_changes():
    with patch("backend.app.main.explain_push", return_value=None):
        response = client.post("/api/pushes/explain", json=_body())

    assert response.status_code == 204
    assert response.content == b""


def test_explain_push_route_rejects_unexpected_request_fields():
    body = _body() | {"api_key": "must-not-be-accepted"}
    response = client.post("/api/pushes/explain", json=body)

    assert response.status_code == 422