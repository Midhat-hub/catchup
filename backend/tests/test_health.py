"""Unit tests for FastAPI endpoints and health checking logic."""

from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def test_root_endpoint():
    """Verify root endpoint responds with basic application information."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "Catchup"
    assert "environment" in data


def test_health_app_endpoint():
    """Verify pure application liveness check without DB probe."""
    response = client.get("/health/app")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "Catchup"


def test_health_healthy_database():
    """Verify /health returns 200 when database and vector extension are ready."""
    mock_health = {
        "connected": True,
        "vector_extension": True,
        "vector_version": "0.8.0",
        "error": None,
    }
    with patch("backend.app.main.check_db_health", return_value=mock_health):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"]["status"] == "connected"
        assert data["database"]["vector_extension"] is True
        assert data["database"]["vector_version"] == "0.8.0"
        assert data["database"]["error"] is None


def test_health_database_disconnected():
    """Verify /health returns 503 and degraded status when database is disconnected."""
    mock_health = {
        "connected": False,
        "vector_extension": False,
        "vector_version": None,
        "error": "Connection refused",
    }
    with patch("backend.app.main.check_db_health", return_value=mock_health):
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"]["status"] == "disconnected"
        assert data["database"]["vector_extension"] is False
        assert "Connection refused" in data["database"]["error"]


def test_health_missing_vector_extension():
    """Verify /health returns 503 when DB connects but pgvector extension is missing."""
    mock_health = {
        "connected": True,
        "vector_extension": False,
        "vector_version": None,
        "error": "pgvector extension is not installed",
    }
    with patch("backend.app.main.check_db_health", return_value=mock_health):
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"]["status"] == "connected"
        assert data["database"]["vector_extension"] is False


def test_health_db_isolated_endpoint():
    """Verify /health/db endpoint behavior for direct database probing."""
    with patch("backend.app.main.check_db_health", return_value={"connected": True, "vector_extension": True, "vector_version": "0.8.0", "error": None}):
        response = client.get("/health/db")
        assert response.status_code == 200

    with patch("backend.app.main.check_db_health", return_value={"connected": False, "vector_extension": False, "vector_version": None, "error": "down"}):
        response = client.get("/health/db")
        assert response.status_code == 503
