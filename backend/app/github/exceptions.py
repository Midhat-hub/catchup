"""Custom domain exceptions for GitHub API interactions."""

from typing import Any, Optional


class GitHubError(Exception):
    """Base exception for all GitHub-related operations."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body


class GitHubAuthError(GitHubError):
    """Raised when GitHub token authentication fails (401 Unauthorized)."""
    pass


class PrivateRepoError(GitHubError):
    """Raised when a repository is private.

    Catchup strictly enforces public-only repository processing per PROJECT2.md.
    """
    pass


class RepoNotFoundError(GitHubError):
    """Raised when a repository is not found (404 Not Found)."""
    pass


class RateLimitExceededError(GitHubError):
    """Raised when GitHub API rate limit is exceeded (403/429)."""

    def __init__(
        self,
        message: str,
        reset_epoch: Optional[int] = None,
        retry_after: Optional[int] = None,
        status_code: int = 403,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.reset_epoch = reset_epoch
        self.retry_after = retry_after


class GitHubAPIUnavailableError(GitHubError):
    """Raised when GitHub API returns 5xx server errors."""
    pass
