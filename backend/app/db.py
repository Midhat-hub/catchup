"""Database connection management and initialization for Catchup.

Uses psycopg 3 with connection pooling (psycopg_pool) and registers pgvector.
Provides health checking, safe schema initialization, and testable design.
"""

from contextlib import contextmanager
import logging
from pathlib import Path
import sys
from typing import Any, Dict, Generator, List, Optional
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

_pool: Optional[ConnectionPool] = None


def _configure_connection(conn: psycopg.Connection) -> None:
    """Configure each pooled connection, registering pgvector if the extension exists."""
    try:
        register_vector(conn)
    except Exception:
        # Before schema initialization, the vector type may not exist yet in PostgreSQL.
        pass


def get_pool() -> ConnectionPool:
    """Return the application connection pool singleton.

    Creates and configures the pool on first access.
    """
    global _pool
    if _pool is None or _pool.closed:
        settings = get_settings()
        db_url = settings.get_database_url()
        logger.info("Initializing database connection pool at %s:%s", settings.postgres_host, settings.postgres_port)
        _pool = ConnectionPool(
            conninfo=db_url,
            min_size=1,
            max_size=10,
            timeout=10.0,
            open=True,
            configure=_configure_connection,
        )
    return _pool


def close_pool() -> None:
    """Close the connection pool cleanly."""
    global _pool
    if _pool is not None and not _pool.closed:
        logger.info("Closing database connection pool")
        _pool.close()
    _pool = None


@contextmanager
def get_db_connection() -> Generator[psycopg.Connection, None, None]:
    """Context manager that borrows a connection from the pool.

    The connection is committed or rolled back appropriately and returned to the pool.
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def check_db_health() -> Dict[str, Any]:
    """Verify database connectivity and pgvector extension availability.

    Returns a health dictionary distinguishing database availability from errors.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 1. Ping the database
                cur.execute("SELECT 1 AS ping;")
                ping_result = cur.fetchone()
                if not ping_result or ping_result["ping"] != 1:
                    return {
                        "connected": False,
                        "vector_extension": False,
                        "vector_version": None,
                        "error": "Database ping failed to return expected value",
                    }

                # 2. Check for the pgvector extension
                cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
                ext_row = cur.fetchone()
                if ext_row:
                    return {
                        "connected": True,
                        "vector_extension": True,
                        "vector_version": ext_row["extversion"],
                        "error": None,
                    }
                else:
                    return {
                        "connected": True,
                        "vector_extension": False,
                        "vector_version": None,
                        "error": "pgvector extension is not installed in the database",
                    }
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return {
            "connected": False,
            "vector_extension": False,
            "vector_version": None,
            "error": str(exc),
        }


def init_db(schema_path: Optional[Path] = None) -> List[str]:
    """Execute the database schema script and return verified table names.

    Args:
        schema_path: Path to the SQL schema file. Defaults to schema.sql in this directory.

    Returns:
        List of table names present in the public schema after initialization.
    """
    if schema_path is None:
        schema_path = Path(__file__).parent / "schema.sql"

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    sql_content = schema_path.read_text(encoding="utf-8")

    logger.info("Executing schema initialization from %s", schema_path)
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_content)
        conn.commit()

        # Query existing tables in public schema to verify
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """
            )
            tables = [row["table_name"] for row in cur.fetchall()]

    logger.info("Schema initialized successfully. Existing tables: %s", tables)
    return tables


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("Testing Catchup database connection and schema...")
    try:
        tables = init_db()
        print(f"Database initialized successfully. Tables found: {tables}")
        health = check_db_health()
        print(f"Health check status: {health}")
        if not health["connected"] or not health["vector_extension"]:
            sys.exit(1)
    finally:
        close_pool()
