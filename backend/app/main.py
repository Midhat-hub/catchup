"""FastAPI entry point for the Catchup application.

Provides application lifecycle management and robust health checks.
"""

from contextlib import asynccontextmanager
import logging
from typing import Any, Dict
from fastapi import FastAPI, Response, status

from backend.app.config import get_settings
from backend.app.db import check_db_health, close_pool

# Configure root logger
settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("catchup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown events."""
    logger.info("Starting %s in %s mode", settings.app_name, settings.app_env)
    yield
    logger.info("Shutting down %s", settings.app_name)
    close_pool()


app = FastAPI(
    title=settings.app_name,
    description="Explain every push to public GitHub repositories in plain language.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/", tags=["General"])
async def root() -> Dict[str, str]:
    """Root endpoint returning basic application identification."""
    return {
        "app": settings.app_name,
        "environment": settings.app_env,
        "docs": "/docs",
    }


@app.get("/health/app", tags=["Health"])
async def health_app() -> Dict[str, str]:
    """Basic application liveness check (does not query the database)."""
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/db", tags=["Health"])
async def health_db(response: Response) -> Dict[str, Any]:
    """Isolated database and pgvector connectivity check."""
    db_status = check_db_health()
    if not db_status["connected"] or not db_status["vector_extension"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return db_status


@app.get("/health", tags=["Health"])
async def health(response: Response) -> Dict[str, Any]:
    """Comprehensive health check distinguishing app status from database connectivity.

    Returns HTTP 200 when both app and database are healthy.
    Returns HTTP 503 with degraded status when the database is unavailable.
    """
    db_status = check_db_health()

    if db_status["connected"] and db_status["vector_extension"]:
        return {
            "status": "healthy",
            "app": settings.app_name,
            "database": {
                "status": "connected",
                "vector_extension": True,
                "vector_version": db_status["vector_version"],
                "error": None,
            },
        }

    # If DB failed or vector extension is missing, accurately report degraded status
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "degraded",
        "app": settings.app_name,
        "database": {
            "status": "connected" if db_status["connected"] else "disconnected",
            "vector_extension": db_status["vector_extension"],
            "vector_version": db_status.get("vector_version"),
            "error": db_status.get("error"),
        },
    }
