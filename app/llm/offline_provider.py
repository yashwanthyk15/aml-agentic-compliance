"""Deterministic provider for local development and reproducible evaluation."""
from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel

from app.llm.base import LLMProvider

T = TypeVar("T", bound=BaseModel)


class OfflineProvider(LLMProvider):
    """Return conservative structured outputs without network access."""

    @property
    def model_name(self) -> str:
        return "offline-deterministic"

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> T | str:
        if response_schema is None:
            return self.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        fields = {}
        name = response_schema.__name__
        prompt = user_prompt.lower()
        if name == "ScreenerLLMResponse":
            indicators = []
            if "structur" in prompt:
                indicators.append("STRUCTURING_PATTERN")
            if "sanctions" in prompt or "watchlist" in prompt:
                indicators.append("SANCTIONS_CANDIDATE")
            if "high-risk" in prompt or "high risk" in prompt:
                indicators.append("HIGH_RISK_GEOGRAPHY")
            fields = {
                "triggered_indicators": indicators,
                "supporting_evidence": ["Deterministic screening evidence was provided."],
                "missing_evidence": ["Human review of source records remains required."],
                "confidence": 0.75 if indicators else 0.45,
                "severity": "HIGH" if indicators else "LOW",
                "reasoning": "Offline analysis uses only authorized deterministic evidence.",
            }
        elif name == "InvestigatorLLMResponse":
            fields = {
                "supporting_evidence": ["The screening signal is present in the authorized records."],
                "contradictory_evidence": [],
                "missing_evidence": ["Additional customer context requires analyst review."],
                "regulatory_applicability": "The supplied evidence should be reviewed against the cited source.",
                "sanctions_assessment": "No source-supported confirmation was established by offline screening.",
                "legitimate_explanation": None,
                "assessment": "The case requires a documented human decision.",
                "confidence": 0.65,
                "requires_human_review": True,
            }
        elif name == "RecommenderLLMResponse":
            fields = {
                "suggested_action": "MANUAL_REVIEW",
                "rationale": "Offline mode provides decision support only and requires human review.",
                "evidence_summary": ["Typed screening and investigation handoffs were completed."],
                "confidence": 0.6,
                "key_factors": ["Human review required"],
            }
        else:
            fields = {}
        return response_schema.model_validate(fields)

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        marker = re.search(r"\[([^\]]+)\s+p\.([^\]]+)\]", user_prompt)
        citation = f" [{marker.group(1)} p.{marker.group(2)}]" if marker else ""
        return (
            "Offline analysis based only on the authorized regulatory evidence"
            f"{citation}: the available evidence does not support a stronger conclusion. "
            "Review the cited source and records before acting."
        )
