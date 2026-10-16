"""Offline tests for the structured Gemini wrapper."""

import json
import logging

import httpx
import pytest

from backend.app.explain.gemini import (
    GeminiClient,
    GeminiConfigurationError,
    GeminiError,
    GeminiResponseError,
    clear_response_cache,
)


def _success_response() -> dict:
    return {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({
                "one_line": "Updates book lookups.",
                "what_changed": "The lookup now checks the requested identifier.",
                "might_affect": [{"target": "findBook", "reason": "It performs the changed lookup.", "link": "src/books.js#L4"}],
                "things_to_know": [{"term": "identifier", "meaning": "A value used to locate a record."}],
                "flags": [],
            })}]},
        }],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 45},
    }


def test_gemini_sends_key_only_in_header_and_validates_structured_output(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-key-do-not-log"
        assert "test-key-do-not-log" not in str(request.url)
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseSchema"]["type"] == "OBJECT"
        return httpx.Response(200, json=_success_response())

    clear_response_cache()
    with GeminiClient(api_key="test-key-do-not-log", transport=httpx.MockTransport(handler)) as client:
        result = client.generate_explanation("Explain this function.")

    assert result.draft.one_line == "Updates book lookups."
    assert result.tokens_in == 120
    assert result.tokens_out == 45
    assert result.seconds >= 0
    assert "test-key-do-not-log" not in caplog.text


def test_gemini_retries_rate_limits_with_exponential_backoff():
    attempts = 0
    delays = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "busy"})
        return httpx.Response(200, json=_success_response())

    clear_response_cache()
    with GeminiClient(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        sleep_func=delays.append,
    ) as client:
        result = client.generate_explanation("Retry this explanation.")

    assert attempts == 2
    assert delays == [2.0]
    assert result.draft.one_line == "Updates book lookups."


def test_gemini_prompt_cache_avoids_duplicate_requests():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success_response())

    clear_response_cache()
    with GeminiClient(api_key="test-key", transport=httpx.MockTransport(handler)) as client:
        first = client.generate_explanation("Cached explanation.")
        second = client.generate_explanation("Cached explanation.")

    assert calls == 1
    assert second.cache_hit is True
    assert second.tokens_in == second.tokens_out == 0
    assert second.draft == first.draft


def test_gemini_rejects_missing_key_and_invalid_response_without_leaking_key():
    calls = []

    def unexpected_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with GeminiClient(api_key="", transport=httpx.MockTransport(unexpected_handler)) as client:
        with pytest.raises(GeminiConfigurationError, match="GEMINI_API_KEY"):
            client.generate_explanation("Do not call without configuration.")
    assert calls == []

    def invalid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not-json"}]}}]})

    clear_response_cache()
    with GeminiClient(api_key="private-test-key", transport=httpx.MockTransport(invalid_handler)) as client:
        with pytest.raises(GeminiResponseError):
            client.generate_explanation("Reject malformed output.")


def test_gemini_stops_after_retry_limit_without_response_body(caplog):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "private response detail"}})

    with GeminiClient(
        api_key="secret-test-key",
        transport=httpx.MockTransport(handler),
        max_attempts=2,
        sleep_func=lambda delay: None,
    ) as client:
        with pytest.raises(GeminiError, match="after retries"):
            client.generate_explanation("Transient failure.")

    assert calls == 2
    assert "secret-test-key" not in caplog.text
    assert "private response detail" not in caplog.text