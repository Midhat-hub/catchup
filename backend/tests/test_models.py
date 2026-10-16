"""Unit tests for Pydantic data models defined in PROJECT2.md."""

import pytest
from pydantic import ValidationError
from backend.app.models import (
    PushData,
    PushFile,
    ChangedSymbol,
    SimilarCode,
    CallerReference,
    TestLink,
    Evidence,
    MightAffect,
    ThingToKnow,
    ExplanationMeta,
    Explanation,
)


def test_push_data_valid():
    """Verify valid PushData instantiation and nested PushFile."""
    push = PushData(
        repo="owner/cache-project",
        before_commit="a" * 40,
        after_commit="b" * 40,
        files=[
            PushFile(path="src/cache.js", status="modified", patch="@@ -1,3 +1,4 @@"),
            PushFile(path="README.md", status="added"),
        ],
    )
    assert push.repo == "owner/cache-project"
    assert len(push.files) == 2
    assert push.files[1].patch is None


def test_push_data_missing_field():
    """Verify missing required fields raise validation error."""
    with pytest.raises(ValidationError):
        PushData(repo="owner/repo", before_commit="abc")  # Missing after_commit


def test_changed_symbol_valid_and_bounds():
    """Verify ChangedSymbol validations for positive 1-based line numbers."""
    sym = ChangedSymbol(
        file="src/index.js",
        name="calculateTotal",
        kind="modified",
        start_line=10,
        end_line=25,
        diff_snippet="function calculateTotal() { ... }",
    )
    assert sym.name == "calculateTotal"
    assert sym.start_line == 10

    # Line number must be >= 1
    with pytest.raises(ValidationError):
        ChangedSymbol(
            file="src/index.js",
            name="badLine",
            kind="added",
            start_line=0,
            end_line=10,
            diff_snippet="code",
        )


def test_evidence_structure():
    """Verify Evidence model accepts all defined context lists."""
    evidence = Evidence(
        changes=[
            ChangedSymbol(
                file="src/db.js",
                name="connect",
                kind="modified",
                start_line=5,
                end_line=15,
                diff_snippet="const connect = () => {};",
            )
        ],
        callers=[
            CallerReference(caller_file="src/app.js", caller_name="startServer", target_name="connect")
        ],
        tests=[
            TestLink(test_file="test/db.test.js", target_name="connect")
        ],
        similar_code=[
            SimilarCode(file="src/auth.js", name="initAuth", score=0.82, snippet="function initAuth()")
        ],
        flags=["touches the database"],
        token_count=1450,
    )
    assert len(evidence.changes) == 1
    assert len(evidence.callers) == 1
    assert len(evidence.tests) == 1
    assert len(evidence.similar_code) == 1
    assert evidence.flags == ["touches the database"]
    assert evidence.token_count == 1450


def test_explanation_structure_and_forbid_extra():
    """Verify full Explanation model serialization and extra field prohibition."""
    meta = ExplanationMeta(
        model="gemini-1.5-flash",
        tokens_in=3500,
        tokens_out=420,
        seconds=2.15,
    )
    explanation = Explanation(
        one_line="Updates the cache TTL eviction policy to prevent memory leaks.",
        what_changed="Modified evictExpiredKeys to use an interval-based timer rather than on-demand checking.",
        might_affect=[
            MightAffect(
                target="cacheService.get",
                reason="Expired items will now be purged before lookup.",
                link="src/cache.js#L45",
            )
        ],
        things_to_know=[
            ThingToKnow(
                term="TTL (Time To Live)",
                meaning="The duration for which cached data is kept before being discarded.",
            )
        ],
        flags=["changes cache eviction logic"],
        meta=meta,
    )
    assert explanation.one_line.startswith("Updates the cache")
    assert len(explanation.might_affect) == 1
    assert explanation.meta.seconds == 2.15

    # Extra fields should be forbidden
    with pytest.raises(ValidationError):
        Explanation(
            one_line="Test",
            what_changed="Test",
            meta=meta,
            unplanned_extra_field="invalid",
        )
