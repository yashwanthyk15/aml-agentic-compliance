"""
Policy Validator — deterministic safety checks.

This is NOT an LLM agent. It applies hard-coded policy rules to the
pipeline output and can override the recommendation to MANUAL_REVIEW
when safety conditions are met.
"""
import structlog

from app.orchestration.state import (
    AgentState,
    PipelineStatus,
    PolicyResult,
    PolicyValidation,
    RecommendedAction,
    SanctionsMatchState,
)

log = structlog.get_logger(__name__)


class PolicyValidator:
    """Deterministic policy validator — no LLM calls."""

    def validate(self, state: AgentState) -> AgentState:
        """Apply safety rules and produce a PolicyValidation."""
        log.info("policy_validation_started", request_id=state.request_id[:8])

        reasons: list[str] = []

        # Rule 1: missing investigation
        if not state.investigation_result:
            reasons.append("Investigation stage incomplete")

        # Rule 2: missing recommendation
        if not state.recommendation:
            reasons.append("Recommendation stage incomplete")

        # Rule 3: low confidence
        if state.recommendation and state.recommendation.confidence < 0.5:
            reasons.append("Low confidence recommendation")

        # Rule 4: strong sanctions match
        if state.authorized_data.sanctions_candidates:
            for sc in state.authorized_data.sanctions_candidates:
                if sc.match_state in (
                    SanctionsMatchState.STRONG_POTENTIAL_MATCH,
                    SanctionsMatchState.CONFIRMED_SOURCE_SUPPORTED_MATCH,
                ):
                    reasons.append("Potential sanctions match requires human review")
                    break

        # Rule 5: conflicting evidence
        if state.investigation_result:
            has_supporting = len(state.investigation_result.supporting_evidence) > 0
            has_contradictory = len(state.investigation_result.contradictory_evidence) > 0
            if has_supporting and has_contradictory:
                reasons.append("Conflicting evidence")

        # Rule 6: pipeline errors
        if state.errors:
            reasons.append("Pipeline errors encountered")

        # build the validation result
        if reasons:
            original = state.recommendation.suggested_action if state.recommendation else None
            overridden = (
                RecommendedAction.MANUAL_REVIEW
                if original and original != RecommendedAction.MANUAL_REVIEW
                else None
            )

            state.policy_validation = PolicyValidation(
                result=PolicyResult.MANUAL_REVIEW_REQUIRED,
                reasons=reasons,
                overridden_action=overridden,
                original_action=original,
            )
            if state.recommendation and overridden:
                state.recommendation.suggested_action = overridden
                state.recommendation.requires_human_review = True
            state.status = PipelineStatus.MANUAL_REVIEW_REQUIRED
            log.warning("policy_manual_review", reasons=reasons)
        else:
            state.policy_validation = PolicyValidation(
                result=PolicyResult.VALID,
                reasons=[],
            )
            log.info("policy_validation_passed")

        state.completed_stages.append("policy_validation")
        return state
