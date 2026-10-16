"""Offline diff/symbol tests for Step 4 meaningful-change detection."""

import pytest

from backend.app.explain.changes import find_changed_symbols


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("function saveBook(id) { return id; }", "function saveBook(id) { // now returns\n return id; }"),
        ("function saveBook(id) { return id; }", "function  saveBook( id ){\n return id ;\n}"),
        ("// only a note\nconst ready = true;", "/* updated note */\nconst ready = true;"),
    ],
)
def test_comment_and_whitespace_only_javascript_changes_are_ignored(before, after):
    assert find_changed_symbols({"src/books.js": before}, {"src/books.js": after}) == []


def test_function_body_change_is_reported_with_source_lines():
    changes = find_changed_symbols(
        {"src/books.js": "function saveBook(id) {\n  return id;\n}\n"},
        {"src/books.js": "function saveBook(id, title) {\n  return { id, title };\n}\n"},
    )

    assert len(changes) == 1
    assert changes[0].name == "saveBook"
    assert changes[0].kind == "modified"
    assert changes[0].start_line == 1
    assert "title" in changes[0].diff_snippet


def test_rename_add_delete_and_file_level_configuration_changes():
    renamed = find_changed_symbols(
        {"src/books.js": "function oldName() {}"},
        {"src/books.js": "function newName() {}"},
    )
    removed = find_changed_symbols(
        {"src/books.js": "function deleteBook() {}"},
        {"src/books.js": ""},
    )
    added = find_changed_symbols(
        {"src/books.js": None},
        {"src/books.js": "function addBook() {}"},
    )
    config = find_changed_symbols(
        {"config/runtime.json": '{"timeout": 10}'},
        {"config/runtime.json": '{ "timeout": 20 }'},
    )

    assert {(change.name, change.kind) for change in renamed} == {("oldName", "removed"), ("newName", "added")}
    assert [(change.name, change.kind) for change in removed] == [("deleteBook", "removed")]
    assert [(change.name, change.kind) for change in added] == [("addBook", "added")]
    assert len(config) == 1
    assert config[0].kind == "modified"
    assert config[0].name == "runtime.json"


def test_invalid_javascript_falls_back_to_file_level_change():
    changes = find_changed_symbols(
        {"src/broken.js": "function run() {}"},
        {"src/broken.js": "function run( {"},
    )

    assert len(changes) == 1
    assert changes[0].name == "broken.js"
    assert changes[0].kind == "modified"