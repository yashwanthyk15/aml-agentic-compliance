"""
Agent 3: Recommendation Engine

Takes the ScreeningAlert + InvestigationResult and produces a
constrained recommendation: ESCALATE, NO_ACTION, or MANUAL_REVIEW.

This is decision SUPPORT, not decision authority. The output always
sets requires_human_review=True.
"""
import structlog
from pydantic import BaseModel, Field

from app.llm.base import LLMProvider
from app.orchestration.state import (
    AgentError, AgentState, PipelineStatus, Recommendation, RecommendedAction,
)

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """\
You are an AML recommendation engine. Based on the screening alert and investigation findings, \
provide a recommendation for the compliance team.

You may ONLY recommend one of:
- ESCALATE: Strong evidence of compliance concern requiring senior review
- NO_ACTION: Evidence does not support a compliance concern at this time
- MANUAL_REVIEW: Insufficient or conflicting evidence requiring human judgment

Rules:
- You are providing decision SUPPORT, not making a legal determination
- Always explain your reasoning with specific evidence references
- If evidence conflicts or is insufficient, recommend MANUAL_REVIEW
- Consider historical feedback patterns when available
- A potential sanctions match should generally lead to ESCALATE or MANUAL_REVIEW
- Return a JSON object matching the schema below"""


class RecommenderLLMResponse(BaseModel):
    suggested_action: str = Field(description="ESCALATE, NO_ACTION, or MANUAL_REVIEW")
    rationale: str = Field(default="")
    evidence_summary: list[str] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    key_factors: list[str] = Field(default_factory=list)


class RecommendationEngine:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(self, state: AgentState) -> AgentState:
        """Produce a constrained recommendation."""
        log.info("recommender_started", request_id=state.request_id[:8])

        if not state.screening_alert:
            log.warning("no_screening_alert_for_recommendation")
            state.errors.append(AgentError(
                code="NO_SCREENING_ALERT",
                message="Cannot recommend without screening results.",
                agent_name="RECOMMENDER",
            ))
            return state

        try:
            user_prompt = self._build_prompt(state)

            response = self.llm.generate(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=RecommenderLLMResponse,
            )

            # parse the action (default to MANUAL_REVIEW if unrecognised)
            try:
                action = RecommendedAction(response.suggested_action.upper())
            except ValueError:
                log.warning("unknown_action", raw=response.suggested_action)
                action = RecommendedAction.MANUAL_REVIEW

            rec = Recommendation(
                alert_id=state.screening_alert.alert_id,
                investigation_id=(
                    state.investigation_result.investigation_id
                    if state.investigation_result else "N/A"
                ),
                suggested_action=action,
                rationale=response.rationale,
                evidence_summary=response.evidence_summary,
                confidence=response.confidence,
                requires_human_review=True,  # always
            )

            state.recommendation = rec
            state.completed_stages.append("recommendation")
            log.info("recommender_completed", action=action.value, confidence=rec.confidence)

        except Exception as exc:
            log.error("recommender_failed", error=str(exc)[:200])
            state.errors.append(AgentError(
                code="RECOMMENDER_FAILURE",
                message=str(exc)[:200],
                retryable=False,
                agent_name="RECOMMENDER",
            ))
            state.status = PipelineStatus.MANUAL_REVIEW_REQUIRED

        return state

    def _build_prompt(self, state: AgentState) -> str:
        parts = [f"Alert: {state.screening_alert.alert_id}"]
        parts.append(f"Severity: {state.screening_alert.severity.value}")
        parts.append(f"Screener Confidence: {state.screening_alert.confidence:.2f}")

        if state.screening_alert.triggered_indicators:
            parts.append(f"Indicators: {', '.join(state.screening_alert.triggered_indicators)}")
        if state.screening_alert.screener_reasoning:
            parts.append(f"Screener Reasoning: {state.screening_alert.screener_reasoning}")

        if state.investigation_result:
            inv = state.investigation_result
            parts.append(f"\nInvestigation Confidence: {inv.confidence:.2f}")
            if inv.supporting_evidence:
                parts.append(f"Supporting: {'; '.join(inv.supporting_evidence[:5])}")
            if inv.contradictory_evidence:
                parts.append(f"Contradictory: {'; '.join(inv.contradictory_evidence[:5])}")
            if inv.missing_evidence:
                parts.append(f"Missing: {'; '.join(inv.missing_evidence[:5])}")
            if inv.assessment:
                parts.append(f"Assessment: {inv.assessment}")
            if inv.sanctions_assessment:
                parts.append(f"Sanctions: {inv.sanctions_assessment}")

        # historical feedback
        fb = state.authorized_data.feedback_history
        if fb:
            parts.append(f"\nHistorical feedback: {len(fb)} prior dispositions available")

        return "\n".join(parts)
