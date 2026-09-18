"""
Pipeline orchestrator  the central coordinator.

Wires together authorization, routing, agents, policy validation,
and audit logging into a single requestresponse pipeline.

    state = orchestrator.run(query, user_context)

The pipeline is intentionally linear (no graph/DAG framework needed):

    authorize  filter  route  screen  investigate  recommend
     validate  render  audit
"""
from __future__ import annotations

import traceback
import uuid
from pathlib import Path
from typing import Any

import structlog
import yaml

from app.agents.screener import ComplianceScreener
from app.agents.investigator import InvestigationAgent
from app.agents.recommender import RecommendationEngine
from app.audit.logger import AuditLogger
from app.feedback.store import FeedbackStore
from app.feedback.weighting import FeedbackWeightingEngine
from app.llm.base import LLMProvider
from app.llm.factory import create_llm_provider
from app.orchestration.router import classify_query
from app.orchestration.state import (
    AgentError,
    AgentState,
    AuthorizedData,
    DeterministicSignal,
    PipelineStatus,
    PolicyResult,
    QueryType,
    RegulatoryEvidence,
    Recommendation,
    RecommendedAction,
    UserContext,
)
from app.policy.validator import PolicyValidator
from app.security.rbac import RBACEngine
from app.security.masking import MaskingEngine
from app.security.injection_detector import InjectionDetector
from app.security.trust_boundary import TrustBoundary

log = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Orchestrator:
    """Top-level pipeline coordinator.

    Usage::

        orch = Orchestrator()
        result = orch.run("Which transactions breach regulatory guidance?",
                          UserContext(profile_id="P01", role="AML_ANALYST"))
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        rbac: RBACEngine | None = None,
        masking: MaskingEngine | None = None,
        feedback_store: FeedbackStore | None = None,
        audit_logger: AuditLogger | None = None,
        retriever: Any | None = None,          # RegulatoryRetriever
        screening_rules: Any | None = None,     # ScreeningRuleEngine
        sanctions_screener: Any | None = None,  # SanctionsScreener
    ):
        self._llm = llm or create_llm_provider()
        self._rbac = rbac or RBACEngine()
        self._masking = masking or MaskingEngine(self._rbac)
        self._feedback_store = feedback_store or FeedbackStore()
        self._audit = audit_logger or AuditLogger()
        self._retriever = retriever
        self._screening_rules = screening_rules
        self._sanctions = sanctions_screener
        self._injection = InjectionDetector()
        self._trust = TrustBoundary(self._injection)
        self._validator = PolicyValidator()
        self._weighting = FeedbackWeightingEngine(self._feedback_store)

        # agents
        self._screener = ComplianceScreener(self._llm)
        self._investigator = InvestigationAgent(self._llm)
        self._recommender = RecommendationEngine(self._llm)

    def run(self, query: str, user_context: UserContext) -> AgentState:
        """Execute the full pipeline for a single query."""
        state = AgentState(
            request_id=str(uuid.uuid4()),
            user_context=user_context,
            query=query,
        )

        try:
            # 1. classify query
            state.query_type = classify_query(query)
            self._audit.log_query_received(state)
            log.info("pipeline_start", query_type=state.query_type.value, role=user_context.role)

            # 2. check for injection in query
            scan = self._injection.scan(query)
            if scan.detected:
                self._audit.log_injection_detected(state, "user_query", scan.patterns_matched)
                log.warning("injection_in_query", patterns=scan.patterns_matched)
                # we still proceed  the injection detector flags it but the
                # trust boundary prevents it from being treated as an instruction

            # 3. authorization gate
            if not self._authorize(state):
                return state

            # 4. gather authorized data
            self._gather_data(state)

            # 5. route to appropriate agent path
            state = self._execute_agent_path(state)

            # 6. generate response text
            state.response_text = self._render_response(state)

        except Exception as exc:
            log.error("pipeline_error", error=str(exc)[:200])
            state.status = PipelineStatus.FAILED
            state.errors.append(AgentError(
                code="PIPELINE_ERROR",
                message=str(exc)[:200],
                retryable=False,
            ))
            state.response_text = (
                "An internal error occurred while processing your request. "
                "Please try again or contact support."
            )

        log.info(
            "pipeline_complete",
            status=state.status.value,
            stages=state.completed_stages,
            error_count=len(state.errors),
        )
        return state

    # -- authorization -------------------------------------------------------

    def _authorize(self, state: AgentState) -> bool:
        """Check whether the user's role permits the requested resource types."""
        role = state.user_context.role
        qt = state.query_type

        # regulatory content is always accessible
        if qt == QueryType.REGULATORY_ONLY:
            self._audit.log_authorization(state, True, "regulations")
            state.completed_stages.append("authorization")
            return True

        # audit content for auditors
        if qt == QueryType.AUDIT:
            auth = self._rbac.authorize(role, "audit")
            if not auth.allowed:
                self._deny(state, auth.reason)
                return False
            state.completed_stages.append("authorization")
            return True

        # transaction-related queries
        if qt in (QueryType.TRANSACTION_ONLY, QueryType.REGULATORY_PLUS_TRANSACTION,
                  QueryType.INVESTIGATION, QueryType.SANCTIONS):
            auth = self._rbac.authorize(role, "transactions")
            if not auth.allowed:
                self._deny(state, auth.reason)
                return False

        # feedback submission
        if qt == QueryType.FEEDBACK:
            auth = self._rbac.authorize(role, "feedback")
            if not auth.allowed:
                self._deny(state, auth.reason)
                return False

        self._audit.log_authorization(state, True)
        state.completed_stages.append("authorization")
        return True

    def _deny(self, state: AgentState, reason: str) -> None:
        state.status = PipelineStatus.FAILED
        state.response_text = (
            f"Access denied: {reason}. "
            "Your role does not have permission to access the requested information. "
            "Contact the compliance team if you believe this is an error."
        )
        self._audit.log_authorization(state, False)

    # -- data gathering ------------------------------------------------------

    def _gather_data(self, state: AgentState) -> None:
        """Retrieve and filter data based on query type and user authorization."""
        qt = state.query_type
        role = state.user_context.role
        authorized = AuthorizedData()

        # regulatory evidence (via retriever if available)
        if qt in (QueryType.REGULATORY_ONLY, QueryType.REGULATORY_PLUS_TRANSACTION,
                  QueryType.INVESTIGATION):
            if self._retriever:
                try:
                    evidence = self._retriever.search(state.query, top_k=5)
                    authorized.regulatory_evidence = evidence
                except Exception as exc:
                    log.warning("retrieval_failed", error=str(exc)[:100])

        # transaction data (from CSV / in-memory for demo)
        if qt in (QueryType.TRANSACTION_ONLY, QueryType.REGULATORY_PLUS_TRANSACTION,
                  QueryType.INVESTIGATION, QueryType.SANCTIONS):
            txns = self._load_transactions(state)
            # mask PII in transaction records
            masked = [self._masking.mask_record(t, role, "transaction") for t in txns]
            authorized.transactions = masked
            self._audit.log_data_filtered(state, "transactions", len(masked))

            # run deterministic screening
            if self._screening_rules and txns:
                signals = self._screening_rules.screen_transactions(txns)
                authorized.deterministic_signals = signals

            # sanctions screening on counterparty names
            if self._sanctions and txns:
                for txn in txns:
                    cp_name = txn.get("counterparty_name", "")
                    if cp_name:
                        # check for injection in counterparty name
                        cp_scan = self._injection.scan(cp_name)
                        if cp_scan.detected:
                            self._audit.log_injection_detected(
                                state, "counterparty_name", cp_scan.patterns_matched
                            )
                        candidates = self._sanctions.screen_name(cp_name)
                        authorized.sanctions_candidates.extend(candidates)

        # feedback history
        authorized.feedback_history = [
            e.model_dump() for e in self._feedback_store.get_all_events()
        ]

        state.authorized_data = authorized
        state.completed_stages.append("data_gathering")

    def _load_transactions(self, state: AgentState) -> list[dict]:
        """Load transactions from CSV.  In production this queries PostgreSQL."""
        csv_path = _PROJECT_ROOT / "data" / "raw" / "transactions" / "transactions.csv"
        if not csv_path.exists():
            log.warning("no_transaction_data", path=str(csv_path))
            return []

        try:
            import pandas as pd
            df = pd.read_csv(csv_path)

            # apply role-based filtering
            role = state.user_context.role
            perms = self._rbac.get_permitted_resources(role)
            txn_access = perms.get("transactions", "denied")

            if txn_access == "denied":
                return []
            elif txn_access == "flagged_only":
                # only return flagged transactions (those with alerts)
                alerts_path = _PROJECT_ROOT / "data" / "raw" / "transactions" / "alerts.csv"
                if alerts_path.exists():
                    import json as _json
                    alerts_df = pd.read_csv(alerts_path)
                    flagged_ids = set()
                    # alerts CSV has transaction_ids column with JSON arrays
                    tid_col = "transaction_ids" if "transaction_ids" in alerts_df.columns else "transaction_id"
                    for val in alerts_df[tid_col].dropna():
                        try:
                            parsed = _json.loads(str(val)) if str(val).startswith("[") else [str(val)]
                            flagged_ids.update(str(x) for x in parsed)
                        except (ValueError, TypeError):
                            flagged_ids.add(str(val))
                    df = df[df["transaction_id"].astype(str).isin(flagged_ids)]
            elif txn_access == "scoped":
                # RM: filter by portfolio
                portfolio_id = state.user_context.portfolio_id
                if portfolio_id:
                    customers_path = _PROJECT_ROOT / "data" / "raw" / "customers" / "customers.csv"
                    if customers_path.exists():
                        cust_df = pd.read_csv(customers_path)
                        portfolio_custs = set(
                            cust_df[cust_df["relationship_manager_id"] == portfolio_id]["customer_id"].astype(str)
                        )
                        df = df[df["customer_id"].astype(str).isin(portfolio_custs)]

            return df.to_dict("records")
        except Exception as exc:
            log.error("transaction_load_failed", error=str(exc)[:150])
            return []

    # -- agent execution -----------------------------------------------------

    def _execute_agent_path(self, state: AgentState) -> AgentState:
        """Run the appropriate agent chain based on query type."""
        qt = state.query_type

        if qt == QueryType.REGULATORY_ONLY:
            # just retrieval + LLM explanation, no screening pipeline
            state = self._handle_regulatory_query(state)
            return state

        if qt == QueryType.AUDIT:
            state = self._handle_audit_query(state)
            return state

        if qt == QueryType.ACCESS_REQUEST:
            state.response_text = self._describe_access(state)
            state.completed_stages.append("access_request")
            return state

        if qt == QueryType.FEEDBACK:
            state.response_text = "Feedback submission is available via the /feedback endpoint or CLI."
            state.completed_stages.append("feedback")
            return state

        # full pipeline: screen  investigate  recommend  validate
        state = self._run_full_pipeline(state)
        return state

    def _handle_regulatory_query(self, state: AgentState) -> AgentState:
        """Answer a pure regulatory question using retrieved evidence."""
        evidence = state.authorized_data.regulatory_evidence
        if not evidence:
            state.response_text = (
                "No sufficiently relevant regulatory evidence was retrieved "
                "from the available corpus for this query."
            )
            state.completed_stages.append("regulatory_retrieval")
            return state

        # build context from evidence
        evidence_text = "\n\n".join(
            f"[{e.document_id}  {e.section or 'N/A'}  p.{e.page or '?'}]\n{e.text_excerpt}"
            for e in evidence
        )

        try:
            answer = self._llm.generate_text(
                system_prompt=(
                    "You are an AML compliance assistant. Answer the question using ONLY "
                    "the regulatory evidence provided below. Cite the document, section, and "
                    "page number for each claim. If the evidence does not support an answer, "
                    "say so explicitly. Do not invent regulatory rules."
                ),
                user_prompt=f"Question: {state.query}\n\nRegulatory Evidence:\n{evidence_text}",
            )
            state.response_text = answer
        except Exception as exc:
            log.error("regulatory_llm_failed", error=str(exc)[:150])
            state.response_text = "Unable to generate regulatory analysis at this time."
            state.errors.append(AgentError(code="LLM_FAILURE", message=str(exc)[:150]))

        state.completed_stages.append("regulatory_retrieval")
        return state

    def _handle_audit_query(self, state: AgentState) -> AgentState:
        """Return audit trail information."""
        events = self._audit.get_events(limit=50)
        if not events:
            state.response_text = "No audit events found."
        else:
            lines = []
            for evt in events[-20:]:
                lines.append(
                    f"[{evt.get('timestamp', '?')}] {evt.get('event_type', '?')} "
                    f"| role={evt.get('actor_role', '?')} | outcome={evt.get('outcome', 'N/A')}"
                )
            state.response_text = "Recent audit trail:\n" + "\n".join(lines)
        state.completed_stages.append("audit")
        return state

    def _describe_access(self, state: AgentState) -> str:
        perms = self._rbac.get_permitted_resources(state.user_context.role)
        lines = [f"Access permissions for role '{state.user_context.role}':"]
        for resource, level in perms.items():
            lines.append(f"   {resource}: {level}")
        return "\n".join(lines)

    def _run_full_pipeline(self, state: AgentState) -> AgentState:
        """Execute the three-agent pipeline with policy validation."""

        # Agent 1: Screener
        self._audit.log_agent_started(state, "COMPLIANCE_SCREENER")
        try:
            state = self._screener.run(state)
            self._audit.log_agent_completed(state, "COMPLIANCE_SCREENER")
        except Exception as exc:
            log.error("screener_failed", error=str(exc)[:150])
            self._audit.log_agent_failed(state, "COMPLIANCE_SCREENER", "SCREENER_FAILURE", str(exc)[:150])
            state.errors.append(AgentError(
                code="SCREENER_FAILURE", message=str(exc)[:150], agent_name="COMPLIANCE_SCREENER"
            ))
            state.status = PipelineStatus.FAILED
            state.response_text = "Screening could not be completed. Manual review is required."
            return state

        # Agent 2: Investigator
        self._audit.log_agent_started(state, "INVESTIGATOR")
        try:
            state = self._investigator.run(state)
            self._audit.log_agent_completed(state, "INVESTIGATOR")
        except Exception as exc:
            log.error("investigator_failed", error=str(exc)[:150])
            self._audit.log_agent_failed(state, "INVESTIGATOR", "INVESTIGATOR_FAILURE", str(exc)[:150])
            state.errors.append(AgentError(
                code="INVESTIGATOR_UNAVAILABLE", message="Investigation could not be completed safely.",
                retryable=False, agent_name="INVESTIGATOR"
            ))
            state.status = PipelineStatus.MANUAL_REVIEW_REQUIRED
            state.missing_stages.extend(["investigation", "recommendation"])
            state.response_text = (
                "Screening completed but investigation could not be completed. "
                "No recommendation was generated. Manual review is required."
            )
            return state

        # Agent 3: Recommender
        self._audit.log_agent_started(state, "RECOMMENDER")
        try:
            state = self._recommender.run(state)
            self._audit.log_agent_completed(state, "RECOMMENDER")
            if state.recommendation:
                self._audit.log_recommendation(state, state.recommendation.suggested_action.value)
        except Exception as exc:
            log.error("recommender_failed", error=str(exc)[:150])
            self._audit.log_agent_failed(state, "RECOMMENDER", "RECOMMENDER_FAILURE", str(exc)[:150])
            state.errors.append(AgentError(
                code="RECOMMENDER_FAILURE", message=str(exc)[:150], agent_name="RECOMMENDER"
            ))
            state.status = PipelineStatus.MANUAL_REVIEW_REQUIRED
            state.missing_stages.append("recommendation")

        # Policy Validator (deterministic, not an agent)
        state = self._validator.validate(state)
        if state.policy_validation:
            self._audit.log_policy_validation(state, state.policy_validation.result.value)

        return state

    # -- response rendering --------------------------------------------------

    def _render_response(self, state: AgentState) -> str:
        """Build a human-readable response from the pipeline results."""
        if state.response_text:
            return state.response_text

        parts = []

        if state.screening_alert:
            alert = state.screening_alert
            parts.append(f"[SEARCH] Screening Alert: {alert.alert_id}")
            parts.append(f"   Severity: {alert.severity.value}")
            parts.append(f"   Confidence: {alert.confidence:.0%}")
            if alert.triggered_indicators:
                parts.append(f"   Indicators: {', '.join(alert.triggered_indicators)}")
            if alert.screener_reasoning:
                parts.append(f"   Reasoning: {alert.screener_reasoning}")

        if state.investigation_result:
            inv = state.investigation_result
            parts.append(f"\n[INVESTIGATE] Investigation: {inv.investigation_id}")
            parts.append(f"   Confidence: {inv.confidence:.0%}")
            if inv.supporting_evidence:
                parts.append(f"   Supporting: {'; '.join(inv.supporting_evidence[:3])}")
            if inv.contradictory_evidence:
                parts.append(f"   Contradictory: {'; '.join(inv.contradictory_evidence[:3])}")
            if inv.missing_evidence:
                parts.append(f"   Missing: {'; '.join(inv.missing_evidence[:3])}")
            if inv.assessment:
                parts.append(f"   Assessment: {inv.assessment}")

        if state.recommendation:
            rec = state.recommendation
            parts.append(f"\n[RECOMMEND] Recommendation: {rec.suggested_action.value}")
            parts.append(f"   Confidence: {rec.confidence:.0%}")
            parts.append(f"   Rationale: {rec.rationale}")

        if state.policy_validation:
            pv = state.policy_validation
            parts.append(f"\n[PASS] Policy Validation: {pv.result.value}")
            if pv.reasons:
                parts.append(f"   Reasons: {'; '.join(pv.reasons)}")

        if state.errors:
            parts.append(f"\n[WARN]  Errors: {len(state.errors)}")
            for err in state.errors:
                parts.append(f"   [{err.code}] {err.message}")

        return "\n".join(parts) if parts else "No results generated for this query."
