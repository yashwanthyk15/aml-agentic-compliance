import structlog
from app.orchestration.state import (
    AgentState, PolicyValidation, PolicyResult, PipelineStatus, SanctionsMatchState
)

logger = structlog.get_logger(__name__)

class PolicyValidator:
    def __init__(self):
        self.stage_name = "policy_validation"

    def validate(self, state: AgentState) -> AgentState:
        """Apply deterministic safety rules to the recommendation."""
        logger.info("Starting policy validation", query_id=state.query_id)
        
        reasons = []
        requires_manual_review = False
        
        # Rule 1: No investigation_result
        if not state.investigation_result:
            requires_manual_review = True
            reasons.append("Investigation stage incomplete")
            
        # Rule 2: No recommendation
        if not state.recommendation:
            requires_manual_review = True
            reasons.append("Recommendation stage incomplete")
            
        # Rule 3: Low confidence recommendation
        if state.recommendation and state.recommendation.confidence < 0.5:
            requires_manual_review = True
            reasons.append("Low confidence recommendation")
            
        # Rule 4: Sanctions match
        if state.authorized_data and state.authorized_data.sanctions_candidates:
            for candidate in state.authorized_data.sanctions_candidates:
                if candidate.match_state in (SanctionsMatchState.STRONG_POTENTIAL_MATCH, SanctionsMatchState.CONFIRMED_MATCH):
                    requires_manual_review = True
                    reasons.append("Potential sanctions match requires human review")
                    break
                    
        # Rule 5: Conflicting evidence
        if state.investigation_result:
            has_strong_supporting = bool(state.investigation_result.supporting_evidence)
            has_strong_contradictory = bool(state.investigation_result.contradictory_evidence)
            if has_strong_supporting and has_strong_contradictory:
                requires_manual_review = True
                reasons.append("Conflicting evidence")
                
        # Rule 6: Pipeline errors
        if state.errors:
            requires_manual_review = True
            reasons.append("Pipeline errors encountered")

        # Apply results
        if requires_manual_review:
            logger.warning("Policy validation failed, manual review required", reasons=reasons)
            state.status = PipelineStatus.MANUAL_REVIEW_REQUIRED
            
            validation = PolicyValidation(
                result=PolicyResult.MANUAL_REVIEW_REQUIRED,
                reasoning="; ".join(reasons),
                triggered_rules=reasons
            )
        else:
            logger.info("Policy validation passed")
            validation = PolicyValidation(
                result=PolicyResult.VALID,
                reasoning="All policy rules passed.",
                triggered_rules=[]
            )
            
        state.policy_validation = validation
        if self.stage_name not in state.completed_stages:
            state.completed_stages.append(self.stage_name)
            
        return state
