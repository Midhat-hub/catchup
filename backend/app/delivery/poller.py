"""GitHub default-branch polling and durable push-job creation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from backend.app.config import get_settings
from backend.app.github.client import GitHubClient
from backend.app.github.exceptions import GitHubError
from backend.app.index.mapping import parse_repository_identifier
from backend.app.delivery.queue import (
    enqueue_push_job,
    establish_tracking_baseline,
    tracked_repositories,
)

logger = logging.getLogger(__name__)


@dataclass
class PollingSummary:
    repositories_checked: int = 0
    commits_discovered: int = 0
    jobs_created: int = 0
    repositories_with_errors: list[str] = field(default_factory=list)


def _poll_repository(client: GitHubClient, row: dict[str, Any], summary: PollingSummary) -> None:
    repo_id = row["id"]
    repository = row["name"]
    cursor_sha = row["last_explained_commit"]
    if not cursor_sha:
        cursor_sha = establish_tracking_baseline(repo_id)
        if cursor_sha:
            logger.info("Initialized tracking baseline for %s", repository)
        return

    owner, name = parse_repository_identifier(repository)
    metadata = client.get_repo(owner, name)
    commits = client.get_commits_after(
        owner,
        name,
        metadata.default_branch,
        cursor_sha,
    )
    previous_sha = cursor_sha
    summary.commits_discovered += len(commits)
    for commit in commits:
        if not commit.parents or commit.parents[0] != previous_sha:
            raise GitHubError(
                f"Commit history for {repository} does not form the expected first-parent chain; "
                "the tracking cursor was not advanced."
            )
        job_id = enqueue_push_job(repo_id, repository, previous_sha, commit.sha)
        if job_id is not None:
            summary.jobs_created += 1
        previous_sha = commit.sha


def poll_once(github_client: GitHubClient | None = None) -> PollingSummary:
    """Poll all ready repositories once and enqueue every unseen first-parent commit."""
    summary = PollingSummary()
    repositories = tracked_repositories()
    if github_client is None:
        with GitHubClient() as client:
            _poll_repositories(client, repositories, summary)
    else:
        _poll_repositories(github_client, repositories, summary)
    return summary


def _poll_repositories(client: GitHubClient, repositories: list[dict[str, Any]], summary: PollingSummary) -> None:
    for row in repositories:
        summary.repositories_checked += 1
        try:
            _poll_repository(client, row, summary)
        except Exception as exc:
            summary.repositories_with_errors.append(row["name"])
            logger.warning(
                "Polling failed for repository=%s error=%s; tracking position unchanged",
                row["name"],
                type(exc).__name__,
            )


async def polling_loop(interval_seconds: int | None = None) -> None:
    """Poll periodically; a failed cycle is logged safely and retried next interval."""
    interval = interval_seconds if interval_seconds is not None else get_settings().github_poll_interval_seconds
    while True:
        try:
            summary = await asyncio.to_thread(poll_once)
            if summary.jobs_created or summary.repositories_with_errors:
                logger.info(
                    "GitHub poll checked=%s commits=%s jobs_created=%s errors=%s",
                    summary.repositories_checked,
                    summary.commits_discovered,
                    summary.jobs_created,
                    len(summary.repositories_with_errors),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("GitHub poll cycle failed error=%s", type(exc).__name__)
        await asyncio.sleep(interval)