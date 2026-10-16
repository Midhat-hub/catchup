"""Offline scenarios matching the ten Step 7 push types in PROJECT2.md."""

import pytest

from backend.app.explain.changes import find_changed_symbols
from backend.app.explain.evidence import detect_risk_flags
from backend.app.models import PushData, PushFile, TestLink


@pytest.mark.parametrize(
    ("scenario", "path", "before", "after", "expected_name", "expected_kind"),
    [
        ("rename called function", "src/books.js", "function oldBook() {}", "function newBook() {}", "newBook", "added"),
        ("add function parameter", "src/books.js", "function saveBook(id) { return id; }", "function saveBook(id, title) { return title; }", "saveBook", "modified"),
        ("delete function", "src/books.js", "function deleteBook() {}", "", "deleteBook", "removed"),
        ("change untested function", "src/untested.js", "function calculate() { return 1; }", "function calculate() { return 2; }", "calculate", "modified"),
        ("add feature", "src/books.js", "", "function searchBooks(term) { return term; }", "searchBooks", "added"),
        ("comment-only push", "src/books.js", "function saveBook() { return 1; }", "function saveBook() { // note\n return 1; }", None, None),
        ("fix small bug", "src/books.js", "function total(items) { return items.length - 1; }", "function total(items) { return items.length; }", "total", "modified"),
        ("change test-only code", "tests/books.test.js", "function testBook() { assert.equal(1, 1); }", "function testBook() { assert.equal(1, 2); }", "testBook", "modified"),
        ("change config", "config/runtime.json", '{"port":8000}', '{"port":8080}', "runtime.json", "modified"),
        ("change database migration", "migrations/002.sql", "SELECT id FROM books;", "SELECT id, title FROM books;", "002.sql", "modified"),
    ],
)
def test_planned_push_scenario(scenario, path, before, after, expected_name, expected_kind):
    changes = find_changed_symbols({path: before}, {path: after})

    if expected_name is None:
        assert changes == [], scenario
    else:
        assert any(change.name == expected_name and change.kind == expected_kind for change in changes), scenario


def test_no_test_coverage_flag_for_uncovered_changed_symbol():
    push = PushData(
        repo="owner/repo",
        before_commit="a" * 40,
        after_commit="b" * 40,
        files=[PushFile(path="src/untested.js", status="modified")],
    )
    changes = find_changed_symbols(
        {"src/untested.js": "function calculate() { return 1; }"},
        {"src/untested.js": "function calculate() { return 2; }"},
    )

    flags = detect_risk_flags(push, {}, {}, changes, [])

    assert "no tests cover the changed code" in flags