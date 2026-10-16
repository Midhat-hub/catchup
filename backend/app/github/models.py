"""Pydantic data models for GitHub API responses."""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class RepoMetadata(BaseModel):
    """Metadata for a validated public GitHub repository."""

    model_config = ConfigDict(extra="ignore")

    id: int = Field(..., description="GitHub numeric repository identifier")
    name: str = Field(..., description="Repository short name")
    full_name: str = Field(..., description="Repository full name, e.g. 'owner/repo'")
    owner: str = Field(..., description="Owner username or organization name")
    default_branch: str = Field(default="main", description="Default branch name")
    is_private: bool = Field(..., description="True if repository is private")
    description: Optional[str] = Field(default=None, description="Repository description")
    html_url: Optional[str] = Field(default=None, description="Public GitHub URL")


class RepoFileItem(BaseModel):
    """File item retrieved from a recursive Git tree."""

    model_config = ConfigDict(extra="ignore")

    path: str = Field(..., description="File path relative to repository root")
    type: str = Field(..., description="'blob' for files or 'tree' for subdirectories")
    size: Optional[int] = Field(default=None, description="File size in bytes if blob")
    sha: str = Field(..., description="Git blob/tree SHA")
    mode: Optional[str] = Field(default=None, description="File mode, e.g. '100644'")


class CommitItem(BaseModel):
    """Information for a Git commit on the repository."""

    model_config = ConfigDict(extra="ignore")

    sha: str = Field(..., description="Full 40-character commit SHA")
    message: str = Field(..., description="Commit message")
    author_name: str = Field(default="Unknown", description="Author name")
    author_email: Optional[str] = Field(default=None, description="Author email")
    date: Optional[str] = Field(default=None, description="Commit timestamp ISO 8601 string")
    parents: List[str] = Field(default_factory=list, description="Parent commit SHAs")
    html_url: Optional[str] = Field(default=None, description="Web URL for commit")


class RateLimitInfo(BaseModel):
    """GitHub REST API rate limit status."""

    model_config = ConfigDict(extra="ignore")

    limit: int = Field(..., description="Hourly request limit")
    remaining: int = Field(..., description="Remaining requests in current window")
    reset_epoch: int = Field(..., description="Unix epoch timestamp when quota resets")
    used: int = Field(default=0, description="Requests consumed so far")
