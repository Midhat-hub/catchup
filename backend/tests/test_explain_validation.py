"""Tests that model references are checked against mapped repository evidence."""

from backend.app.explain.gemini import GeminiDraft
from backend.app.explain.pipeline import _sanitize_draft, _sanitize_text
from backend.app.explain.pipeline import _sanitize_draft
from backend.app.models import MightAffect


def test_sanitizer_removes_unverified_names_and_links_but_keeps_real_symbols():
    draft = GeminiDraft(
        one_line="Updates saveBook and inventedThing.",
        what_changed="`saveBook` now handles the request; `ghostFunction` is also changed in src/secret.js.",
        might_affect=[
            MightAffect(target="saveBook", reason="It handles the updated record.", link="src/books.js#L2"),
            MightAffect(target="inventedService", reason="A made-up target.", link="src/nope.js#L1"),
        ],
        things_to_know=[],
        flags=["model-suggested flag must not be trusted"],
    )

    sanitized, removed = _sanitize_draft(
        draft,
        {"src/books.js"},
        {"saveBook"},
        ["locally computed flag"],
    )

    assert removed == 3
    assert "inventedThing" in sanitized.one_line
    assert "ghostFunction" not in sanitized.what_changed
    assert "src/secret.js" not in sanitized.what_changed
    assert len(sanitized.might_affect) == 1
    assert sanitized.might_affect[0].target == "saveBook"
    assert sanitized.flags == ["locally computed flag"]


def test_evidenced_names_and_normal_prose_remain_readable():
    draft = GeminiDraft(
        one_line="validateBookInput and createBook now call updateBook with Number.isFinite.",
        what_changed="The validated input reaches the existing updateBook path; unrelated explanatory prose remains intact.",
        might_affect=[
            MightAffect(
                target="Number.isFinite",
                reason="This built-in number check is used by the changed validation.",
                link="src/services/bookService.js#L8",
            )
        ],
        things_to_know=[],
        flags=[],
    )

    sanitized, _ = _sanitize_draft(
        draft,
        {"src/services/bookService.js"},
        {"validateBookInput", "createBook", "updateBook", "Number.isFinite"},
        [],
    )

    assert "validateBookInput" in sanitized.one_line
    assert "createBook" in sanitized.one_line
    assert "updateBook" in sanitized.one_line
    assert "Number.isFinite" in sanitized.one_line
    assert "unrelated explanatory prose remains intact" in sanitized.what_changed
    assert sanitized.might_affect[0].target == "Number.isFinite"


def test_unknown_plain_prose_identifier_is_not_rewritten_but_unknown_backtick_is():
    text = "The parser explains inventedBehavior clearly; `inventedBehavior` is not an evidenced code symbol."

    sanitized, removed = _sanitize_text(text, {"src/books.js"}, {"saveBook"})

    assert "The parser explains inventedBehavior clearly" in sanitized
    assert "related code(related code)" not in sanitized
    assert "`inventedBehavior`" not in sanitized
    assert removed == 1


def test_unsupported_paths_are_never_returned_as_paths():
    text = "See src/services/related code.js for details."

    sanitized, removed = _sanitize_text(text, {"src/services/bookService.js"}, {"saveBook"})

    assert "src/services/related code.js" not in sanitized
    assert "related code.js" not in sanitized
    assert removed >= 1