"""GitHub REST API client for Catchup.

Features:
- Validates repository visibility and strictly refuses private repositories.
- Fetches recursive file trees for repository mapping.
- Fetches latest commits and compares commits (producing PushData models).
- In-memory ETag caching to avoid burning rate limit quotas (HTTP 304 handling).
- Rate limit tracking and explicit domain error mapping.
- Never prints, logs, or leaks authorization tokens.
"""

import logging
import time
import base64
import binascii
from typing import Any, Dict, List, Optional
import httpx

from backend.app.github.auth import get_auth_headers, get_token
from backend.app.github.cache import ETagCache
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
from backend.app.models import PushData, PushFile

logger = logging.getLogger(__name__)

# Global cache instance
_default_cache = ETagCache()


class GitHubClient:
    """Synchronous GitHub REST API client with ETag caching and rate limit handling."""

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = "https://api.github.com",
        timeout: float = 15.0,
        cache: Optional[ETagCache] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else get_token()
        self.timeout = timeout
        self.cache = cache if cache is not None else _default_cache
        self._http = httpx.Client(
            timeout=self.timeout,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _build_url(self, endpoint: str) -> str:
        """Build full API URL from endpoint."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute HTTP request with caching, ETag conditional headers, and error handling."""
        url = self._build_url(endpoint)
        headers = get_auth_headers(self.token)

        # Generate cache key
        param_str = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        cache_key = f"{method.upper()}:{url}:{param_str}"

        # If cached, attach ETag for conditional request
        cached_entry = self.cache.get(cache_key)
        if cached_entry and cached_entry[0]:
            headers["If-None-Match"] = cached_entry[0]

        try:
            response = self._http.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            logger.error("GitHub API request timed out for %s", url)
            raise GitHubAPIUnavailableError(f"GitHub API request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            logger.error("GitHub API request failed for %s: %s", url, exc)
            raise GitHubError(f"GitHub network communication error: {exc}") from exc

        # Handle HTTP 304 Not Modified
        if response.status_code == 304:
            logger.debug("Cache hit (304 Not Modified) for %s", url)
            if cached_entry:
                return cached_entry[1]

        # Handle rate limits
        remaining_str = response.headers.get("x-ratelimit-remaining")
        reset_str = response.headers.get("x-ratelimit-reset")
        reset_epoch = int(reset_str) if reset_str and reset_str.isdigit() else None

        if response.status_code in (403, 429):
            if remaining_str == "0" or "rate limit" in response.text.lower():
                retry_after = None
                if reset_epoch:
                    retry_after = max(0, int(reset_epoch - time.time()))
                logger.warning("GitHub rate limit exceeded. Resets in %s seconds", retry_after)
                raise RateLimitExceededError(
                    f"GitHub API rate limit exceeded. Resets at epoch {reset_epoch}.",
                    reset_epoch=reset_epoch,
                    retry_after=retry_after,
                    status_code=response.status_code,
                )

        # Handle specific error status codes
        if response.status_code == 401:
            raise GitHubAuthError(
                "GitHub authentication failed. Check your GITHUB_TOKEN.",
                status_code=401,
            )

        if response.status_code == 404:
            raise RepoNotFoundError(
                f"GitHub resource not found: {endpoint}",
                status_code=404,
            )

        if response.status_code >= 500:
            raise GitHubAPIUnavailableError(
                f"GitHub API service error ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )

        if not response.is_success:
            raise GitHubError(
                f"GitHub API error ({response.status_code}): {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )

        # Successful response (200 OK)
        data = response.json()
        etag = response.headers.get("etag")
        if etag:
            self.cache.set(cache_key, etag, data)

        return data

    def get_repo(self, owner: str, repo: str) -> RepoMetadata:
        """Fetch repository metadata and enforce public-only policy.

        Raises:
            PrivateRepoError: If the repository is private.
            RepoNotFoundError: If the repository does not exist.
            GitHubError: On network or authentication errors.
        """
        endpoint = f"repos/{owner}/{repo}"
        data = self._request("GET", endpoint)

        is_private = data.get("private", False)
        if is_private:
            raise PrivateRepoError(
                f"Repository '{owner}/{repo}' is private. "
                "Catchup strictly supports public repositories only."
            )

        return RepoMetadata(
            id=data["id"],
            name=data["name"],
            full_name=data.get("full_name", f"{owner}/{repo}"),
            owner=data.get("owner", {}).get("login", owner),
            default_branch=data.get("default_branch", "main"),
            is_private=is_private,
            language=data.get("language"),
            description=data.get("description"),
            html_url=data.get("html_url"),
        )

    def list_files(
        self,
        owner: str,
        repo: str,
        tree_sha: Optional[str] = None,
    ) -> List[RepoFileItem]:
        """Fetch the full recursive file tree for the repository.

        If tree_sha is not provided, resolves the repository's default branch.
        """
        if not tree_sha:
            metadata = self.get_repo(owner, repo)
            tree_sha = metadata.default_branch

        endpoint = f"repos/{owner}/{repo}/git/trees/{tree_sha}"
        data = self._request("GET", endpoint, params={"recursive": "1"})

        if data.get("truncated"):
            raise GitHubError(
                "GitHub returned a truncated repository tree. Mapping stopped to avoid marking an incomplete repository ready."
            )

        tree_items = data.get("tree", [])
        files: List[RepoFileItem] = []
        for item in tree_items:
            files.append(
                RepoFileItem(
                    path=item["path"],
                    type=item["type"],
                    size=item.get("size"),
                    sha=item["sha"],
                    mode=item.get("mode"),
                )
            )
        return files

    def get_blob_content(self, owner: str, repo: str, blob_sha: str) -> bytes:
        """Fetch and decode a Git blob by the SHA returned in a repository tree."""
        data = self._request("GET", f"repos/{owner}/{repo}/git/blobs/{blob_sha}")
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise GitHubError(f"GitHub returned an unsupported encoding for blob {blob_sha}.")
        try:
            encoded_content = "".join(data["content"].split())
            return base64.b64decode(encoded_content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise GitHubError(f"GitHub returned invalid base64 for blob {blob_sha}.") from exc

    def get_latest_commits(
        self,
        owner: str,
        repo: str,
        branch: Optional[str] = None,
        limit: int = 10,
    ) -> List[CommitItem]:
        """Fetch the latest commits on a repository branch, handling pagination."""
        params: Dict[str, Any] = {"per_page": min(limit, 100)}
        if branch:
            params["sha"] = branch

        endpoint = f"repos/{owner}/{repo}/commits"
        data = self._request("GET", endpoint, params=params)

        commits: List[CommitItem] = []
        for item in data[:limit]:
            commit_info = item.get("commit", {})
            author_info = commit_info.get("author") or {}
            parents = [p.get("sha") for p in item.get("parents", []) if p.get("sha")]

            commits.append(
                CommitItem(
                    sha=item["sha"],
                    message=commit_info.get("message", "").strip(),
                    author_name=author_info.get("name", "Unknown"),
                    author_email=author_info.get("email"),
                    date=author_info.get("date"),
                    parents=parents,
                    html_url=item.get("html_url"),
                )
            )
        return commits

    def get_commits_after(
        self,
        owner: str,
        repo: str,
        branch: str,
        after_sha: str,
        *,
        page_size: int = 100,
        max_pages: int = 1000,
    ) -> List[CommitItem]:
        """Return commits after a tracked SHA in chronological order.

        GitHub's commits endpoint is newest-first. Keep paging until the exact
        tracked SHA is found; a missing base is an error, never an empty success.
        """
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        commits_by_sha: Dict[str, CommitItem] = {}
        head_sha: Optional[str] = None
        endpoint = f"repos/{owner}/{repo}/commits"
        for page in range(1, max_pages + 1):
            data = self._request(
                "GET",
                endpoint,
                params={"sha": branch, "per_page": page_size, "page": page},
            )
            if not isinstance(data, list):
                raise GitHubError("GitHub returned an invalid commit history response.")
            for item in data:
                sha = item.get("sha")
                if not sha:
                    continue
                if head_sha is None:
                    head_sha = sha
                commit_info = item.get("commit", {})
                author_info = commit_info.get("author") or {}
                parents = [parent.get("sha") for parent in item.get("parents", []) if parent.get("sha")]
                commits_by_sha[sha] = CommitItem(
                    sha=sha,
                    message=commit_info.get("message", "").strip(),
                    author_name=author_info.get("name", "Unknown"),
                    author_email=author_info.get("email"),
                    date=author_info.get("date"),
                    parents=parents,
                    html_url=item.get("html_url"),
                )
            if after_sha in commits_by_sha:
                break
            if len(data) < page_size:
                break
        if after_sha not in commits_by_sha:
            raise GitHubError(
                f"Tracked commit {after_sha} was not found in the fetched history for {owner}/{repo}@{branch}; "
                "polling stopped without advancing the repository cursor."
            )
        if head_sha == after_sha:
            return []

        descending_first_parent: List[CommitItem] = []
        current_sha = head_sha
        visited: set[str] = set()
        while current_sha != after_sha:
            if current_sha in visited:
                raise GitHubError("GitHub returned a cyclic commit parent chain.")
            visited.add(current_sha)
            commit = commits_by_sha.get(current_sha)
            if commit is None:
                item = self._request("GET", f"repos/{owner}/{repo}/commits/{current_sha}")
                commit_info = item.get("commit", {})
                author_info = commit_info.get("author") or {}
                parents = [parent.get("sha") for parent in item.get("parents", []) if parent.get("sha")]
                commit = CommitItem(
                    sha=item["sha"],
                    message=commit_info.get("message", "").strip(),
                    author_name=author_info.get("name", "Unknown"),
                    author_email=author_info.get("email"),
                    date=author_info.get("date"),
                    parents=parents,
                    html_url=item.get("html_url"),
                )
            descending_first_parent.append(commit)
            if not commit.parents:
                raise GitHubError(
                    f"Tracked commit {after_sha} is not an ancestor of {head_sha}; polling stopped without advancing."
                )
            current_sha = commit.parents[0]
        return list(reversed(descending_first_parent))

    def get_compare(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
    ) -> PushData:
        """Compare two commits and return a PushData object.

        Reuses the PushData and PushFile models established in Step 1.
        """
        endpoint = f"repos/{owner}/{repo}/compare/{base}...{head}"
        data = self._request("GET", endpoint)

        files: List[PushFile] = []
        for f in data.get("files", []):
            files.append(
                PushFile(
                    path=f.get("filename", ""),
                    status=f.get("status", "modified"),
                    patch=f.get("patch"),
                )
            )

        return PushData(
            repo=f"{owner}/{repo}",
            before_commit=base,
            after_commit=head,
            files=files,
        )

    def get_rate_limit(self) -> RateLimitInfo:
        """Fetch current GitHub REST API rate limit status."""
        data = self._request("GET", "rate_limit")
        core = data.get("resources", {}).get("core", {})
        return RateLimitInfo(
            limit=core.get("limit", 0),
            remaining=core.get("remaining", 0),
            reset_epoch=core.get("reset", 0),
            used=core.get("used", 0),
        )
