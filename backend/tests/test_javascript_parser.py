"""Focused tests for Tree-sitter JavaScript extraction."""

from backend.app.parsing.javascript import parse_javascript


def test_parse_supported_declarations_imports_calls_and_lines():
    source = '''import express from "express";
import { helper as importedHelper } from "./helper.js";
const fs = require("node:fs");
const { readFile: read } = require("node:fs/promises");
const app = express();
const topLevel = 1;

function declared() {
  importedHelper();
  function nested() { return fs.readFileSync("x"); }
  return nested();
}

const named = function namedInner() {};
const arrow = async () => named();

class Worker {
  async run() { return arrow(); }
}

app.get("/books", async (request, response) => {
  response.json(await declared());
});
'''

    parsed = parse_javascript(source, "src/app.js")
    by_name = {chunk.name: chunk for chunk in parsed.chunks}

    assert by_name["declared"].kind == "function_declaration"
    assert by_name["nested"].start_line == 10
    assert by_name["named"].kind == "named_function_expression"
    assert by_name["arrow"].metadata["async"] is True
    assert by_name["run"].kind == "class_method"
    assert by_name["GET /books"].kind == "route_handler"
    assert by_name["GET /books"].metadata["http_method"] == "GET"
    assert any(chunk.kind == "file_level" and "topLevel" in chunk.text for chunk in parsed.chunks)
    assert any(binding.local_name == "importedHelper" and binding.module == "./helper.js" for binding in parsed.imports)
    assert any(binding.local_name == "fs" and binding.commonjs for binding in parsed.imports)
    assert any(binding.local_name == "read" and binding.imported_name == "readFile" for binding in parsed.imports)
    assert by_name["declared"].start_line == 8
    assert by_name["declared"].end_line == 12
    assert any(call.target == "importedHelper" for call in by_name["declared"].calls)


def test_identical_source_ranges_are_not_duplicated():
    parsed = parse_javascript("const run = () => 1;\n", "src/run.js")
    arrow = next(chunk for chunk in parsed.chunks if chunk.kind == "arrow_function")
    file_chunks = [chunk for chunk in parsed.chunks if chunk.kind == "file_level"]

    assert arrow.text == "() => 1"
    assert file_chunks
    assert all(arrow.text not in chunk.text for chunk in file_chunks)
    assert "const run = " in "".join(chunk.text for chunk in file_chunks)


def test_nested_route_function_is_not_mislabeled_as_another_route():
    parsed = parse_javascript(
        'app.get("/books", (request, response) => { function helper() {} });',
        "src/routes.js",
    )
    kinds_by_name = {chunk.name: chunk.kind for chunk in parsed.chunks}

    assert kinds_by_name["GET /books"] == "route_handler"
    assert kinds_by_name["helper"] == "function_declaration"