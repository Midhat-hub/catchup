"""Offline tests for mapping orchestration and static relationships."""

from unittest.mock import Mock

import pytest

from backend.app.github.exceptions import RepoNotFoundError
from backend.app.github.models import CommitItem, RepoFileItem, RepoMetadata
from backend.app.index import mapping
from backend.app.index.embeddings import EmbeddingError
from backend.app.parsing.javascript import JavaScriptParseError, parse_javascript


def test_call_graph_is_import_aware_and_skips_ambiguous_calls():
    parsed_files = [
        parse_javascript("export function target() {}\n", "src/lib.js"),
        parse_javascript(
            'import { target as imported } from "./lib.js";\n'
            "function caller() { imported(); }\n"
            "function unresolved() { missing(); }\n",
            "src/caller.js",
        ),
        parse_javascript(
            "function duplicate() {}\n"
            "function duplicate() {}\n"
            "function uncertain() { duplicate(); }\n",
            "src/ambiguous.js",
        ),
        parse_javascript("function recursive() { recursive(); }\n", "src/recursive.js"),
    ]
    chunks = [chunk for parsed in parsed_files for chunk in parsed.chunks]
    references, _ = mapping._build_relationships(parsed_files, chunks)
    by_index = {index: chunk for index, chunk in enumerate(chunks)}
    edges = {(by_index[caller].name, by_index[callee].name) for caller, callee in references}

    assert ("caller", "target") in edges
    assert ("uncertain", "duplicate") not in edges
    assert ("unresolved", "missing") not in edges
    assert ("recursive", "recursive") in edges


def test_test_files_link_only_resolved_targets():
    parsed_files = [
        parse_javascript("export function saveBook() {}\nexport function deleteBook() {}", "src/books.js"),
        parse_javascript(
            'import { saveBook } from "../books.js";\n'
            'test("saves", () => { saveBook(); unknownFunction(); });\n',
            "src/__tests__/books.spec.js",
        ),
    ]
    chunks = [chunk for parsed in parsed_files for chunk in parsed.chunks]
    _, links = mapping._build_relationships(parsed_files, chunks)
    link_names = {(path, chunks[index].name) for path, index in links}

    assert ("src/__tests__/books.spec.js", "saveBook") in link_names
    assert all(name != "deleteBook" for _, name in link_names)
    assert all(name != "unknownFunction" for _, name in link_names)


class FakeGitHubClient:
    def __init__(self, files, contents):
        self.files = files
        self.contents = contents
        self.tree_revision = None
        self.blob_requests = []

    def get_repo(self, owner, name):
        return RepoMetadata(
            id=123,
            name=name,
            full_name=f"{owner}/{name}",
            owner=owner,
            default_branch="main",
            is_private=False,
            language="JavaScript",
        )

    def get_latest_commits(self, owner, name, branch, limit):
        assert branch == "main"
        return [CommitItem(sha="a" * 40, message="test", parents=[])]

    def list_files(self, owner, name, tree_sha):
        self.tree_revision = tree_sha
        return self.files

    def get_blob_content(self, owner, name, blob_sha):
        self.blob_requests.append(blob_sha)
        if blob_sha not in self.contents:
            raise RepoNotFoundError("blob missing", status_code=404)
        return self.contents[blob_sha]


def _fake_repository():
    files = [
        RepoFileItem(path="src/books.js", type="blob", size=100, sha="books"),
        RepoFileItem(path="src/__tests__/books.test.js", type="blob", size=100, sha="test"),
        RepoFileItem(path="node_modules/pkg/index.js", type="blob", size=20, sha="vendor"),
        RepoFileItem(path="dist/app.min.js", type="blob", size=30, sha="minified"),
        RepoFileItem(path="src/large.js", type="blob", size=1_000_001, sha="large"),
        RepoFileItem(path="README.md", type="blob", size=20, sha="readme"),
    ]
    contents = {
        "books": b"export function saveBook() { return true; }\nfunction caller() { saveBook(); }\n",
        "test": b'import { saveBook } from "../books.js";\ntest("saves", () => { saveBook(); unknown(); });\n',
    }
    return FakeGitHubClient(files, contents)


def test_map_repository_persists_exact_revision_and_statistics(monkeypatch):
    client = _fake_repository()
    persisted = {}
    embedding_batches = []
    monkeypatch.setattr(mapping, "register_mapping", lambda metadata, sha: (9, False))
    monkeypatch.setattr(mapping, "mark_mapping_failed", Mock())

    def embed(texts):
        embedding_batches.append(list(texts))
        return [[0.25] * 384 for _ in texts]

    def persist(repo_id, sha, chunks, embeddings, references, test_links):
        persisted.update(
            repo_id=repo_id,
            sha=sha,
            chunks=chunks,
            embeddings=embeddings,
            references=references,
            test_links=test_links,
        )
        return len(chunks), len(references), len(test_links)

    monkeypatch.setattr(mapping, "persist_mapping", persist)
    monkeypatch.setattr(mapping, "read_mapping_status", Mock())

    result = mapping.map_repository(
        "owner/books",
        github_client=client,
        embedding_function=embed,
    )

    assert result.status == "ready"
    assert result.repository == "owner/books"
    assert result.commit_sha == "a" * 40
    assert client.tree_revision == result.commit_sha
    assert result.source_files_examined == 2
    assert result.files_parsed == 2
    assert result.embeddings_generated == result.chunks_stored
    assert len(embedding_batches) == 2
    assert sum(len(batch) for batch in embedding_batches) == result.chunks_stored
    assert result.skipped_file_count == 3
    assert persisted["repo_id"] == 9
    assert persisted["sha"] == result.commit_sha
    assert (
        "src/__tests__/books.test.js",
        next(i for i, chunk in enumerate(persisted["chunks"]) if chunk.name == "saveBook"),
    ) in persisted["test_links"]
    assert any(
        persisted["chunks"][caller].name == "caller" and persisted["chunks"][callee].name == "saveBook"
        for caller, callee in persisted["references"]
    )


def test_same_revision_is_idempotent_and_skips_download(monkeypatch):
    client = _fake_repository()
    monkeypatch.setattr(mapping, "register_mapping", lambda metadata, sha: (9, True))
    monkeypatch.setattr(mapping, "read_mapping_status", lambda name: {
        "name": name,
        "status": "ready",
        "mapped_commit_sha": "a" * 40,
        "mapping_error": None,
        "files_parsed": 2,
        "chunks_stored": 4,
        "references_stored": 1,
        "test_links_stored": 1,
    })
    monkeypatch.setattr(mapping, "persist_mapping", Mock(side_effect=AssertionError("must not rewrite")))

    result = mapping.map_repository("owner/books", github_client=client)

    assert result.already_mapped is True
    assert result.source_files_examined == 0
    assert result.embeddings_generated == 0
    assert result.chunks_stored == 4
    assert client.tree_revision is None
    assert client.blob_requests == []


def test_mapping_status_is_converted_to_api_model(monkeypatch):
    monkeypatch.setattr(mapping, "read_mapping_status", lambda name: {
        "repository": "Owner/Repo",
        "status": "ready",
        "mapped_commit_sha": "a" * 40,
        "mapping_error": None,
        "files_parsed": 2,
        "chunks_stored": 3,
        "references_stored": 1,
        "test_links_stored": 1,
    })

    result = mapping.get_repository_mapping_status("owner/repo")

    assert result.repository == "Owner/Repo"
    assert result.mapped_commit_sha == "a" * 40


def test_missing_blob_is_reported_as_skipped_without_invalidating_mapping(monkeypatch):
    client = _fake_repository()
    client.files.append(RepoFileItem(path="src/missing.js", type="blob", size=20, sha="gone"))
    monkeypatch.setattr(mapping, "register_mapping", lambda metadata, sha: (9, False))
    monkeypatch.setattr(mapping, "mark_mapping_failed", Mock())
    monkeypatch.setattr(mapping, "persist_mapping", lambda repo_id, sha, chunks, vectors, refs, links: (len(chunks), len(refs), len(links)))

    result = mapping.map_repository(
        "owner/books",
        github_client=client,
        embedding_function=lambda texts: [[0.1] * 384 for _ in texts],
    )

    assert result.status == "ready"
    assert result.skipped_file_count == 4
    assert any(file.path == "src/missing.js" and file.reason == "missing_blob" for file in result.skipped_files)


def test_non_javascript_repository_is_rejected_before_file_mapping(monkeypatch):
    client = _fake_repository()
    metadata = client.get_repo("owner", "books").model_copy(update={"language": "Python"})
    client.get_repo = lambda owner, name: metadata
    mark_failed = Mock()
    monkeypatch.setattr(mapping, "mark_mapping_failed", mark_failed)
    monkeypatch.setattr(mapping, "register_mapping", Mock(side_effect=AssertionError("must reject before mapping")))

    with pytest.raises(mapping.UnsupportedRepositoryError, match="JavaScript repositories only"):
        mapping.map_repository("owner/books", github_client=client)

    assert mark_failed.called


@pytest.mark.parametrize(
    ("contents", "exception"),
    [
        ({"books": b"function broken( {"}, JavaScriptParseError),
        ({"books": b"function works() {}"}, EmbeddingError),
    ],
)
def test_failed_mapping_sets_failed_status_and_never_persists(monkeypatch, contents, exception):
    client = _fake_repository()
    client.contents.update(contents)
    monkeypatch.setattr(mapping, "register_mapping", lambda metadata, sha: (9, False))
    mark_failed = Mock()
    monkeypatch.setattr(mapping, "mark_mapping_failed", mark_failed)
    monkeypatch.setattr(mapping, "persist_mapping", Mock(side_effect=AssertionError("must not persist")))
    embed = (lambda texts: (_ for _ in ()).throw(EmbeddingError("test model failure")))

    with pytest.raises(exception):
        mapping.map_repository("owner/books", github_client=client, embedding_function=embed)

    assert mark_failed.call_args.kwargs["repo_id"] == 9