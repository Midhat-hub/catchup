"""Tests for source file inclusion and generated-file exclusion."""

import pytest

from backend.app.parsing.files import file_skip_reason


@pytest.mark.parametrize("path", ["src/app.js", "src/component.jsx", "scripts/task.cjs", "src/module.mjs"])
def test_accepts_supported_javascript_files(path):
    assert file_skip_reason(path, 100) is None


@pytest.mark.parametrize(
    ("path", "size", "reason"),
    [
        ("README.md", 20, "unsupported_extension"),
        ("node_modules/pkg/index.js", 20, "generated_or_dependency_directory"),
        ("dist/app.js", 20, "generated_or_dependency_directory"),
        ("public/app.min.js", 20, "generated_file"),
        ("src/huge.js", 1_000_001, "file_too_large"),
    ],
)
def test_rejects_unsupported_or_irrelevant_files(path, size, reason):
    assert file_skip_reason(path, size) == reason