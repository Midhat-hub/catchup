"""GitHub token resolution and authentication utilities.

Follows PROJECT2.md requirement:
"A read-only personal access token in .env, behind a get_token() function".
Ensures tokens are never exposed, logged, or printed.
"""

from typing import Dict, Optional
from backend.app.config import get_settings


def get_token() -> Optional[str]:
    """Retrieve GitHub Personal Access Token from application settings.

    Returns:
        The token string stripped of whitespace, or None if not configured.
    """
    settings = get_settings()
    token = settings.github_token
    if token and token.strip():
        return token.strip()
    return None


def get_auth_headers(token: Optional[str] = None) -> Dict[str, str]:
    """Build standard GitHub API request headers.

    If a token is provided or configured in settings, attaches Bearer authorization.
    Never logs or exposes the token value.
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Catchup-App",
    }
    resolved_token = token if token is not None else get_token()
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"
    return headers
