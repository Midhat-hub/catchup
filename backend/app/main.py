"""FastAPI entry point for the Catchup application.

Provides application lifecycle management and robust health checks.
"""
import sys
from contextlib import asynccontextmanager
import asyncio
import logging
from typing import Any, Dict
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.db import check_db_health, close_pool
from backend.app.delivery.poller import polling_loop
from backend.app.delivery.worker import worker_loop
from backend.app.explain.gemini import GeminiConfigurationError, GeminiError
from backend.app.explain.models import ExplainPushRequest, PushHistoryItem
from backend.app.explain.persistence import (
    MappingRevisionMismatchError,
    RepositoryNotMappedError,
    list_repository_pushes,
)
from backend.app.explain.pipeline import ChangeAnalysisError, explain_push
from backend.app.github.exceptions import (
    GitHubError,
    PrivateRepoError,
    RateLimitExceededError,
    RepoNotFoundError,
)
from backend.app.index.embeddings import EmbeddingError
from backend.app.index.mapping import (
    RepositoryIdentifierError,
    UnsupportedRepositoryError,
    get_repository_mapping_status,
    map_repository,
    parse_repository_identifier,
)
from backend.app.index.mapping_models import (
    MappingStatistics,
    MappingStatus,
    RepositoryListItem,
    RepositoryMappingRequest,
)
from backend.app.index.persistence import list_repository_mappings
from backend.app.parsing.javascript import JavaScriptParseError

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
    existing_tasks = getattr(app.state, "catchup_background_tasks", None)
    owns_tasks = not existing_tasks or not any(not task.done() for task in existing_tasks)
    if owns_tasks:
        tasks = [
            asyncio.create_task(polling_loop(), name="catchup-github-poller"),
            asyncio.create_task(worker_loop(), name="catchup-push-worker"),
        ]
        app.state.catchup_background_tasks = tasks
        logger.info("Started local GitHub polling and push worker services")
    else:
        tasks = existing_tasks
        logger.info("Reusing active local polling/worker tasks")
    try:
        yield
    finally:
        if owns_tasks:
            logger.info("Stopping local polling and push worker services")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            app.state.catchup_background_tasks = None
        logger.info("Shutting down %s", settings.app_name)
        close_pool()


app = FastAPI(
    title=settings.app_name,
    description="Explain every push to public GitHub repositories in plain language.",
    version="0.1.0",
    lifespan=lifespan,
)
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://catchup-flax-three.vercel.app",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
print("PYTHON VERSION:", sys.version, flush=True)


@app.get("/api/repos", response_model=list[RepositoryListItem], tags=["Repositories"])
def repositories_list() -> list[RepositoryListItem]:
    """List repositories already added to this local Catchup database."""
    return [RepositoryListItem.model_validate(row) for row in list_repository_mappings()]


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


@app.post("/api/repos/map", response_model=MappingStatistics, tags=["Repositories"])
def map_repository_endpoint(request: RepositoryMappingRequest) -> MappingStatistics:
    """Map the current default-branch revision of a public JavaScript repository."""
    try:
        return map_repository(request.repository)
    except RepositoryIdentifierError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrivateRepoError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RateLimitExceededError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    except (UnsupportedRepositoryError, JavaScriptParseError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get(
    "/api/repos/{owner}/{repo}/mapping",
    response_model=MappingStatus,
    tags=["Repositories"],
)
def repository_mapping_status(owner: str, repo: str) -> MappingStatus:
    """Return stored mapping status and record counts without contacting GitHub."""
    try:
        mapping_status = get_repository_mapping_status(f"{owner}/{repo}")
    except RepositoryIdentifierError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if mapping_status is None:
        raise HTTPException(status_code=404, detail="Repository mapping was not found.")
    return mapping_status


@app.get(
    "/api/repos/{owner}/{repo}/pushes",
    response_model=list[PushHistoryItem],
    tags=["Pushes"],
)
def repository_pushes(owner: str, repo: str, limit: int = 50) -> list[PushHistoryItem]:
    """List persisted pushes newest first, including any saved explanation evidence."""
    try:
        repository = f"{owner}/{repo}"
        parse_repository_identifier(repository)
        return list_repository_pushes(repository, limit=limit)
    except RepositoryIdentifierError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/pushes/explain", tags=["Pushes"], response_model=None)
def explain_push_endpoint(request: ExplainPushRequest, response: Response) -> Any:
    """Explain and persist one push; return 204 for comment/whitespace-only changes."""
    try:
        explanation = explain_push(
            request.repo,
            request.before_commit,
            request.after_commit,
            request.use_extra_evidence,
        )
        if explanation is None:
            response.status_code = status.HTTP_204_NO_CONTENT
            return None
        return explanation
    except RepositoryIdentifierError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RepositoryNotMappedError, MappingRevisionMismatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ChangeAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GeminiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GeminiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
