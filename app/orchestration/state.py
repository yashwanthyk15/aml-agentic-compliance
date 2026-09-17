"""
Typed state objects for the agent pipeline.

Every agent communicates through these Pydantic models — never through
free-form text blobs.  The orchestrator holds one AgentState instance
that accumulates results as the pipeline progresses.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class QueryType(str, Enum):
    REGULATORY_ONLY = "REGULATORY_ONLY"
    TRANSACTION_ONLY = "TRANSACTION_ONLY"
    REGULATORY_PLUS_TRANSACTION = "REGULATORY_PLUS_TRANSACTION"
    INVESTIGATION = "INVESTIGATION"
    AUDIT = "AUDIT"
    ACCESS_REQUEST = "ACCESS_REQUEST"
    FEEDBACK = "FEEDBACK"
    SANCTIONS = "SANCTIONS"


class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SanctionsMatchState(str, Enum):
    NO_MATCH = "NO_MATCH"
    POTENTIAL_MATCH = "POTENTIAL_MATCH"
    STRONG_POTENTIAL_MATCH = "STRONG_POTENTIAL_MATCH"
    CONFIRMED_SOURCE_SUPPORTED_MATCH = "CONFIRMED_SOURCE_SUPPORTED_MATCH"


class Disposition(str, Enum):
    TRUE_HIT = "TRUE_HIT"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    ESCALATED = "ESCALATED"


class RecommendedAction(str, Enum):
    ESCALATE = "ESCALATE"
    NO_ACTION = "NO_ACTION"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class PolicyResult(str, Enum):
    VALID = "VALID"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class PipelineStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


# ---------------------------------------------------------------------------
# User / Auth context
# ---------------------------------------------------------------------------

class UserContext(BaseModel):
    """Identifies the requesting user and their role for RBAC."""
    profile_id: str
    role: str
    admin: bool = False
    portfolio_id: str | None = None  # only for RELATIONSHIP_MANAGER


# ---------------------------------------------------------------------------
# Evidence objects
# ---------------------------------------------------------------------------

class RegulatoryEvidence(BaseModel):
    """A single piece of retrieved regulatory evidence with provenance."""
    document_id: str
    document_version: str | None = None
    section: str | None = None
    section_path: list[str] = Field(default_factory=list)
    page: int | None = None
    chunk_id: str | None = None
    text_excerpt: str
    jurisdiction: str | None = None
    effective_date: str | None = None
    relevance_score: float = 0.0
    source_type: str = "REGULATORY"


class TransactionEvidence(BaseModel):
    """Summarised transaction data safe for the current role."""
    transaction_id: str
    timestamp: str | None = None
    amount: float | None = None
    currency: str | None = None
    direction: str | None = None
    country: str | None = None
    channel: str | None = None
    transaction_type: str | None = None
    counterparty_name: str | None = None
    remittance_text: str | None = None
    risk_signals: list[str] = Field(default_factory=list)


class SanctionsCandidate(BaseModel):
    """A single sanctions-matching candidate."""
    entry_id: str
    source: str = "OFAC_SDN"
    matched_name: str
    query_name: str
    match_score: float
    match_state: SanctionsMatchState
    primary_name: str
    aliases: list[str] = Field(default_factory=list)
    programs: list[str] = Field(default_factory=list)
    entity_type: str | None = None
    countries: list[str] = Field(default_factory=list)
    identifiers: list[dict[str, str]] = Field(default_factory=list)


class DeterministicSignal(BaseModel):
    """Output of a deterministic screening rule."""
    rule_id: str
    rule_name: str
    rule_type: str  # INTERNAL_DEMO_RULE | REGULATORY_REQUIREMENT
    description: str
    triggered: bool
    details: dict[str, Any] = Field(default_factory=dict)
    severity: AlertSeverity = AlertSeverity.MEDIUM


# ---------------------------------------------------------------------------
# Agent handoff models
# ---------------------------------------------------------------------------

class ScreeningAlert(BaseModel):
    """Produced by Agent 1 (Compliance Screener)."""
    alert_id: str = Field(default_factory=lambda: f"ALT_{uuid.uuid4().hex[:8].upper()}")
    transaction_ids: list[str] = Field(default_factory=list)
    customer_id: str | None = None
    triggered_indicators: list[str] = Field(default_factory=list)
    deterministic_signals: list[DeterministicSignal] = Field(default_factory=list)
    regulatory_evidence: list[RegulatoryEvidence] = Field(default_factory=list)
    sanctions_matches: list[SanctionsCandidate] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    severity: AlertSeverity = AlertSeverity.MEDIUM
    source_trace: list[str] = Field(default_factory=list)
    screener_reasoning: str = ""


class InvestigationResult(BaseModel):
    """Produced by Agent 2 (Investigation Agent)."""
    investigation_id: str = Field(default_factory=lambda: f"INV_{uuid.uuid4().hex[:8].upper()}")
    alert_id: str
    supporting_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    regulatory_applicability: str = ""
    sanctions_assessment: str | None = None
    legitimate_explanation: str | None = None
    assessment: str = ""
    confidence: float = 0.0
    requires_human_review: bool = False
    investigator_reasoning: str = ""


class Recommendation(BaseModel):
    """Produced by Agent 3 (Recommendation Engine)."""
    recommendation_id: str = Field(default_factory=lambda: f"REC_{uuid.uuid4().hex[:8].upper()}")
    alert_id: str
    investigation_id: str
    suggested_action: RecommendedAction
    rationale: str
    evidence_summary: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    requires_human_review: bool = True
    learning_signal: dict[str, Any] = Field(default_factory=dict)


class PolicyValidation(BaseModel):
    """Output of the deterministic policy validator."""
    result: PolicyResult
    reasons: list[str] = Field(default_factory=list)
    overridden_action: RecommendedAction | None = None
    original_action: RecommendedAction | None = None


# ---------------------------------------------------------------------------
# Error / failure
# ---------------------------------------------------------------------------

class AgentError(BaseModel):
    """Structured failure from any pipeline stage."""
    code: str
    message: str
    retryable: bool = False
    agent_name: str | None = None


# ---------------------------------------------------------------------------
# Audit event
# ---------------------------------------------------------------------------

class AuditEvent(BaseModel):
    """One row destined for the audit_log table."""
    audit_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor_profile_id: str | None = None
    actor_role: str
    event_type: str
    resource_type: str | None = None
    resource_id: str | None = None
    agent_name: str | None = None
    agent_run_id: str | None = None
    authorization_decision: str | None = None
    query_type: str | None = None
    outcome: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

class FeedbackEvent(BaseModel):
    """An analyst disposition on an alert."""
    feedback_id: str = Field(default_factory=lambda: f"FB_{uuid.uuid4().hex[:8].upper()}")
    alert_id: str
    analyst_profile_id: str
    disposition: Disposition
    pattern_key: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Top-level pipeline state
# ---------------------------------------------------------------------------

class AuthorizedData(BaseModel):
    """Data that has passed through RBAC and masking."""
    transactions: list[dict[str, Any]] = Field(default_factory=list)
    customers: list[dict[str, Any]] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    sanctions_candidates: list[SanctionsCandidate] = Field(default_factory=list)
    regulatory_evidence: list[RegulatoryEvidence] = Field(default_factory=list)
    deterministic_signals: list[DeterministicSignal] = Field(default_factory=list)
    feedback_history: list[dict[str, Any]] = Field(default_factory=list)


class AgentState(BaseModel):
    """
    Central pipeline state passed through every stage.

    The orchestrator creates one of these per request and each agent
    writes its output into the appropriate field.  Downstream agents
    consume the typed output of upstream agents — never raw text.
    """
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_context: UserContext
    query: str
    query_type: QueryType | None = None

    # pre-agent data (after RBAC + masking)
    authorized_data: AuthorizedData = Field(default_factory=AuthorizedData)

    # agent outputs
    screening_alert: ScreeningAlert | None = None
    investigation_result: InvestigationResult | None = None
    recommendation: Recommendation | None = None
    policy_validation: PolicyValidation | None = None

    # pipeline metadata
    status: PipelineStatus = PipelineStatus.SUCCESS
    completed_stages: list[str] = Field(default_factory=list)
    missing_stages: list[str] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)

    # final output
    response_text: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
