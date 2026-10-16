"""HTTP request model for explaining a GitHub push."""

from pydantic import BaseModel, ConfigDict, Field


class ExplainPushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(..., min_length=3, max_length=255, examples=["owner/repository"])
    before_commit: str = Field(..., min_length=7, max_length=64)
    after_commit: str = Field(..., min_length=7, max_length=64)
    use_extra_evidence: bool = True