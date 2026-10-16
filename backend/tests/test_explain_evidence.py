"""Offline Step 4 evidence selection, flags, and token-budget tests."""

from backend.app.explain.evidence import (
    DEFAULT_PROMPT_TOKEN_BUDGET,
    detect_risk_flags,
    select_evidence_batches,
)
from backend.app.models import CallerReference, ChangedSymbol, PushData, PushFile, SimilarCode, TestLink


def _change(path, name, snippet):
    return ChangedSymbol(
        file=path,
        name=name,
        kind="modified",
        start_line=1,
        end_line=3,
        diff_snippet=snippet,
    )


def test_extra_evidence_false_excludes_relationships_similar_code_and_flags():
    batches = select_evidence_batches(
        [_change("src/books.js", "saveBook", "return book;")],
        [CallerReference(caller_file="src/app.js", caller_name="route", target_name="saveBook")],
        [TestLink(test_file="tests/books.test.js", target_name="saveBook")],
        [SimilarCode(file="src/cache.js", name="write", score=0.9)],
        ["touches the database"],
        use_extra_evidence=False,
    )

    assert len(batches) == 1
    assert batches[0].changes
    assert not batches[0].callers
    assert not batches[0].tests
    assert not batches[0].similar_code
    assert not batches[0].flags


def test_evidence_selection_obeys_budget_and_splits_by_file():
    changes = [
        _change("src/a.js", f"function{index}", "x" * 1200)
        for index in range(6)
    ] + [_change("src/b.js", "other", "return true;")]
    callers = [
        CallerReference(caller_file=f"src/caller{index}.js", caller_name=f"caller{index}", target_name="function0")
        for index in range(10)
    ]
    tests = [TestLink(test_file=f"tests/test{index}.js", target_name="function0") for index in range(10)]
    similar = [SimilarCode(file=f"src/near{index}.js", name=f"near{index}", score=0.8, snippet="similar " * 100) for index in range(10)]

    batches = select_evidence_batches(
        changes,
        callers,
        tests,
        similar,
        ["many files changed"],
        use_extra_evidence=True,
        token_budget=DEFAULT_PROMPT_TOKEN_BUDGET,
    )

    assert len(batches) >= 2
    assert all(batch.token_count <= DEFAULT_PROMPT_TOKEN_BUDGET for batch in batches)
    assert {change.file for batch in batches for change in batch.changes} == {"src/a.js", "src/b.js"}
    assert all(len({change.file for change in batch.changes}) == 1 for batch in batches)


def test_budget_rejects_impossible_budget():
    try:
        select_evidence_batches([], [], [], [], [], use_extra_evidence=False, token_budget=1)
    except ValueError as exc:
        assert "token_budget" in str(exc)
    else:
        raise AssertionError("Expected an invalid token budget to fail")


def test_rule_flags_cover_database_config_dependencies_routes_file_count_and_tests():
    push = PushData(
        repo="owner/repo",
        before_commit="a" * 40,
        after_commit="b" * 40,
        files=[
            PushFile(path="db/migration.sql", status="modified"),
            PushFile(path="config/runtime.yml", status="modified"),
            PushFile(path="package.json", status="modified"),
            PushFile(path="src/routes.js", status="modified"),
            *[PushFile(path=f"src/extra{index}.js", status="modified") for index in range(6)],
        ],
    )
    after = {
        "db/migration.sql": "ALTER TABLE books ADD COLUMN isbn TEXT;",
        "config/runtime.yml": "port: 8081",
        "package.json": '{"dependencies":{"express":"5.0.0"}}',
        "src/routes.js": "router.post('/books', saveBook);",
    }
    before = {
        "package.json": '{"dependencies":{"express":"4.0.0"}}',
    }
    changes = [_change("src/routes.js", "POST /books", "router.post('/books', saveBook);")]

    flags = detect_risk_flags(push, before, after, changes, [])

    assert "touches the database" in flags
    assert "changes configuration" in flags
    assert "changes package dependencies" in flags
    assert "changes an API route" in flags
    assert "many files changed at once" in flags
    assert "no tests cover the changed code" in flags


def test_test_file_only_change_is_not_flagged_as_untested_production_code():
    push = PushData(
        repo="owner/repo",
        before_commit="a" * 40,
        after_commit="b" * 40,
        files=[PushFile(path="tests/books.test.js", status="modified")],
    )
    changes = [_change("tests/books.test.js", "testBook", "assert.equal(1, 2)")]

    flags = detect_risk_flags(push, {}, {}, changes, [])

    assert "no tests cover the changed code" not in flags


def test_all_supported_test_suffixes_are_not_flagged_as_uncovered_code():
    for path in ("tests/example.test.cjs", "src/__tests__/example.mjs", "test/example.spec.jsx"):
        push = PushData(
            repo="owner/repo",
            before_commit="a" * 40,
            after_commit="b" * 40,
            files=[PushFile(path=path, status="modified")],
        )
        changes = [_change(path, "testHelper", "assert.equal(1, 1)")]

        flags = detect_risk_flags(push, {}, {}, changes, [])

        assert "no tests cover the changed code" not in flags