"""Tree-sitter based JavaScript parsing and source chunking."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Iterable

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser
from tree_sitter import Language, Node, Parser


class JavaScriptParseError(ValueError):
    """Raised when Tree-sitter cannot parse a complete source file."""


@dataclass(frozen=True)
class ImportBinding:
    """A statically declared JavaScript module import."""

    local_name: str
    module: str
    imported_name: str | None = None
    commonjs: bool = False


@dataclass(frozen=True)
class CallSite:
    """A statically identifiable call made from a chunk."""

    target: str
    line: int
    receiver: str | None = None


@dataclass
class CodeChunk:
    """A source range representing a function/method or module-level code."""

    file_path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    calls: list[CallSite] = field(default_factory=list)


@dataclass
class ParsedJavaScriptFile:
    """Parsed symbols, imports, and calls for one JavaScript source file."""

    path: str
    chunks: list[CodeChunk]
    imports: list[ImportBinding]
    identifiers: set[str]


def _parser() -> Parser:
    return Parser(Language(tree_sitter_javascript.language()))


def _walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _text(source: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _identifier(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    if node.type in {"identifier", "property_identifier", "shorthand_property_identifier_pattern"}:
        return _text(source, node)
    return None


def _function_nodes(root: Node) -> list[Node]:
    return [
        node for node in _walk(root)
        if node.type in {"function_declaration", "function_expression", "arrow_function", "method_definition"}
    ]


def _assigned_name(node: Node, source: bytes) -> str | None:
    parent = node.parent
    if parent is None:
        return None
    if parent.type == "variable_declarator" and parent.child_by_field_name("value") == node:
        return _identifier(parent.child_by_field_name("name"), source)
    if parent.type == "assignment_expression" and parent.child_by_field_name("right") == node:
        return _identifier(parent.child_by_field_name("left"), source)
    return None


def _route_metadata(node: Node, source: bytes) -> tuple[str, str] | None:
    current = node.parent
    while current is not None:
        if current.type in {"function_declaration", "function_expression", "arrow_function", "method_definition"}:
            return None
        if current.type == "call_expression":
            function = current.child_by_field_name("function")
            callee = _text(source, function)
            if "." in callee and callee.rsplit(".", 1)[1] in {
                "get", "post", "put", "patch", "delete", "options", "head", "all", "use"
            }:
                arguments = current.child_by_field_name("arguments")
                if arguments is None or node not in arguments.named_children:
                    current = current.parent
                    continue
                route = ""
                for argument in arguments.named_children:
                    if argument == node:
                        break
                    if argument.type in {"string", "template_string"}:
                        route = _text(source, argument).strip("'\"` ")
                        break
                method = callee.rsplit(".", 1)[1].upper()
                return f"{method} {route}".strip(), method
        current = current.parent
    return None


def _describe_function(node: Node, source: bytes) -> tuple[str, str, dict[str, Any]] | None:
    if node.type == "function_declaration":
        name = _identifier(node.child_by_field_name("name"), source)
        kind = "function_declaration"
    elif node.type == "method_definition":
        name_node = node.child_by_field_name("name")
        name = _text(source, name_node) or "<anonymous-method>"
        kind = "class_method"
    else:
        name = _assigned_name(node, source)
        kind = "named_function_expression" if node.type == "function_expression" else "arrow_function"

    route = _route_metadata(node, source)
    if route is not None:
        name, method = route
        kind = "route_handler"
    elif name is None:
        return None

    metadata: dict[str, Any] = {"async": _text(source, node).lstrip().startswith("async")}
    if route is not None:
        metadata["http_method"] = route[1]
    return name, kind, metadata


def _module_imports(root: Node, source: bytes) -> list[ImportBinding]:
    bindings: list[ImportBinding] = []
    for node in _walk(root):
        if node.type == "import_statement":
            module_node = node.child_by_field_name("source")
            module = _text(source, module_node).strip("'\" ")
            for specifier in _walk(node):
                if specifier.type == "import_specifier":
                    imported = _identifier(specifier.child_by_field_name("name"), source)
                    local = _identifier(specifier.child_by_field_name("alias"), source) or imported
                    if local:
                        bindings.append(ImportBinding(local, module, imported))
                elif specifier.type == "namespace_import":
                    local_node = next((child for child in specifier.named_children if child.type == "identifier"), None)
                    local = _identifier(local_node, source)
                    if local:
                        bindings.append(ImportBinding(local, module, "*"))
            clause = node.child_by_field_name("import_clause")
            if clause is not None:
                default = next((child for child in clause.named_children if child.type == "identifier"), None)
                local = _identifier(default, source)
                if local:
                    bindings.append(ImportBinding(local, module, "default"))
        elif node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is None or value.type != "call_expression":
                continue
            callee = value.child_by_field_name("function")
            arguments = value.child_by_field_name("arguments")
            if _identifier(callee, source) != "require" or arguments is None or not arguments.named_children:
                continue
            module = _text(source, arguments.named_children[0]).strip("'\" ")
            local_node = node.child_by_field_name("name")
            if local_node is not None and local_node.type == "identifier":
                local = _identifier(local_node, source)
                if local:
                    bindings.append(ImportBinding(local, module, "*", commonjs=True))
            elif local_node is not None and local_node.type == "object_pattern":
                for property_node in local_node.named_children:
                    imported = _identifier(property_node.child_by_field_name("key"), source)
                    local = _identifier(property_node.child_by_field_name("value"), source) or imported
                    if imported and local:
                        bindings.append(ImportBinding(local, module, imported, commonjs=True))
    return bindings


def _call_target(node: Node, source: bytes) -> tuple[str, str | None] | None:
    function = node.child_by_field_name("function")
    if function is None:
        return None
    if function.type in {"identifier", "member_expression"}:
        expression = _text(source, function)
        if function.type == "member_expression":
            obj = function.child_by_field_name("object")
            prop = function.child_by_field_name("property")
            target = _identifier(prop, source)
            if target:
                return target, _text(source, obj)
        return expression, None
    return None


def parse_javascript(source_text: str, file_path: str) -> ParsedJavaScriptFile:
    """Parse JavaScript source into unique symbol and module-level chunks.

    Nested functions receive their own chunks. The enclosing function also retains
    its original full source range, so nested ranges intentionally overlap.
    """
    source = source_text.encode("utf-8")
    tree = _parser().parse(source)
    root = tree.root_node
    if root.has_error:
        error_node = next(
            (node for node in _walk(root) if node.type == "ERROR" or node.is_missing),
            root,
        )
        raise JavaScriptParseError(
            f"JavaScript parse error in {file_path} at line {error_node.start_point.row + 1}."
        )

    function_nodes = _function_nodes(root)
    descriptors: dict[tuple[int, int], tuple[Node, str, str, dict[str, Any]]] = {}
    for node in function_nodes:
        description = _describe_function(node, source)
        if description is not None:
            name, kind, metadata = description
            descriptors[(node.start_byte, node.end_byte)] = (node, name, kind, metadata)

    chunks: list[CodeChunk] = []
    function_chunks: dict[tuple[int, int], CodeChunk] = {}
    for node, name, kind, metadata in descriptors.values():
        chunk = CodeChunk(
            file_path=file_path,
            name=name,
            kind=kind,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            text=_text(source, node),
            metadata=metadata,
        )
        chunks.append(chunk)
        function_chunks[(node.start_byte, node.end_byte)] = chunk

    file_level_ranges: list[tuple[int, int, CodeChunk]] = []
    offset = 0
    for node, *_ in sorted(descriptors.values(), key=lambda value: value[0].start_byte):
        start, end = node.start_byte, node.end_byte
        if start > offset:
            module_text = source[offset:start].decode("utf-8", errors="replace")
            if module_text.strip():
                chunk = CodeChunk(
                    file_path=file_path,
                    name=file_path.rsplit("/", 1)[-1],
                    kind="file_level",
                    start_line=source.count(b"\n", 0, offset) + 1,
                    end_line=source.count(b"\n", 0, start) + 1,
                    text=module_text,
                )
                chunks.append(chunk)
                file_level_ranges.append((offset, start, chunk))
        offset = max(offset, end)
    if offset < len(source):
        module_text = source[offset:].decode("utf-8", errors="replace")
        if module_text.strip():
            chunk = CodeChunk(
                file_path=file_path,
                name=file_path.rsplit("/", 1)[-1],
                kind="file_level",
                start_line=source.count(b"\n", 0, offset) + 1,
                end_line=source.count(b"\n") + 1,
                text=module_text,
            )
            chunks.append(chunk)
            file_level_ranges.append((offset, len(source), chunk))

    chunks.sort(key=lambda chunk: (chunk.start_line, chunk.end_line, chunk.kind, chunk.name))
    for call in (node for node in _walk(root) if node.type == "call_expression"):
        target = _call_target(call, source)
        if target is None:
            continue
        target_name, receiver = target
        current: Node | None = call
        owner = None
        while current is not None:
            owner = function_chunks.get((current.start_byte, current.end_byte))
            if owner is not None:
                break
            current = current.parent
        if owner is None:
            owner = next(
                (chunk for start, end, chunk in file_level_ranges if start <= call.start_byte < end),
                None,
            )
        if owner is not None:
            call_site = CallSite(target_name, call.start_point.row + 1, receiver)
            if call_site not in owner.calls:
                owner.calls.append(call_site)

    identifiers = {
        _text(source, node)
        for node in _walk(root)
        if node.type == "identifier" and _text(source, node)
    }
    return ParsedJavaScriptFile(
        path=file_path,
        chunks=chunks,
        imports=_module_imports(root, source),
        identifiers=identifiers,
    )