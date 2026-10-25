"""Pydantic data models defining the core data shapes for Catchup.

These shapes are specified in Section 5 of PROJECT2.md and are locked for
consistency across the ingestion, explanation, delivery, and frontend pipelines.
"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class PushFile(BaseModel):
    """Individual file modified in a Git push."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repository root")
    status: str = Field(..., description="Change status, e.g. 'added', 'modified', 'removed', 'renamed'")
    patch: Optional[str] = Field(default=None, description="Unified diff patch for this file")


class PushData(BaseModel):
    """Complete commit delta information for a push."""

    model_config = ConfigDict(extra="forbid")

    repo: str = Field(..., description="Repository full name, e.g. 'owner/repo'")
    before_commit: str = Field(..., description="Previous commit SHA")
    after_commit: str = Field(..., description="Current pushed commit SHA")
    files: List[PushFile] = Field(default_factory=list, description="List of files changed in this push")


class ChangedSymbol(BaseModel):
    """Specific function, method, or class symbol affected in the commit."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(..., description="File path containing the symbol")
    name: str = Field(..., description="Symbol identifier name")
    kind: str = Field(..., description="Change type: 'added', 'modified', or 'removed'")
    start_line: int = Field(..., ge=1, description="1-based start line number in file")
    end_line: int = Field(..., ge=1, description="1-based end line number in file")
    diff_snippet: str = Field(..., description="Code snippet showing the diff or changed body")


class SimilarCode(BaseModel):
    """Vector search retrieval result for similar code chunks."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(..., description="File path of the similar chunk")
    name: str = Field(..., description="Function or class name of the similar chunk")
    score: float = Field(..., description="Cosine similarity score (0.0 to 1.0)")
    snippet: Optional[str] = Field(default=None, description="Text snippet of the similar chunk")


class CallerReference(BaseModel):
    """Reference connection between a caller and a target function."""

    model_config = ConfigDict(extra="forbid")

    caller_file: str = Field(..., description="File where the caller function resides")
    caller_name: str = Field(..., description="Name of the calling function")
    target_name: str = Field(..., description="Name of the function being called")


class TestLink(BaseModel):
    """Link mapping a test file to a target symbol it exercises."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    test_file: str = Field(..., description="Path to the test file")
    target_name: str = Field(..., description="Symbol name covered by this test")


class Evidence(BaseModel):
    """Context gathered across code search, graph analysis, and rules to prompt the LLM."""

    model_config = ConfigDict(extra="forbid")

    changes: List[ChangedSymbol] = Field(default_factory=list, description="Changed symbols in this push")
    callers: List[CallerReference] = Field(default_factory=list, description="Functions calling the changed symbols")
    tests: List[TestLink] = Field(default_factory=list, description="Tests covering the changed symbols")
    similar_code: List[SimilarCode] = Field(default_factory=list, description="Semantically similar code chunks")
    flags: List[str] = Field(default_factory=list, description="Risk flags, e.g. 'touches the database'")
    token_count: int = Field(default=0, ge=0, description="Estimated token count for prompt budgeting")


class MightAffect(BaseModel):
    """Explanation item describing downstream impact of a change."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(..., description="Function, module, or system target affected")
    reason: str = Field(..., description="Plain-language explanation of why it is affected")
    link: str = Field(..., description="Code link or file reference for inspection")


class ThingToKnow(BaseModel):
    """Beginner-friendly educational term explanation."""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(..., description="Concept, library, or term used in the explanation")
    meaning: str = Field(..., description="Simple, beginner-friendly definition")


class ExplanationMeta(BaseModel):
    """Execution telemetry and generation metadata."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., description="Model identifier used for generation, e.g. 'gemini-1.5-flash'")
    tokens_in: int = Field(default=0, ge=0, description="Input tokens used in prompt")
    tokens_out: int = Field(default=0, ge=0, description="Output tokens generated")
    seconds: float = Field(default=0.0, ge=0.0, description="Total generation duration in seconds")


class Explanation(BaseModel):
    """Final structured push explanation presented in the UI."""

    model_config = ConfigDict(extra="forbid")

    one_line: str = Field(..., description="Single sentence summarizing what the change is about")
    what_changed: str = Field(..., description="Plain-language description of what changed")
    might_affect: List[MightAffect] = Field(default_factory=list, description="Likely affected code and components")
    things_to_know: List[ThingToKnow] = Field(default_factory=list, description="Beginner explanations of key terms")
    flags: List[str] = Field(default_factory=list, description="Identified risk flags")
    meta: ExplanationMeta = Field(..., description="Generation metadata and token statistics")
