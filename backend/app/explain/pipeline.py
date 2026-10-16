"""Step 4 push explanation orchestration."""

from __future__ import annotations

from collections.abc import Sequence, Iterable
import json
import logging
from pathlib import PurePosixPath
import re
from time import perf_counter
from typing import Any

from backend.app.github.client import GitHubClient
from backend.app.github.exceptions import GitHubError
from backend.app.index.mapping import parse_repository_identifier
from backend.app.config import get_settings
from backend.app.models import (
    Evidence,
    Explanation,
    ExplanationMeta,
    MightAffect,
    PushData,
    ThingToKnow,
)
from backend.app.explain.changes import JAVASCRIPT_EXTENSIONS, find_changed_symbols
from backend.app.explain.evidence import (
    DEFAULT_PROMPT_TOKEN_BUDGET,
    estimate_tokens,
    load_evidence,
)
from backend.app.explain.gemini import GeminiClient, GeminiDraft, GeminiError
from backend.app.explain.persistence import (
    MappingRevisionMismatchError,
    RepositoryNotMappedError,
    begin_push,
    current_mapping,
    get_cached_explanation,
    mark_push_failed,
    save_explanation,
)
from backend.app.parsing.files import DEFAULT_MAX_FILE_BYTES

logger = logging.getLogger(__name__)

ALLOWED_CODE_WORDS = {
    "API", "CRUD", "CSS", "DELETE", "Express", "Gemini", "GET", "GitHub", "HTTP", "HTTPS", "JSON", "JavaScript",
    "Node", "Node.js", "PATCH", "POST", "PostgreSQL", "PUT", "REST", "SQL", "URL", "UUID", "pgvector", "pytest",
}
_FILE_REFERENCE = re.compile(
    r"(?<![\w./-])(?:[\w@.-]+/)*[\w@.-]+\.(?:js|jsx|cjs|mjs|ts|tsx|json|sql|md|ya?ml|toml|ini|env)(?:#L\d+(?:-L\d+)?)?",
    re.IGNORECASE,
)
_BACKTICK = re.compile(r"`([^`]+)`")
_CAMEL_OR_CHAIN = re.compile(
    r"\b(?:[a-z_$][A-Za-z0-9_$]*[A-Z][A-Za-z0-9_$]*|"
    r"[A-Z][a-z0-9_$]+[A-Z][A-Za-z0-9_$]*|[\w$]+\.[\w$]+)\b"
)


class ChangeAnalysisError(RuntimeError):
    """The requested commit comparison cannot be analyzed safely."""


def _load_changed_file_sources(
    client: GitHubClient,
    push: PushData,
 ) -> tuple[dict[str, str | None], dict[str, str | None]]:
    owner, repo_name = parse_repository_identifier(push.repo)
    paths = {changed.path for changed in push.files}
    if not paths:
        return {}, {}

    trees: dict[str, dict[str, Any]] = {}
    for revision in (push.before_commit, push.after_commit):
        items = client.list_files(owner, repo_name, tree_sha=revision)
        trees[revision] = {item.path: item for item in items if item.type == "blob" and item.path in paths}

    sources: dict[str, dict[str, str | None]] = {push.before_commit: {}, push.after_commit: {}}
    for revision in (push.before_commit, push.after_commit):
        for path in paths:
            item = trees[revision].get(path)
            if item is None:
                sources[revision][path] = None
                continue
            if item.size is not None and item.size > DEFAULT_MAX_FILE_BYTES:
                raise ChangeAnalysisError(f"Changed file '{path}' exceeds the {DEFAULT_MAX_FILE_BYTES}-byte analysis limit.")
            try:
                source_bytes = client.get_blob_content(owner, repo_name, item.sha)
            except GitHubError as exc:
                if exc.status_code == 404:
                    sources[revision][path] = None
                    continue
                raise
            if b"\x00" in source_bytes:
                sources[revision][path] = None
                continue
            sources[revision][path] = source_bytes.decode("utf-8", errors="replace")
    return sources[push.before_commit], sources[push.after_commit]


def _prompt(repository: str, before: str, after: str, evidence: Evidence) -> str:
    payload = evidence.model_dump(mode="json")
    return (
        "Explain this public JavaScript repository push for a beginner. "
        "Use only facts and identifiers in the supplied evidence. Do not invent files, functions, behavior, "
        "or test coverage. Return only the requested JSON object. Keep the explanation concise and say "
        "'likely' for static impact.\n"
        f"Repository: {repository}\nBefore commit: {before}\nAfter commit: {after}\n"
        "Evidence JSON:\n"
        f"{json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}"
    )


def _sanitize_text(value: str, valid_files: set[str], valid_symbols: set[str]) -> tuple[str, int]:
    removed = 0

    def replace_file(match: re.Match[str]) -> str:
        nonlocal removed
        reference = match.group(0)
        path = reference.split("#", 1)[0]
        if path not in valid_files:
            removed += 1
            return "the changed file"
        return reference

    cleaned = _FILE_REFERENCE.sub(replace_file, value)

    def replace_backtick(match: re.Match[str]) -> str:
        nonlocal removed
        token = match.group(1).strip()
        path = token.split("#", 1)[0]
        if path in valid_files or token in valid_symbols or token in ALLOWED_CODE_WORDS:
            return f"`{token}`"
        if re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", token):
            if token.split(".")[-1] not in valid_symbols:
                removed += 1
                return "the related code"
        return match.group(0)

    cleaned = _BACKTICK.sub(replace_backtick, cleaned)

    def replace_identifier(match: re.Match[str]) -> str:
        nonlocal removed
        token = match.group(0)
        if token not in valid_symbols and token not in ALLOWED_CODE_WORDS:
            removed += 1
            return "related code"
        return token

    cleaned = _CAMEL_OR_CHAIN.sub(replace_identifier, cleaned)
    return cleaned, removed


def _sanitize_draft(
    draft: GeminiDraft,
    valid_files: set[str],
    valid_symbols: set[str],
    trusted_flags: list[str],
) -> tuple[GeminiDraft, int]:
    removed = 0

    def clean(value: str) -> str:
        nonlocal removed
        cleaned, count = _sanitize_text(value, valid_files, valid_symbols)
        removed += count
        return cleaned

    affects: list[MightAffect] = []
    for item in draft.might_affect:
        path = item.link.split("#", 1)[0]
        target = item.target.strip()
        if path not in valid_files:
            removed += 1
            continue
        if target not in valid_symbols and target not in valid_files and target.split(".")[-1] not in valid_symbols:
            removed += 1
            continue
        affects.append(MightAffect(target=clean(target), reason=clean(item.reason), link=item.link))

    terms = [ThingToKnow(term=clean(item.term), meaning=clean(item.meaning)) for item in draft.things_to_know]
    cleaned_draft = GeminiDraft(
        one_line=clean(draft.one_line),
        what_changed=clean(draft.what_changed),
        might_affect=affects,
        things_to_know=terms,
        flags=trusted_flags,
    )
    return cleaned_draft, removed


def _combine_drafts(drafts: list[GeminiDraft], repository: str) -> GeminiDraft:
    if len(drafts) == 1:
        return drafts[0]
    summaries = [draft.one_line.strip().rstrip(".") for draft in drafts]
    one_line = f"This push updates {len(drafts)} file sections in {repository}: " + "; ".join(summaries) + "."
    what_changed = "\n\n".join(draft.what_changed for draft in drafts)
    affects = {(item.target, item.reason, item.link): item for draft in drafts for item in draft.might_affect}
    terms = {(item.term, item.meaning): item for draft in drafts for item in draft.things_to_know}
    flags = list(dict.fromkeys(flag for draft in drafts for flag in draft.flags))
    return GeminiDraft(
        one_line=one_line,
        what_changed=what_changed,
        might_affect=list(affects.values()),
        things_to_know=list(terms.values()),
        flags=flags,
    )


def explain_push(
    repo: str,
    before: str,
    after: str,
    use_extra_evidence: bool = True,
    *,
    github_client: GitHubClient | None = None,
    gemini_client: GeminiClient | None = None,
    token_budget: int = DEFAULT_PROMPT_TOKEN_BUDGET,
) -> Explanation | None:
    """Explain one commit push, persist its evidence, and ignore non-meaningful edits."""
    owner, repo_name = parse_repository_identifier(repo)
    repository = f"{owner}/{repo_name}"
    cached = get_cached_explanation(repository, before, after)
    if cached is not None:
        return cached

    github = github_client or GitHubClient()
    owns_github = github_client is None
    gemini = gemini_client
    owns_gemini = False
    push_id: int | None = None
    try:
        metadata = github.get_repo(owner, repo_name)
        push = github.get_compare(owner, repo_name, before, after)
        before_sources, after_sources = _load_changed_file_sources(github, push)
        changes = find_changed_symbols(before_sources, after_sources)
        if not changes:
            return None

        mapping = current_mapping(metadata.full_name)
        if mapping is None or mapping["status"] != "ready":
            raise RepositoryNotMappedError(f"Repository '{metadata.full_name}' does not have a ready mapping.")
        if mapping["mapped_commit_sha"] != before:
            raise MappingRevisionMismatchError(
                "Repository mapping must match the push's before commit before explanation can run."
            )
        repo_id, push_id, already_saved = begin_push(metadata.full_name, before, after)
        if already_saved is not None:
            return already_saved

        batches, valid_files, valid_symbols = load_evidence(
            repo_id,
            push,
            changes,
            before_sources,
            after_sources,
            use_extra_evidence=use_extra_evidence,
            token_budget=token_budget,
        )
        if not batches:
            return None

        if gemini is None:
            gemini = GeminiClient()
            owns_gemini = True
        generated: list[GeminiDraft] = []
        aggregate_evidence = Evidence()
        total_input_tokens = 0
        total_output_tokens = 0
        total_generation_seconds = 0.0
        for evidence in batches:
            prompt = _prompt(metadata.full_name, before, after, evidence)
            prompt_tokens = estimate_tokens(prompt)
            if prompt_tokens > token_budget:
                raise ChangeAnalysisError("Prepared prompt exceeded the configured token budget.")
            evidence.token_count = prompt_tokens
            result = gemini.generate_explanation(prompt)
            generated.append(result.draft)
            total_input_tokens += result.tokens_in
            total_output_tokens += result.tokens_out
            total_generation_seconds += result.seconds
            aggregate_evidence.changes.extend(evidence.changes)
            aggregate_evidence.callers.extend(evidence.callers)
            aggregate_evidence.tests.extend(evidence.tests)
            aggregate_evidence.similar_code.extend(evidence.similar_code)
            aggregate_evidence.flags.extend(evidence.flags)
            aggregate_evidence.token_count += evidence.token_count

        trusted_flags = list(dict.fromkeys(aggregate_evidence.flags))
        combined = _combine_drafts(generated, metadata.full_name)
        safe_draft, removed_names = _sanitize_draft(combined, valid_files, valid_symbols, trusted_flags)
        model = getattr(gemini, "model", get_settings().gemini_model)
        explanation = Explanation(
            one_line=safe_draft.one_line,
            what_changed=safe_draft.what_changed,
            might_affect=safe_draft.might_affect,
            things_to_know=safe_draft.things_to_know,
            flags=trusted_flags,
            meta=ExplanationMeta(
                model=model,
                tokens_in=total_input_tokens,
                tokens_out=total_output_tokens,
                seconds=total_generation_seconds,
            ),
        )
        logger.info(
            "Explanation validation removed %s unsupported names for %s commits %s..%s",
            removed_names,
            metadata.full_name,
            before[:12],
            after[:12],
        )
        save_explanation(repo_id, push_id, after, explanation, aggregate_evidence, use_extra_evidence)
        return explanation
    except Exception as exc:
        if push_id is not None:
            try:
                mark_push_failed(push_id)
            except Exception:
                logger.exception("Could not persist failed explanation state for %s", repository)
        logger.error(
            "Push explanation failed repository=%s before=%s after=%s error=%s",
            repository,
            before[:12],
            after[:12],
            type(exc).__name__,
        )
        raise
    finally:
        if owns_gemini and gemini is not None:
            gemini.close()
        if owns_github:
            github.close()