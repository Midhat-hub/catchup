"""GitHub connection and API client module (Step 2).

Provides the GitHub REST client, secure token access, in-memory caching,
and domain exception handling.
"""

from backend.app.github.auth import get_auth_headers, get_token
from backend.app.github.cache import ETagCache
from backend.app.github.client import GitHubClient
from backend.app.github.exceptions import (
    GitHubAPIUnavailableError,
    GitHubAuthError,
    GitHubError,
    PrivateRepoError,
    RateLimitExceededError,
    RepoNotFoundError,
)
from backend.app.github.models import (
    CommitItem,
    RateLimitInfo,
    RepoFileItem,
    RepoMetadata,
)

__all__ = [
    "GitHubClient",
    "get_token",
    "get_auth_headers",
    "ETagCache",
    "GitHubError",
    "GitHubAuthError",
    "PrivateRepoError",
    "RepoNotFoundError",
    "RateLimitExceededError",
    "GitHubAPIUnavailableError",
    "RepoMetadata",
    "RepoFileItem",
    "CommitItem",
    "RateLimitInfo",
]
