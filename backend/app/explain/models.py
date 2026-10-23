"""HTTP request model for explaining a GitHub push."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import Evidence, Explanation


class ExplainPushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(..., min_length=3, max_length=255, examples=["owner/repository"])
    before_commit: str = Field(..., min_length=7, max_length=64)
    after_commit: str = Field(..., min_length=7, max_length=64)
    use_extra_evidence: bool = True


class PushHistoryItem(BaseModel):
    push_id: int
    before_commit: str
    after_commit: str
    status: str
    created_at: datetime
    explanation: Explanation | None = None
    evidence: Evidence | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    model: str | None = None
    extra_evidence: bool | None = None