"""
LLM provider protocol — the contract all providers must satisfy.

Agents depend on this interface, never on a concrete provider.
Swapping Gemini for OpenAI (or a local model) should not touch agent code.
"""
from __future__ import annotations

import abc
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(abc.ABC):
    """Abstract base for every LLM backend the system supports."""

    @abc.abstractmethod
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> T | str:
        """Send a prompt and get back either a parsed Pydantic object or raw text.

        When *response_schema* is provided the provider MUST return an
        instance of that model — how it achieves that (native JSON mode,
        post-hoc parsing, retry-with-repair) is an implementation detail.
        """
        ...

    @abc.abstractmethod
    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """Convenience wrapper that always returns plain text."""
        ...

    @property
    @abc.abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier for logging / audit."""
        ...
