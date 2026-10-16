"""Repository evidence lookup, deterministic risk flags, and prompt budgeting."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Iterable

from pgvector import Vector
from psycopg.rows import dict_row

from backend.app.db import get_db_connection
from backend.app.index.embeddings import embed_texts
from backend.app.models import (
    CallerReference,
    ChangedSymbol,
    Evidence,
    PushData,
    SimilarCode,
    TestLink,
)

DEFAULT_PROMPT_TOKEN_BUDGET = 8000
PROMPT_INSTRUCTION_TOKEN_RESERVE = 700
SIMILAR_CODE_PER_SYMBOL = 3


def estimate_tokens(text: str) -> int:
    """Estimate tokens conservatively using the project plan's 4-char heuristic."""
    return (len(text) + 3) // 4


def load_repository_inventory(repo_id: int) -> tuple[set[str], set[str]]:
    """Load file and symbol names used to validate generated references."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT file, name FROM chunks WHERE repo_id = %s", (repo_id,))
            rows = cursor.fetchall()
    return {row[0] for row in rows}, {row[1] for row in rows if row[1]}


def _changed_chunk_ids(cursor, repo_id: int, changes: Iterable[ChangedSymbol]) -> dict[tuple[str, str], list[int]]:
    changed_ids: dict[tuple[str, str], list[int]] = {}
    for change in changes:
        cursor.execute(
            "SELECT id FROM chunks WHERE repo_id = %s AND file = %s AND name = %s ORDER BY id",
            (repo_id, change.file, change.name),
        )
        ids = [row[0] for row in cursor.fetchall()]
        if ids:
            changed_ids[(change.file, change.name)] = ids
    return changed_ids


def _load_callers_and_tests(cursor, changed_ids: dict[tuple[str, str], list[int]]) -> tuple[list[CallerReference], list[TestLink]]:
    callers: dict[tuple[str, str, str], CallerReference] = {}
    tests: dict[tuple[str, str], TestLink] = {}
    for (_, target_name), chunk_ids in changed_ids.items():
        cursor.execute(
            """SELECT DISTINCT caller.file, caller.name, callee.name
               FROM "references" ref
               JOIN chunks caller ON caller.id = ref.caller_chunk_id
               JOIN chunks callee ON callee.id = ref.callee_chunk_id
               WHERE ref.callee_chunk_id = ANY(%s)""",
            (chunk_ids,),
        )
        for caller_file, caller_name, resolved_target in cursor.fetchall():
            item = CallerReference(
                caller_file=caller_file,
                caller_name=caller_name,
                target_name=resolved_target,
            )
            callers[(caller_file, caller_name, resolved_target)] = item
        cursor.execute(
            """SELECT DISTINCT test_file FROM test_links WHERE chunk_id = ANY(%s)""",
            (chunk_ids,),
        )
        for (test_file,) in cursor.fetchall():
            item = TestLink(test_file=test_file, target_name=target_name)
            tests[(test_file, target_name)] = item
    return list(callers.values()), list(tests.values())


def _load_similar_code(cursor, repo_id: int, changes: Iterable[ChangedSymbol], changed_ids: dict[tuple[str, str], list[int]]) -> list[SimilarCode]:
    results: dict[tuple[str, str], SimilarCode] = {}
    for change in changes:
        query_text = change.diff_snippet or f"{change.name} in {change.file}"
        vector = Vector(embed_texts([query_text])[0])
        excluded_ids = changed_ids.get((change.file, change.name), [])
        cursor.execute(
            """SELECT file, name, 1 - (embedding <=> %s) AS score, LEFT(text, 700) AS snippet
               FROM chunks
               WHERE repo_id = %s AND embedding IS NOT NULL AND NOT (id = ANY(%s))
               ORDER BY embedding <=> %s
               LIMIT %s""",
            (vector, repo_id, excluded_ids, vector, SIMILAR_CODE_PER_SYMBOL),
        )
        for file_path, name, score, snippet in cursor.fetchall():
            results[(file_path, name)] = SimilarCode(
                file=file_path,
                name=name,
                score=max(0.0, min(1.0, float(score))),
                snippet=snippet,
            )
    return list(results.values())


def detect_risk_flags(
    push: PushData,
    before_sources: dict[str, str | None],
    after_sources: dict[str, str | None],
    changes: list[ChangedSymbol],
    test_links: list[TestLink],
) -> list[str]:
    """Compute the Step 4 rule-based risk labels; never trust model-generated flags."""
    paths = {file.path.lower() for file in push.files}
    before_by_lower = {path.lower(): source for path, source in before_sources.items()}
    after_by_lower = {path.lower(): source for path, source in after_sources.items()}
    flags: list[str] = []
    if any(
        path.endswith(".sql")
        or "migration" in path
        or "schema" in path
        or re.search(r"\b(?:select|insert|update|delete|create table|alter table)\b", (after_by_lower.get(path) or "").lower())
        for path in paths
    ):
        flags.append("touches the database")
    if any(
        path.endswith((".env", ".yaml", ".yml", ".toml", ".ini"))
        or "config" in path
        or path.endswith(("/settings.js", "/config.js"))
        for path in paths
    ):
        flags.append("changes configuration")
    if "package.json" in paths:
        before_package = before_by_lower.get("package.json")
        after_package = after_by_lower.get("package.json")
        try:
            old = json.loads(before_package or "{}")
            new = json.loads(after_package or "{}")
            dependency_fields = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
            if any(old.get(field, {}) != new.get(field, {}) for field in dependency_fields):
                flags.append("changes package dependencies")
        except json.JSONDecodeError:
            flags.append("changes package.json")
    route_pattern = re.compile(r"\b(?:app|router|server)\.(?:get|post|put|patch|delete|all|use)\s*\(", re.IGNORECASE)
    if any(route_pattern.search(after_by_lower.get(path, "") or "") for path in paths):
        flags.append("changes an API route")
    if len(push.files) >= 10:
        flags.append("many files changed at once")

    def is_test_path(path: str) -> bool:
        normalized = path.lower().replace("\\", "/")
        filename = PurePosixPath(normalized).name
        test_suffixes = tuple(
            f"{suffix}.{extension}"
            for suffix in (".test", ".spec")
            for extension in ("js", "jsx", "cjs", "mjs")
        )
        return any(part in {"test", "tests", "__tests__"} for part in normalized.split("/")) or filename.endswith(test_suffixes)

    changed_js_names = {
        change.name for change in changes
        if change.file.lower().endswith((".js", ".jsx", ".cjs", ".mjs"))
        and not is_test_path(change.file)
    }
    covered_names = {link.target_name for link in test_links}
    if changed_js_names and not changed_js_names.intersection(covered_names):
        flags.append("no tests cover the changed code")
    return flags


def _evidence_size(evidence: Evidence) -> int:
    data = evidence.model_dump(mode="json", exclude={"token_count"})
    return estimate_tokens(json.dumps(data, ensure_ascii=True, separators=(",", ":")))


def _with_token_count(evidence: Evidence) -> Evidence:
    evidence.token_count = PROMPT_INSTRUCTION_TOKEN_RESERVE + _evidence_size(evidence)
    return evidence


def select_evidence_batches(
    changes: list[ChangedSymbol],
    callers: list[CallerReference],
    tests: list[TestLink],
    similar_code: list[SimilarCode],
    flags: list[str],
    *,
    use_extra_evidence: bool,
    token_budget: int = DEFAULT_PROMPT_TOKEN_BUDGET,
) -> list[Evidence]:
    """Group by file and fit evidence in fixed priority order under the input budget."""
    if token_budget <= PROMPT_INSTRUCTION_TOKEN_RESERVE:
        raise ValueError("token_budget must exceed the reserved instruction budget")
    grouped: dict[str, list[ChangedSymbol]] = {}
    for change in changes:
        grouped.setdefault(change.file, []).append(change)
    if not grouped:
        return []

    batches: list[Evidence] = []
    evidence_budget = token_budget
    for file_path in sorted(grouped):
        file_changes = grouped[file_path]
        change_batches: list[list[ChangedSymbol]] = []
        current: list[ChangedSymbol] = []
        for change in file_changes:
            candidate = Evidence(changes=[*current, change])
            if current and PROMPT_INSTRUCTION_TOKEN_RESERVE + _evidence_size(candidate) > evidence_budget:
                change_batches.append(current)
                current = [change]
            else:
                current.append(change)
        if current:
            change_batches.append(current)

        for batch_changes in change_batches:
            changed_names = {change.name for change in batch_changes}
            selected = Evidence(changes=list(batch_changes))
            if use_extra_evidence:
                priority_items = [
                    ("callers", item) for item in callers if item.target_name in changed_names
                ]
                priority_items.extend(("tests", item) for item in tests if item.target_name in changed_names)
                priority_items.extend(("similar_code", item) for item in similar_code)
                priority_items.extend(("flags", flag) for flag in flags)
                for field_name, item in priority_items:
                    current_values = getattr(selected, field_name)
                    if item in current_values:
                        continue
                    candidate = selected.model_copy(deep=True)
                    getattr(candidate, field_name).append(item)
                    if PROMPT_INSTRUCTION_TOKEN_RESERVE + _evidence_size(candidate) <= evidence_budget:
                        selected = candidate

            if PROMPT_INSTRUCTION_TOKEN_RESERVE + _evidence_size(selected) > evidence_budget:
                overflow = PROMPT_INSTRUCTION_TOKEN_RESERVE + _evidence_size(selected) - evidence_budget
                first_change = selected.changes[0]
                keep_chars = max(0, len(first_change.diff_snippet) - overflow * 4)
                selected.changes[0] = first_change.model_copy(update={"diff_snippet": first_change.diff_snippet[:keep_chars]})
            batches.append(_with_token_count(selected))
    return batches


def load_evidence(
    repo_id: int,
    push: PushData,
    changes: list[ChangedSymbol],
    before_sources: dict[str, str | None],
    after_sources: dict[str, str | None],
    *,
    use_extra_evidence: bool,
    token_budget: int = DEFAULT_PROMPT_TOKEN_BUDGET,
) -> tuple[list[Evidence], set[str], set[str]]:
    """Load changed symbols plus mapped callers, tests, vectors, and valid names."""
    valid_files, valid_symbols = load_repository_inventory(repo_id)
    valid_files.update(change.file for change in changes)
    valid_symbols.update(change.name for change in changes)
    if not use_extra_evidence:
        batches = select_evidence_batches(
            changes, [], [], [], [], use_extra_evidence=False, token_budget=token_budget
        )
        return batches, valid_files, valid_symbols

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            changed_ids = _changed_chunk_ids(cursor, repo_id, changes)
            callers, tests = _load_callers_and_tests(cursor, changed_ids)
            similar = _load_similar_code(cursor, repo_id, changes, changed_ids)
    flags = detect_risk_flags(push, before_sources, after_sources, changes, tests)
    batches = select_evidence_batches(
        changes,
        callers,
        tests,
        similar,
        flags,
        use_extra_evidence=True,
        token_budget=token_budget,
    )
    valid_files.update(item.file for item in similar)
    valid_symbols.update(item.name for item in similar)
    valid_files.update(item.caller_file for item in callers)
    valid_files.update(item.test_file for item in tests)
    valid_symbols.update(item.caller_name for item in callers)
    valid_symbols.update(item.target_name for item in callers)
    valid_symbols.update(item.target_name for item in tests)
    return batches, valid_files, valid_symbols