"""Offline orchestration tests for Step 4 explain_push."""

from unittest.mock import Mock

import pytest

from backend.app.explain import pipeline
from backend.app.explain.evidence import Evidence
from backend.app.explain.gemini import GeminiDraft, GeminiError, GeminiResult
from backend.app.github.models import CommitItem, RepoFileItem, RepoMetadata
from backend.app.models import MightAffect, PushData, PushFile

BEFORE = "a" * 40
AFTER = "b" * 40


class FakeGitHubClient:
    def __init__(self, before_source, after_source):
        self.sources = {BEFORE: before_source, AFTER: after_source}
        self.tree_revisions = []
        self.blob_revisions = []

    def get_repo(self, owner, name):
        return RepoMetadata(
            id=7,
            name=name,
            full_name=f"{owner}/{name}",
            owner=owner,
            default_branch="main",
            is_private=False,
            language="JavaScript",
        )

    def get_compare(self, owner, name, base, head):
        return PushData(
            repo=f"{owner}/{name}",
            before_commit=base,
            after_commit=head,
            files=[PushFile(path="src/books.js", status="modified", patch="@@ mock @@")],
        )

    def list_files(self, owner, name, tree_sha):
        self.tree_revisions.append(tree_sha)
        source = self.sources[tree_sha]
        items = []
        for path, content in source.items():
            if content is not None:
                size = len(content) if isinstance(content, bytes) else len(content.encode())
                items.append(RepoFileItem(path=path, type="blob", size=size, sha=f"{tree_sha}:{path}"))
        return items

    def get_blob_content(self, owner, name, blob_sha):
        revision, path = blob_sha.split(":", 1)
        self.blob_revisions.append(revision)
        source = self.sources[revision][path]
        return source if isinstance(source, bytes) else source.encode()


class FakeGeminiClient:
    model = "gemini-test"

    def __init__(self, result=None, error=None):
        self.result = result or GeminiResult(
            draft=GeminiDraft(
                one_line="Updates saveBook behavior.",
                what_changed="saveBook now records the book identifier.",
                might_affect=[MightAffect(target="saveBook", reason="It handles the changed write.", link="src/books.js#L1")],
                things_to_know=[],
                flags=["model flag is ignored"],
            ),
            tokens_in=240,
            tokens_out=50,
            seconds=1.25,
        )
        self.error = error
        self.prompts = []

    def generate_explanation(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.result


def _configure_pipeline(monkeypatch, evidence=None):
    evidence = evidence or Evidence(
        changes=[],
        flags=["no tests cover the changed code"],
        token_count=800,
    )
    saved = {}
    monkeypatch.setattr(pipeline, "get_cached_explanation", lambda *args: None)
    monkeypatch.setattr(pipeline, "current_mapping", lambda repository: {
        "id": 12,
        "status": "ready",
        "mapped_commit_sha": BEFORE,
    })
    monkeypatch.setattr(pipeline, "begin_push", lambda *args: (12, 55, None))
    def fake_load_evidence(repo_id, push, changes, before, after, **kwargs):
        evidence_batch = evidence.model_copy(update={"changes": changes}, deep=True)
        if not kwargs.get("use_extra_evidence", True):
            evidence_batch.callers = []
            evidence_batch.tests = []
            evidence_batch.similar_code = []
            evidence_batch.flags = []
        return [evidence_batch], {"src/books.js"}, {"saveBook", "findBook"}

    monkeypatch.setattr(pipeline, "load_evidence", fake_load_evidence)
    monkeypatch.setattr(
        pipeline,
        "save_explanation",
        lambda *args: saved.update(explanation=args[3], evidence=args[4], use_extra=args[5]),
    )
    failed = Mock()
    monkeypatch.setattr(pipeline, "mark_push_failed", failed)
    return saved, failed


def test_explain_push_runs_compare_generation_validation_and_persistence(monkeypatch):
    saved, failed = _configure_pipeline(monkeypatch)
    github = FakeGitHubClient(
        {"src/books.js": "function saveBook(book) { return book; }"},
        {"src/books.js": "function saveBook(book) { return { id: book.id }; }"},
    )
    gemini = FakeGeminiClient()

    result = pipeline.explain_push(
        "owner/repo",
        BEFORE,
        AFTER,
        github_client=github,
        gemini_client=gemini,
    )

    assert result.one_line == "Updates saveBook behavior."
    assert result.flags == ["no tests cover the changed code"]
    assert result.meta.tokens_in == 240
    assert result.meta.tokens_out == 50
    assert result.meta.seconds == 1.25
    assert saved["explanation"] == result
    assert saved["evidence"].changes[0].name == "saveBook"
    assert saved["use_extra"] is True
    assert github.tree_revisions == [BEFORE, AFTER]
    assert set(github.blob_revisions) == {BEFORE, AFTER}
    failed.assert_not_called()


def test_comment_only_push_returns_without_gemini_or_persistence(monkeypatch):
    saved, failed = _configure_pipeline(monkeypatch)
    github = FakeGitHubClient(
        {"src/books.js": "function saveBook(book) { return book; }"},
        {"src/books.js": "function saveBook(book) { // comment changed\n return book; }"},
    )
    gemini = FakeGeminiClient()

    result = pipeline.explain_push(
        "owner/repo", BEFORE, AFTER, github_client=github, gemini_client=gemini
    )

    assert result is None
    assert gemini.prompts == []
    assert saved == {}
    failed.assert_not_called()


def test_binary_file_does_not_block_changed_javascript(monkeypatch):
    saved, _ = _configure_pipeline(monkeypatch)
    github = FakeGitHubClient(
        {"src/books.js": "function saveBook(book) { return book; }", "assets/cover.png": bytes([0]) + b"binary"},
        {"src/books.js": "function saveBook(book) { return book.id; }", "assets/cover.png": bytes([0]) + b"binary-new"},
    )
    original_compare = github.get_compare

    def compare(owner, name, base, head):
        push = original_compare(owner, name, base, head)
        push.files.append(PushFile(path="assets/cover.png", status="modified"))
        return push

    github.get_compare = compare
    result = pipeline.explain_push(
        "owner/repo", BEFORE, AFTER, github_client=github, gemini_client=FakeGeminiClient()
    )

    assert result is not None
    assert saved["explanation"] == result


def test_use_extra_evidence_false_is_forwarded_and_persisted(monkeypatch):
    saved, _ = _configure_pipeline(monkeypatch)
    captured = {}
    original = pipeline.load_evidence

    def load(*args, **kwargs):
        captured.update(kwargs)
        batches, files, symbols = original(*args, **kwargs)
        assert all(not item.callers and not item.tests and not item.similar_code and not item.flags for item in batches)
        return batches, files, symbols

    monkeypatch.setattr(pipeline, "load_evidence", load)
    github = FakeGitHubClient(
        {"src/books.js": "function saveBook(book) { return book; }"},
        {"src/books.js": "function saveBook(book) { return book.id; }"},
    )

    result = pipeline.explain_push(
        "owner/repo",
        BEFORE,
        AFTER,
        use_extra_evidence=False,
        github_client=github,
        gemini_client=FakeGeminiClient(),
    )

    assert result is not None
    assert captured["use_extra_evidence"] is False
    assert saved["use_extra"] is False


def test_gemini_failure_marks_push_failed_and_never_persists(monkeypatch):
    saved, failed = _configure_pipeline(monkeypatch)
    github = FakeGitHubClient(
        {"src/books.js": "function saveBook() { return 1; }"},
        {"src/books.js": "function saveBook() { return 2; }"},
    )

    with pytest.raises(GeminiError):
        pipeline.explain_push(
            "owner/repo",
            BEFORE,
            AFTER,
            github_client=github,
            gemini_client=FakeGeminiClient(error=GeminiError("provider unavailable")),
        )

    failed.assert_called_once_with(55)
    assert saved == {}


def test_cached_explanation_skips_github_and_generation(monkeypatch):
    from backend.app.models import Explanation, ExplanationMeta

    cached = Explanation(one_line="Cached.", what_changed="Already saved.", meta=ExplanationMeta(model="gemini-test"))
    monkeypatch.setattr(pipeline, "get_cached_explanation", lambda *args: cached)
    github = Mock()
    gemini = FakeGeminiClient()

    result = pipeline.explain_push("owner/repo", BEFORE, AFTER, github_client=github, gemini_client=gemini)

    assert result == cached
    github.get_repo.assert_not_called()
    assert gemini.prompts == []