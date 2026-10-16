"""Request and response models for repository mapping."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RepositoryMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(..., min_length=3, max_length=255, examples=["owner/repository"])


class SkippedFile(BaseModel):
    path: str
    reason: str


class MappingStatistics(BaseModel):
    status: Literal["ready"] = "ready"
    repository: str
    commit_sha: str
    source_files_examined: int = Field(ge=0)
    files_parsed: int = Field(ge=0)
    chunks_stored: int = Field(ge=0)
    embeddings_generated: int = Field(ge=0)
    references_stored: int = Field(ge=0)
    test_links_stored: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    skipped_files: list[SkippedFile] = Field(default_factory=list)
    skipped_file_count: int = 0
    skipped_files_truncated: bool = False
    already_mapped: bool = False


class MappingStatus(BaseModel):
    repository: str
    status: str
    mapped_commit_sha: str | None = None
    mapping_error: str | None = None
    files_parsed: int = 0
    chunks_stored: int = 0
    references_stored: int = 0
    test_links_stored: int = 0