"""Durable push explanation worker and background loop."""

from __future__ import annotations

import asyncio
import logging

from backend.app.config import get_settings
from backend.app.delivery.queue import (
    JobRecord,
    claim_next_job,
    complete_job,
    retry_or_fail_job,
)
from backend.app.explain.pipeline import explain_push
from backend.app.index.mapping import map_repository
from backend.app.github.exceptions import RateLimitExceededError

logger = logging.getLogger(__name__)


def process_job(
    job: JobRecord,
    *,
    explain_function=explain_push,
    map_function=map_repository,
    complete_function=complete_job,
    retry_function=retry_or_fail_job,
) -> bool:
    """Explain, remap at the exact commit, then advance the tracking cursor."""
    settings = get_settings()
    payload = job.payload
    try:
        if payload.get("type") != "explain_push":
            raise ValueError("Unsupported job type.")
        repository = payload["repository"]
        before_commit = payload["before_commit"]
        after_commit = payload["after_commit"]

        explain_function(repository, before_commit, after_commit, use_extra_evidence=True)
        mapping_result = map_function(repository, revision=after_commit)
        if mapping_result.commit_sha != after_commit:
            raise RuntimeError("Repository remapping did not complete at the job commit.")
        if not complete_function(job):
            raise RuntimeError("Tracking cursor changed before the job could be completed.")
        logger.info(
            "Completed push job id=%s repository=%s commit=%s",
            job.id,
            repository,
            after_commit[:12],
        )
        return True
    except Exception as exc:
        retry_after_seconds = 30

        if isinstance(exc, RateLimitExceededError):
            retry_after_seconds = exc.retry_after or 60

        next_status = retry_function(
            job.id,
            job.attempts,
            settings.job_max_attempts,
            f"{type(exc).__name__}: {exc}",
            retry_after_seconds=retry_after_seconds,
        )

        logger.warning(
            "Push job failed id=%s attempt=%s/%s next_status=%s error=%s retry_after=%s",
            job.id,
            job.attempts,
            settings.job_max_attempts,
            next_status,
            type(exc).__name__,
            retry_after_seconds,
        )
        return False


def process_next_job() -> bool:
    """Claim and process one job, returning false when no work is available."""
    settings = get_settings()
    job = claim_next_job(settings.job_lease_seconds, settings.job_max_attempts)
    return process_job(job) if job is not None else False


async def worker_loop(idle_seconds: float | None = None) -> None:
    """Continuously process durable jobs until application shutdown cancels the task."""
    settings = get_settings()
    idle_delay = idle_seconds if idle_seconds is not None else settings.job_worker_idle_seconds
    while True:
        try:
            processed = await asyncio.to_thread(process_next_job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Job worker cycle failed")
            processed = False
        if not processed:
            await asyncio.sleep(idle_delay)