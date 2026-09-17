"""
Google Gemini provider — the default LLM backend for this system.

Handles structured JSON output via Gemini's native response_mime_type,
with retry/backoff for transient failures (429, 5xx).
"""
from __future__ import annotations

import json
import time
from typing import TypeVar

import google.generativeai as genai
import structlog
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMProvider

log = structlog.get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

# Transient HTTP codes worth retrying
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GeminiProvider(LLMProvider):
    """Wraps the google-generativeai SDK with retry logic and structured output."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        max_retries: int = 2,
        initial_backoff: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_backoff: float = 8.0,
    ):
        genai.configure(api_key=api_key)
        self._model_name = model
        self._max_retries = max_retries
        self._initial_backoff = initial_backoff
        self._backoff_multiplier = backoff_multiplier
        self._max_backoff = max_backoff

    # -- public API ----------------------------------------------------------

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> T | str:
        raw = self._call_with_retry(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=response_schema is not None,
        )

        if response_schema is None:
            return raw

        return self._parse_structured(raw, response_schema)

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        return self._call_with_retry(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    # -- internals -----------------------------------------------------------

    def _call_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Fire the API call, retrying transient errors with exponential backoff."""
        gen_config = genai.types.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if json_mode:
            gen_config.response_mime_type = "application/json"

        model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system_prompt,
            generation_config=gen_config,
        )

        backoff = self._initial_backoff
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 2):  # initial + retries
            try:
                resp = model.generate_content(user_prompt)

                if resp.prompt_feedback and resp.prompt_feedback.block_reason:
                    raise RuntimeError(
                        f"Prompt blocked: {resp.prompt_feedback.block_reason}"
                    )

                text = resp.text
                if not text:
                    raise RuntimeError("Empty response from Gemini")
                return text

            except Exception as exc:
                last_exc = exc
                retryable = self._is_retryable(exc)
                log.warning(
                    "gemini_call_failed",
                    attempt=attempt,
                    retryable=retryable,
                    error=str(exc)[:200],
                )
                if not retryable or attempt > self._max_retries:
                    break
                time.sleep(backoff)
                backoff = min(backoff * self._backoff_multiplier, self._max_backoff)

        raise RuntimeError(
            f"Gemini call failed after {self._max_retries + 1} attempts: {last_exc}"
        ) from last_exc

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Decide whether an exception is worth retrying."""
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg:
            return True
        if any(f"{code}" in msg for code in (500, 502, 503, 504)):
            return True
        if "timeout" in msg or "connection" in msg:
            return True
        return False

    @staticmethod
    def _parse_structured(raw: str, schema: type[T]) -> T:
        """Parse JSON text into a Pydantic model, handling common Gemini quirks."""
        text = raw.strip()

        # Gemini sometimes wraps JSON in markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            log.error("json_parse_failed", raw_preview=text[:300])
            raise ValueError(f"Gemini returned invalid JSON: {exc}") from exc

        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            log.error("schema_validation_failed", errors=exc.error_count())
            raise ValueError(
                f"Gemini output did not match {schema.__name__}: {exc}"
            ) from exc
