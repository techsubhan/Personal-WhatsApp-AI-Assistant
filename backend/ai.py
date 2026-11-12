"""
ai.py
=====
OpenRouter AI client.

Responsibilities
-----------------
- Send chat-completion requests to OpenRouter's OpenAI-compatible API.
- Use the primary model (OpenAI OSS 120B by default) for every request.
- Retry transient failures (timeouts, 5xx, connection errors) with
  exponential backoff, up to OPENROUTER_MAX_RETRIES attempts.
- Handle HTTP 429 (rate limited) with a longer backoff informed by the
  `Retry-After` header when present.
- Automatically fall back to a secondary model if the primary model
  exhausts its retries or returns a non-recoverable error.
- Never raise raw HTTP/JSON exceptions to callers - everything is wrapped
  in `AIGenerationError` so calling code (prompt_builder.py / routes.py)
  can handle failures uniformly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from config import settings
from logger import get_logger, log_ai_error

logger = get_logger("ai")


class AIGenerationError(Exception):
    """Raised when both the primary and fallback models fail to respond."""


@dataclass
class AIResponse:
    """Result of a successful AI generation call."""

    text: str
    model_used: str
    used_fallback: bool


class OpenRouterClient:
    """Client for OpenRouter's OpenAI-compatible chat completions endpoint."""

    def __init__(self) -> None:
        self._base_url = settings.openrouter_base_url.rstrip("/")
        self._api_key = settings.openrouter_api_key
        self._timeout = settings.openrouter_timeout_seconds
        self._max_retries = max(1, settings.openrouter_max_retries)
        self._temperature = settings.openrouter_temperature
        self._max_tokens = settings.openrouter_max_tokens
        self._primary_model = settings.openrouter_primary_model
        self._fallback_model = settings.openrouter_fallback_model

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if settings.openrouter_site_url:
            headers["HTTP-Referer"] = settings.openrouter_site_url
        if settings.openrouter_site_name:
            headers["X-Title"] = settings.openrouter_site_name
        return headers

    def _call_model(self, model: str, messages: List[Dict[str, str]]) -> str:
        """
        Attempt a single model with retry/backoff. Raises `AIGenerationError`
        if all retry attempts for this specific model are exhausted.
        """
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }

        last_error: Optional[str] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                response = requests.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self._timeout,
                )
            except requests.Timeout:
                last_error = f"timeout after {self._timeout}s"
                logger.warning(
                    "OpenRouter timeout for model=%s attempt=%d/%d",
                    model, attempt, self._max_retries,
                )
                self._backoff(attempt)
                continue
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                logger.warning(
                    "OpenRouter network error for model=%s attempt=%d/%d: %s",
                    model, attempt, self._max_retries, exc,
                )
                self._backoff(attempt)
                continue

            if response.status_code == 429:
                last_error = "rate limited (429)"
                retry_after = response.headers.get("Retry-After")
                wait_seconds = float(retry_after) if retry_after else self._backoff_seconds(attempt)
                logger.warning(
                    "OpenRouter rate limit for model=%s attempt=%d/%d; waiting %.1fs",
                    model, attempt, self._max_retries, wait_seconds,
                )
                time.sleep(wait_seconds)
                continue

            if response.status_code >= 500:
                last_error = f"server error {response.status_code}"
                logger.warning(
                    "OpenRouter server error %s for model=%s attempt=%d/%d",
                    response.status_code, model, attempt, self._max_retries,
                )
                self._backoff(attempt)
                continue

            if response.status_code >= 400:
                # Client errors (bad request, auth failure, invalid model,
                # etc.) will not be fixed by retrying.
                last_error = f"client error {response.status_code}: {response.text}"
                logger.error(
                    "OpenRouter client error %s for model=%s: %s",
                    response.status_code, model, response.text,
                )
                raise AIGenerationError(last_error)

            try:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Empty content in AI response")
                return content
            except (KeyError, IndexError, ValueError) as exc:
                last_error = f"malformed response: {exc}"
                logger.error("OpenRouter malformed response for model=%s: %s", model, exc)
                self._backoff(attempt)
                continue

        raise AIGenerationError(last_error or f"Exhausted retries for model {model}")

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        # Exponential backoff: 1s, 2s, 4s, 8s, capped at 15s.
        return min(15.0, float(2 ** (attempt - 1)))

    def _backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_seconds(attempt))

    def generate_reply(self, messages: List[Dict[str, str]]) -> AIResponse:
        """
        Generate a reply using the primary model, automatically falling
        back to the configured fallback model if the primary model fails
        entirely (all retries exhausted or a non-recoverable error).
        """
        try:
            text = self._call_model(self._primary_model, messages)
            return AIResponse(text=text, model_used=self._primary_model, used_fallback=False)
        except AIGenerationError as primary_error:
            log_ai_error(self._primary_model, str(primary_error))
            logger.warning(
                "Primary model '%s' failed (%s); switching to fallback model '%s'.",
                self._primary_model, primary_error, self._fallback_model,
            )

        try:
            text = self._call_model(self._fallback_model, messages)
            return AIResponse(text=text, model_used=self._fallback_model, used_fallback=True)
        except AIGenerationError as fallback_error:
            log_ai_error(self._fallback_model, str(fallback_error))
            raise AIGenerationError(
                f"Both primary ({self._primary_model}) and fallback "
                f"({self._fallback_model}) models failed: {fallback_error}"
            ) from fallback_error


# Singleton client instance used throughout the application.
openrouter_client = OpenRouterClient()
