"""Meaningful push detection and changed-symbol extraction."""

from __future__ import annotations

import difflib
import json
from pathlib import PurePosixPath
from typing import Iterable

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser

from backend.app.models import ChangedSymbol
from backend.app.parsing.javascript import JavaScriptParseError, parse_javascript

JAVASCRIPT_EXTENSIONS = {".js", ".jsx", ".cjs", ".mjs"}
MAX_DIFF_SNIPPET_CHARS = 5000


def _leaf_signature(node: Node, source: bytes, tokens: list[tuple[str, bytes]]) -> None:
    if node.type == "comment":
        return
    if node.child_count == 0:
        tokens.append((node.type, source[node.start_byte:node.end_byte]))
        return
    for child in node.children:
        _leaf_signature(child, source, tokens)


def _signature(path: str, text: str) -> tuple[object, ...]:
    """Normalize formatting and comments without altering literal contents."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in JAVASCRIPT_EXTENSIONS:
        source = text.encode("utf-8")
        parser = Parser(Language(tree_sitter_javascript.language()))
        tree = parser.parse(source)
        if tree.root_node.has_error:
            return ("invalid-js", text)
        tokens: list[tuple[str, bytes]] = []
        _leaf_signature(tree.root_node, source, tokens)
        return tuple(tokens)
    if suffix == ".json":
        try:
            return ("json", json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")))
        except (json.JSONDecodeError, TypeError):
            pass
    return tuple(text.split())


def _diff_snippet(before: str | None, after: str | None) -> str:
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()
    snippet = "\n".join(
        difflib.unified_diff(before_lines, after_lines, fromfile="before", tofile="after", lineterm="", n=3)
    )
    return snippet[:MAX_DIFF_SNIPPET_CHARS]


def _line_count(text: str | None) -> int:
    if not text:
        return 1
    return max(1, len(text.splitlines()))


def _symbol_index(chunks: Iterable) -> dict[tuple[str, str], list]:
    grouped: dict[tuple[str, str], list] = {}
    for chunk in chunks:
        grouped.setdefault((chunk.kind, chunk.name), []).append(chunk)
    return grouped


def _file_level_change(
    path: str,
    operation: str,
    before: str | None,
    after: str | None,
) -> ChangedSymbol:
    source = after if after is not None else before or ""
    return ChangedSymbol(
        file=path,
        name=PurePosixPath(path).name or path,
        kind=operation,
        start_line=1,
        end_line=_line_count(source),
        diff_snippet=_diff_snippet(before, after),
    )


def _changed_file_symbols(path: str, before: str | None, after: str | None) -> list[ChangedSymbol]:
    operation = "added" if before is None else "removed" if after is None else "modified"
    if _signature(path, before or "") == _signature(path, after or ""):
        return []

    if PurePosixPath(path).suffix.lower() not in JAVASCRIPT_EXTENSIONS:
        return [_file_level_change(path, operation, before, after)]

    try:
        before_chunks = parse_javascript(before, path).chunks if before is not None else []
        after_chunks = parse_javascript(after, path).chunks if after is not None else []
    except JavaScriptParseError:
        return [_file_level_change(path, operation, before, after)]

    before_index = _symbol_index(before_chunks)
    after_index = _symbol_index(after_chunks)
    changes: list[ChangedSymbol] = []
    for identity in sorted(set(before_index) | set(after_index)):
        before_group = before_index.get(identity, [])
        after_group = after_index.get(identity, [])
        paired_count = min(len(before_group), len(after_group))
        for index in range(paired_count):
            previous = before_group[index]
            current = after_group[index]
            if _signature(path, previous.text) == _signature(path, current.text):
                continue
            changes.append(ChangedSymbol(
                file=path,
                name=current.name,
                kind="modified",
                start_line=current.start_line,
                end_line=current.end_line,
                diff_snippet=_diff_snippet(previous.text, current.text),
            ))
        for chunk in before_group[paired_count:]:
            changes.append(ChangedSymbol(
                file=path,
                name=chunk.name,
                kind="removed",
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                diff_snippet=_diff_snippet(chunk.text, None),
            ))
        for chunk in after_group[paired_count:]:
            changes.append(ChangedSymbol(
                file=path,
                name=chunk.name,
                kind="added",
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                diff_snippet=_diff_snippet(None, chunk.text),
            ))

    return changes or [_file_level_change(path, operation, before, after)]


def find_changed_symbols(
    before_sources: dict[str, str | None],
    after_sources: dict[str, str | None],
) -> list[ChangedSymbol]:
    """Return meaningful file/symbol changes, excluding comment/format-only JS edits."""
    changed: list[ChangedSymbol] = []
    for path in sorted(set(before_sources) | set(after_sources)):
        changed.extend(_changed_file_symbols(path, before_sources.get(path), after_sources.get(path)))
    return changed