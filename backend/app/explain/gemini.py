"""Structured Gemini Flash generation with safe retries and prompt caching."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import threading
from time import perf_counter, sleep
from typing import Any, Callable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.config import get_settings
from backend.app.models import MightAffect, ThingToKnow

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
MAX_RETRY_DELAY_SECONDS = 30.0
MAX_CACHE_ENTRIES = 256
_response_cache: dict[str, "GeminiResult"] = {}
_cache_lock = threading.Lock()

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "one_line": {"type": "STRING"},
        "what_changed": {"type": "STRING"},
        "might_affect": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "target": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                    "link": {"type": "STRING"},
                },
                "required": ["target", "reason", "link"],
            },
        },
        "things_to_know": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"term": {"type": "STRING"}, "meaning": {"type": "STRING"}},
                "required": ["term", "meaning"],
            },
        },
        "flags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["one_line", "what_changed", "might_affect", "things_to_know", "flags"],
}


class GeminiError(RuntimeError):
    """Base error for Gemini configuration, transport, and response failures."""


class GeminiConfigurationError(GeminiError):
    """Gemini cannot be called because its required configuration is missing."""


class GeminiResponseError(GeminiError):
    """Gemini returned content that violates the structured response contract."""


class GeminiDraft(BaseModel):
    """Structured model output before trusted local metrics are attached."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    one_line: str
    what_changed: str
    might_affect: list[MightAffect]
    things_to_know: list[ThingToKnow]
    flags: list[str]


@dataclass(frozen=True)
class GeminiResult:
    draft: GeminiDraft
    tokens_in: int
    tokens_out: int
    seconds: float
    cache_hit: bool = False


def clear_response_cache() -> None:
    """Clear the process-local response cache, primarily for isolated tests."""
    with _cache_lock:
        _response_cache.clear()


def _approximate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _retry_delay(response: httpx.Response | None, attempt: int, base_delay: float) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(MAX_RETRY_DELAY_SECONDS, max(0.0, float(retry_after)))
            except ValueError:
                pass
    return min(MAX_RETRY_DELAY_SECONDS, base_delay * (2 ** attempt))


class GeminiClient:
    """Gemini REST wrapper; credentials travel only in a request header."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
        max_attempts: int = MAX_ATTEMPTS,
        base_delay: float = 1.0,
        sleep_func: Callable[[float], None] = sleep,
    ) -> None:
        settings = get_settings()
        configured_key = settings.gemini_api_key if api_key is None else api_key
        self._api_key = (configured_key or "").strip()
        self.model = model or settings.gemini_model
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.sleep_func = sleep_func
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GeminiClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def generate_explanation(self, prompt: str) -> GeminiResult:
        """Generate validated JSON, retry transient failures, and cache by prompt."""
        if not self._api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")
        if not prompt.strip():
            raise ValueError("Gemini prompt cannot be empty.")

        cache_key = hashlib.sha256(f"{self.model}\0{prompt}".encode("utf-8")).hexdigest()
        with _cache_lock:
            cached = _response_cache.get(cache_key)
        if cached is not None:
            logger.info("Gemini prompt cache hit for model %s", self.model)
            return GeminiResult(
                draft=cached.draft,
                tokens_in=0,
                tokens_out=0,
                seconds=0.0,
                cache_hit=True,
            )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _RESPONSE_SCHEMA,
                "temperature": 0.2,
                "maxOutputTokens": 900,
            },
        }
        started = perf_counter()
        last_status: int | None = None
        for attempt in range(self.max_attempts):
            response: httpx.Response | None = None
            try:
                response = self._http.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self._api_key,
                    },
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt + 1 >= self.max_attempts:
                    logger.error("Gemini transport failed after %s attempts", self.max_attempts)
                    raise GeminiError("Gemini request failed after retries.") from exc
                delay = _retry_delay(None, attempt, self.base_delay)
                logger.warning("Gemini transport failure; retry %s/%s in %.1fs", attempt + 1, self.max_attempts - 1, delay)
                self.sleep_func(delay)
                continue

            if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                last_status = response.status_code
                if attempt + 1 >= self.max_attempts:
                    break
                delay = _retry_delay(response, attempt, self.base_delay)
                logger.warning(
                    "Gemini returned HTTP %s; retry %s/%s in %.1fs",
                    response.status_code,
                    attempt + 1,
                    self.max_attempts - 1,
                    delay,
                )
                self.sleep_func(delay)
                continue
            if not response.is_success:
                logger.error("Gemini rejected request with HTTP %s", response.status_code)
                raise GeminiError(f"Gemini request was rejected with HTTP {response.status_code}.")

            try:
                data = response.json()
                parts = data["candidates"][0]["content"]["parts"]
                response_text = "".join(part.get("text", "") for part in parts)
                draft = GeminiDraft.model_validate_json(response_text)
            except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
                raise GeminiResponseError("Gemini returned invalid structured explanation JSON.") from exc

            usage = data.get("usageMetadata") or {}
            tokens_in = int(usage.get("promptTokenCount", _approximate_tokens(prompt)))
            tokens_out = int(usage.get("candidatesTokenCount", _approximate_tokens(response_text)))
            result = GeminiResult(
                draft=draft,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                seconds=max(0.0, perf_counter() - started),
            )
            with _cache_lock:
                if len(_response_cache) >= MAX_CACHE_ENTRIES:
                    _response_cache.pop(next(iter(_response_cache)))
                _response_cache[cache_key] = result
            logger.info(
                "Gemini generation complete model=%s tokens_in=%s tokens_out=%s seconds=%.3f",
                self.model,
                tokens_in,
                tokens_out,
                result.seconds,
            )
            return result

        logger.error("Gemini transient HTTP failure exhausted retries status=%s", last_status)
        raise GeminiError(f"Gemini request failed after retries (last HTTP status {last_status}).")