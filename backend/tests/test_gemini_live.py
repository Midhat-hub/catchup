"""Opt-in live Gemini check; ordinary tests never make paid/quota API calls."""

import os

import pytest

from backend.app.config import get_settings
from backend.app.explain.gemini import GeminiClient

pytestmark = pytest.mark.gemini


def test_live_gemini_structured_generation():
    if os.environ.get("RUN_LIVE_GEMINI_TEST") != "1":
        pytest.skip("Set RUN_LIVE_GEMINI_TEST=1 to opt into one live Gemini request.")
    if not get_settings().gemini_api_key:
        pytest.skip("GEMINI_API_KEY is not configured.")

    with GeminiClient() as client:
        result = client.generate_explanation(
            "Explain this verified fact to a beginner: a JavaScript function can return a value. "
            "Use one short sentence and no repository-specific names."
        )

    assert result.draft.one_line
    assert result.tokens_in > 0
    assert result.tokens_out > 0