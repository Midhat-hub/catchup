"""Integration tests for live PostgreSQL database connectivity and schema verification.

These tests are marked with @pytest.mark.integration and run against a live
PostgreSQL + pgvector container started via Docker Compose.
"""

import pytest
from backend.app.db import check_db_health, init_db, get_db_connection


@pytest.fixture(scope="module")
def ensure_db_available():
    """Verify live database connectivity before running integration tests."""
    health = check_db_health()
    if not health["connected"]:
        pytest.skip(f"Live PostgreSQL database is not reachable: {health.get('error')}")
    return health


@pytest.mark.integration
def test_live_database_health(ensure_db_available):
    """Verify live PostgreSQL and pgvector extension are healthy."""
    health = check_db_health()
    assert health["connected"] is True
    assert health["vector_extension"] is True
    assert health["vector_version"] is not None
    assert health["error"] is None


@pytest.mark.integration
def test_schema_initialization_all_seven_tables(ensure_db_available):
    """Verify that all 7 required tables from PROJECT2.md are created."""
    created_tables = init_db()

    expected_tables = {
        "repos",
        "chunks",
        "references",
        "test_links",
        "pushes",
        "jobs",
        "explanations",
    }

    # Ensure all expected tables are in the created tables
    missing_tables = expected_tables - set(created_tables)
    assert not missing_tables, f"Missing required database tables: {missing_tables}"


@pytest.mark.integration
def test_pgvector_operations(ensure_db_available):
    """Verify pgvector type operations and vector insertion/query."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Test simple vector literal calculation
            cur.execute("SELECT '[1,2,3]'::vector <-> '[4,5,6]'::vector AS distance;")
            row = cur.fetchone()
            assert row is not None
            assert row[0] > 0
