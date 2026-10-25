"""Repository ingestion, JavaScript analysis, embeddings, and call graph."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
import logging
import posixpath
import re
from time import perf_counter
from typing import Callable, Sequence

from backend.app.github.client import GitHubClient
from backend.app.github.exceptions import GitHubError
from backend.app.github.models import RepoMetadata
from backend.app.index.embeddings import EmbeddingError, embed_texts
from backend.app.index.mapping_models import MappingStatistics, MappingStatus, SkippedFile
from backend.app.index.persistence import (
    get_mapping_status as read_mapping_status,
    mark_mapping_failed,
    persist_mapping,
    register_mapping,
)
from backend.app.parsing.files import DEFAULT_MAX_FILE_BYTES, file_skip_reason
from backend.app.parsing.javascript import (
    CodeChunk,
    ParsedJavaScriptFile,
    parse_javascript,
)

logger = logging.getLogger(__name__)
MAX_REPORTED_SKIPPED_FILES = 100


class RepositoryIdentifierError(ValueError):
    """The repository identifier is not in owner/name form."""


class UnsupportedRepositoryError(RuntimeError):
    """The public repository has no supported JavaScript source to map."""


class RepositoryMappingError(RuntimeError):
    """A repository could not be mapped completely."""


def parse_repository_identifier(repository: str) -> tuple[str, str]:
    """Validate and split a GitHub ``owner/repository`` identifier."""
    value = repository.strip()
    if len(value) > 255 or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise RepositoryIdentifierError("Repository must use the owner/repository format.")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise RepositoryIdentifierError("Repository owner and name must be valid GitHub identifiers.")
    return owner, name


def _is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return (
        name.endswith((".test.js", ".spec.js", ".test.jsx", ".spec.jsx", ".test.cjs", ".spec.cjs", ".test.mjs", ".spec.mjs"))
        or "__tests__" in {part.lower() for part in PurePosixPath(path).parts}
    )


def _module_file(importer: str, module: str, known_paths: set[str]) -> str | None:
    if not module.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), module))
    candidates = [base] if PurePosixPath(base).suffix else [base, *(base + suffix for suffix in (".js", ".cjs", ".mjs")), *(base + "/index" + suffix for suffix in (".js", ".cjs", ".mjs"))]
    return next((candidate for candidate in candidates if candidate in known_paths), None)


def _resolve_call(
    caller_file: str,
    call_target: str,
    receiver: str | None,
    parsed_by_path: dict[str, ParsedJavaScriptFile],
    symbols_by_path: dict[str, dict[str, list[int]]],
    known_paths: set[str],
    method_indices: set[int],
) -> int | None:
    """Resolve only unique same-file or explicitly imported in-repo symbols."""
    parsed_file = parsed_by_path[caller_file]
    for binding in parsed_file.imports:
        module_path: str | None = None
        target_name: str | None = None
        if receiver == binding.local_name and binding.imported_name in {"*", "default"}:
            module_path = _module_file(caller_file, binding.module, known_paths)
            target_name = call_target
        elif receiver is None and call_target == binding.local_name and binding.imported_name not in {"*", "default"}:
            module_path = _module_file(caller_file, binding.module, known_paths)
            target_name = binding.imported_name
        if module_path and target_name:
            candidates = symbols_by_path.get(module_path, {}).get(target_name, [])
            return candidates[0] if len(candidates) == 1 else None
        if call_target == binding.local_name or receiver == binding.local_name:
            return None

    if receiver is not None and receiver != "this":
        imported_receiver = any(binding.local_name == receiver for binding in parsed_file.imports)
        if imported_receiver:
            return None

    candidates = symbols_by_path.get(caller_file, {}).get(call_target, [])
    if receiver == "this":
        candidates = [index for index in candidates if index in method_indices]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _build_relationships(
    parsed_files: list[ParsedJavaScriptFile],
    chunks: list[CodeChunk],
) -> tuple[set[tuple[int, int]], set[tuple[str, int]]]:
    parsed_by_path = {parsed.path: parsed for parsed in parsed_files}
    known_paths = set(parsed_by_path)
    symbols_by_path: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    method_indices = {index for index, chunk in enumerate(chunks) if chunk.kind == "class_method"}

    for index, chunk in enumerate(chunks):
        if chunk.kind != "file_level":
            symbols_by_path[chunk.file_path][chunk.name].append(index)

    references: set[tuple[int, int]] = set()
    test_links: set[tuple[str, int]] = set()
    for caller_index, chunk in enumerate(chunks):
        for call in chunk.calls:
            target_index = _resolve_call(
                chunk.file_path,
                call.target,
                call.receiver,
                parsed_by_path,
                symbols_by_path,
                known_paths,
                method_indices,
            )
            if target_index is None:
                continue
            if target_index == caller_index and not (
                call.target == chunk.name and receiver_is_this_or_local(chunk, call.target, call.receiver)
            ):
                continue
            references.add((caller_index, target_index))
            if _is_test_file(chunk.file_path):
                test_links.add((chunk.file_path, target_index))
    return references, test_links


def receiver_is_this_or_local(chunk: CodeChunk, target: str, receiver: str | None) -> bool:
    """A same-chunk direct call is recursive when its target names that chunk."""
    return chunk.kind != "file_level" and chunk.name == target and receiver in {None, "this"}


def _record_skipped(skipped: list[SkippedFile], path: str, reason: str, count: int) -> int:
    count += 1
    if len(skipped) < MAX_REPORTED_SKIPPED_FILES:
        skipped.append(SkippedFile(path=path, reason=reason))
    return count


def _mapping_statistics_from_status(
    repository: str,
    commit_sha: str,
    status: dict,
    started: float,
) -> MappingStatistics:
    chunks = int(status["chunks_stored"])
    return MappingStatistics(
        repository=repository,
        commit_sha=commit_sha,
        source_files_examined=0,
        files_parsed=int(status["files_parsed"]),
        chunks_stored=chunks,
        embeddings_generated=0,
        references_stored=int(status["references_stored"]),
        test_links_stored=int(status["test_links_stored"]),
        duration_seconds=max(0.0, perf_counter() - started),
        already_mapped=True,
    )


def map_repository(
    repository: str,
    *,
    revision: str | None = None,
    github_client: GitHubClient | None = None,
    embedding_function: Callable[[Sequence[str]], list[list[float]]] | None = None,
) -> MappingStatistics:
    """Map the current default-branch revision of a public JavaScript repository."""
    started = perf_counter()
    owner, repo_name = parse_repository_identifier(repository)
    requested_name = f"{owner}/{repo_name}"
    client = github_client or GitHubClient()
    owns_client = github_client is None
    repo_id: int | None = None
    metadata: RepoMetadata | None = None
    try:
        logger.info("Mapping %s: fetching repository metadata", requested_name)
        metadata = client.get_repo(owner, repo_name)
        if metadata.language and metadata.language.lower() != "javascript":
            raise UnsupportedRepositoryError(
                f"Repository primary language is {metadata.language}; Catchup currently maps JavaScript repositories only."
            )
        if revision is None:
            logger.info("Mapping %s: resolving default branch commit", requested_name)
            latest = client.get_latest_commits(owner, repo_name, branch=metadata.default_branch, limit=1)
            if not latest:
                raise UnsupportedRepositoryError("The repository's default branch has no commit to map.")
            commit_sha = latest[0].sha
        else:
            if not re.fullmatch(r"[0-9a-fA-F]{7,64}", revision):
                raise RepositoryIdentifierError("Revision must be a valid Git commit SHA.")
            commit_sha = revision
        repo_id, already_mapped = register_mapping(metadata, commit_sha)
        if already_mapped:
            status = read_mapping_status(metadata.full_name)
            if status is None:
                raise RepositoryMappingError("The repository mapping disappeared while reading its status.")
            return _mapping_statistics_from_status(metadata.full_name, commit_sha, status, started)

        logger.info("Mapping %s@%s: fetching repository tree", metadata.full_name, commit_sha[:12])
        tree = client.list_files(owner, repo_name, tree_sha=commit_sha)
        parsed_files: list[ParsedJavaScriptFile] = []
        chunks: list[CodeChunk] = []
        embeddings: list[list[float]] = []
        seen_ranges: set[tuple[str, int, int, str]] = set()
        embed = embedding_function or embed_texts
        skipped: list[SkippedFile] = []
        skipped_count = 0
        source_files_examined = 0
        files_parsed = 0

        for item in tree:
            if item.type != "blob":
                continue
            reason = file_skip_reason(item.path, item.size)
            if reason is not None:
                if PurePosixPath(item.path).suffix.lower() in {".js", ".jsx", ".cjs", ".mjs"}:
                    skipped_count = _record_skipped(skipped, item.path, reason, skipped_count)
                continue

            source_files_examined += 1
            logger.debug("Mapping %s@%s: fetching blob %s", metadata.full_name, commit_sha[:12], item.path)
            try:
                content = client.get_blob_content(owner, repo_name, item.sha)
            except GitHubError as exc:
                if exc.status_code == 404:
                    skipped_count = _record_skipped(skipped, item.path, "missing_blob", skipped_count)
                    continue
                raise
            if len(content) > DEFAULT_MAX_FILE_BYTES:
                skipped_count = _record_skipped(skipped, item.path, "file_too_large", skipped_count)
                continue
            if b"\x00" in content:
                skipped_count = _record_skipped(skipped, item.path, "binary_file", skipped_count)
                continue
            try:
                source_text = content.decode("utf-8")
            except UnicodeDecodeError:
                skipped_count = _record_skipped(skipped, item.path, "non_utf8_file", skipped_count)
                continue

            logger.debug("Mapping %s@%s: parsing %s", metadata.full_name, commit_sha[:12], item.path)
            parsed = parse_javascript(source_text, item.path)
            parsed_files.append(parsed)
            files_parsed += 1
            file_chunks: list[CodeChunk] = []
            for chunk in parsed.chunks:
                source_range = (chunk.file_path, chunk.start_line, chunk.end_line, chunk.text)
                if source_range not in seen_ranges:
                    seen_ranges.add(source_range)
                    chunks.append(chunk)
                    file_chunks.append(chunk)
            if file_chunks:
                logger.info(
                    "Mapping %s@%s: embedding %s chunks from %s",
                    metadata.full_name, commit_sha[:12], len(file_chunks), item.path,
                )
                file_embeddings = embed([chunk.text for chunk in file_chunks])
                if len(file_embeddings) != len(file_chunks) or any(
                    len(vector) != 384 for vector in file_embeddings
                ):
                    raise EmbeddingError("Embedding output did not match the 384-dimensional chunk contract.")
                embeddings.extend(file_embeddings)

        if not parsed_files:
            raise UnsupportedRepositoryError("No supported UTF-8 JavaScript source files were found to map.")

        if not chunks:
            raise UnsupportedRepositoryError("The repository contains JavaScript files but no parseable code chunks.")

        logger.info("Mapping %s@%s: building relationships for %s chunks", metadata.full_name, commit_sha[:12], len(chunks))
        references, test_links = _build_relationships(parsed_files, chunks)
        logger.info("Mapping %s@%s: persisting %s chunks and vectors", metadata.full_name, commit_sha[:12], len(chunks))
        stored_chunks, stored_references, stored_test_links = persist_mapping(
            repo_id,
            commit_sha,
            chunks,
            embeddings,
            references,
            test_links,
        )
        return MappingStatistics(
            repository=metadata.full_name,
            commit_sha=commit_sha,
            source_files_examined=source_files_examined,
            files_parsed=files_parsed,
            chunks_stored=stored_chunks,
            embeddings_generated=len(embeddings),
            references_stored=stored_references,
            test_links_stored=stored_test_links,
            duration_seconds=max(0.0, perf_counter() - started),
            skipped_files=skipped,
            skipped_file_count=skipped_count,
            skipped_files_truncated=skipped_count > len(skipped),
        )
    except Exception as exc:
        try:
            mark_mapping_failed(
                metadata.full_name if metadata is not None else requested_name,
                str(exc),
                repo_id=repo_id,
            )
        except Exception:
            logger.exception("Could not persist failed mapping status for %s", requested_name)
        logger.exception("Repository mapping failed for %s: %s", requested_name, exc)
        raise
    finally:
        if owns_client:
            client.close()


def get_repository_mapping_status(repository: str) -> MappingStatus | None:
    """Read current mapping state without contacting GitHub."""
    owner, repo_name = parse_repository_identifier(repository)
    status = read_mapping_status(f"{owner}/{repo_name}")
    return MappingStatus(**status) if status is not None else None
