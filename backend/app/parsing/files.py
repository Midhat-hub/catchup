"""Repository file filtering for JavaScript source ingestion."""

from pathlib import PurePosixPath

SUPPORTED_EXTENSIONS = {".js", ".jsx", ".cjs", ".mjs"}
IGNORED_DIRECTORIES = {
    ".git", ".next", ".nuxt", ".cache", ".turbo", "node_modules", "bower_components",
    "build", "dist", "coverage", "vendor", "out", "tmp", "temp",
}
GENERATED_MARKERS = (".min.", ".bundle.", ".generated.", ".g.")
DEFAULT_MAX_FILE_BYTES = 1_000_000


def file_skip_reason(path: str, size: int | None, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> str | None:
    """Return why a tree entry should be skipped, or ``None`` if it is source."""
    normalized = path.replace("\\", "/")
    file_path = PurePosixPath(normalized)
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported_extension"
    if any(part.lower() in IGNORED_DIRECTORIES for part in file_path.parts[:-1]):
        return "generated_or_dependency_directory"
    filename = file_path.name.lower()
    if any(marker in filename for marker in GENERATED_MARKERS):
        return "generated_file"
    if filename.endswith((".d.js", ".min.js", ".map.js")):
        return "generated_file"
    if size is not None and size > max_file_bytes:
        return "file_too_large"
    return None