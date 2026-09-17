import json
import structlog
from typing import List
from pydantic import BaseModel, Field
from app.llm.base import LLMProvider
from app.orchestration.state import (
    AgentState, Recommendation, AgentError, RecommendedAction, PipelineStatus
)

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are an AML recommendation engine. Based on the screening alert and investigation findings, provide a recommendation for the compliance team.

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
- Return structured output matching the required schema"""

class RecommenderLLMResponse(BaseModel):
    suggested_action: str = Field(description="Action to recommend: ESCALATE, NO_ACTION, or MANUAL_REVIEW")
    rationale: str = Field(description="Reasoning for the recommendation")
    evidence_summary: List[str] = Field(description="Summary of key evidence used")
    confidence: float = Field(description="Confidence score (0.0 to 1.0)", ge=0.0, le=1.0)
    key_factors: List[str] = Field(description="Key factors driving the decision")

class RecommendationEngine:
    def __init__(self, llm: LLMProvider):
        self.llm = llm
        self.stage_name = "recommendation"

    def run(self, state: AgentState) -> AgentState:
        """Produce a constrained recommendation based on screening + investigation."""
        logger.info("Starting recommendation stage", query_id=state.query_id)
        
        if not state.screening_alert or not state.investigation_result:
            error_msg = "Cannot run recommendation: screening_alert or investigation_result is missing."
            logger.error(error_msg)
            state.status = PipelineStatus.MANUAL_REVIEW_REQUIRED
            state.errors.append(
                AgentError(
                    stage=self.stage_name,
                    error_message=error_msg,
                    details="Missing prerequisite stages."
                )
            )
            return state
            
        try:
            # Build context
            context_data = {
                "screening_alert": state.screening_alert.model_dump(),
                "investigation_result": state.investigation_result.model_dump(),
                "feedback_history": state.authorized_data.feedback_history if state.authorized_data and hasattr(state.authorized_data, 'feedback_history') else []
            }
            
            prompt = f"Provide a recommendation based on the following findings:\n\n{json.dumps(context_data, indent=2)}"
            
            # Call LLM
            logger.debug("Calling LLM for recommendation")
            response = self.llm.generate(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                response_model=RecommenderLLMResponse
            )
            
            # Map action
            try:
                action = RecommendedAction(response.suggested_action.upper())
            except ValueError:
                logger.warning(f"Invalid suggested_action {response.suggested_action}, defaulting to MANUAL_REVIEW")
                action = RecommendedAction.MANUAL_REVIEW

            # Create Recommendation
            recommendation = Recommendation(
                suggested_action=action,
                rationale=response.rationale,
                evidence_summary=response.evidence_summary,
                confidence=response.confidence,
                key_factors=response.key_factors,
                requires_human_review=True # Always set to True as per rules
            )
            
            state.recommendation = recommendation
            if self.stage_name not in state.completed_stages:
                state.completed_stages.append(self.stage_name)
            
            logger.info("Recommendation completed successfully")
            
        except Exception as e:
            logger.error("Error during recommendation", error=str(e))
            state.status = PipelineStatus.MANUAL_REVIEW_REQUIRED
            state.errors.append(
                AgentError(
                    stage=self.stage_name,
                    error_message=str(e),
                    details="Exception occurred during LLM recommendation phase."
                )
            )
            
        return state
