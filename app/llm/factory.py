"""
LLM factory — loads the right provider based on configuration.

Usage:
    provider = create_llm_provider()          # reads from settings.yaml / .env
    provider = create_llm_provider("gemini")  # explicit
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from app.llm.base import LLMProvider
from app.llm.gemini_provider import GeminiProvider

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_settings() -> dict:
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    if settings_path.exists():
        with open(settings_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def create_llm_provider(provider_name: str | None = None) -> LLMProvider:
    """Instantiate the configured LLM provider.

    Resolution order for each setting:
      1. Environment variable (GEMINI_API_KEY, GEMINI_MODEL)
      2. config/settings.yaml
      3. Hardcoded default
    """
    settings = _load_settings()
    llm_cfg = settings.get("llm", {})
    runtime_cfg = settings.get("agent_runtime", {})

    name = provider_name or os.getenv("LLM_PROVIDER") or llm_cfg.get("provider", "gemini")

    if name == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY is not set. Add it to .env or export it."
            )
        return GeminiProvider(
            api_key=api_key,
            model=os.getenv("GEMINI_MODEL") or llm_cfg.get("model", "gemini-2.0-flash"),
            max_retries=runtime_cfg.get("max_retries", 2),
            initial_backoff=runtime_cfg.get("initial_backoff_seconds", 1.0),
            backoff_multiplier=runtime_cfg.get("backoff_multiplier", 2.0),
            max_backoff=runtime_cfg.get("max_backoff_seconds", 8.0),
        )

    raise ValueError(
        f"Unknown LLM provider '{name}'. Supported: gemini. "
        f"Add a new provider in app/llm/ and register it here."
    )
