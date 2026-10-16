"""Tests that model references are checked against mapped repository evidence."""

from backend.app.explain.gemini import GeminiDraft
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

    assert removed >= 4
    assert "inventedThing" not in sanitized.one_line
    assert "ghostFunction" not in sanitized.what_changed
    assert "src/secret.js" not in sanitized.what_changed
    assert len(sanitized.might_affect) == 1
    assert sanitized.might_affect[0].target == "saveBook"
    assert sanitized.flags == ["locally computed flag"]