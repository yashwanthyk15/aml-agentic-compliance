"""
Audit logger — records every meaningful action in the pipeline.

Writes to both the PostgreSQL audit_log table (when available) and
a local JSONL file as a fallback.  Never stores raw restricted PII.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.orchestration.state import AgentState, AuditEvent

log = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_AUDIT_LOG_DIR = _PROJECT_ROOT / "data" / "processed"


class AuditLogger:
    """Append-only audit logger backed by a JSONL file.

    In a production system this would write to PostgreSQL's audit_log
    table.  For the demo we keep a local file so the system works
    without a running database.
    """

    def __init__(self, log_path: Path | None = None):
        self._log_path = log_path or (_AUDIT_LOG_DIR / "audit_log.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # -- public helpers for common event types ------------------------------

    def log_query_received(self, state: AgentState) -> AuditEvent:
        return self._record(state, "QUERY_RECEIVED", outcome=state.query_type.value if state.query_type else "UNKNOWN")

    def log_authorization(self, state: AgentState, allowed: bool, resource_type: str = "", resource_id: str = "") -> AuditEvent:
        event_type = "AUTHORIZATION_ALLOWED" if allowed else "AUTHORIZATION_DENIED"
        return self._record(
            state, event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            authorization_decision="ALLOW" if allowed else "DENY",
        )

    def log_data_filtered(self, state: AgentState, resource_type: str, count: int) -> AuditEvent:
        return self._record(
            state, "DATA_FILTERED",
            resource_type=resource_type,
            metadata={"record_count": count},
        )

    def log_pii_masked(self, state: AgentState, fields_masked: list[str]) -> AuditEvent:
        return self._record(
            state, "PII_MASKED",
            metadata={"fields_masked": fields_masked},
        )

    def log_injection_detected(self, state: AgentState, field_name: str, patterns: list[str]) -> AuditEvent:
        return self._record(
            state, "INJECTION_DETECTED",
            metadata={"field": field_name, "patterns": patterns},
        )

    def log_agent_started(self, state: AgentState, agent_name: str) -> AuditEvent:
        return self._record(state, "AGENT_STARTED", agent_name=agent_name)

    def log_agent_completed(self, state: AgentState, agent_name: str, outcome: str = "SUCCESS") -> AuditEvent:
        return self._record(state, "AGENT_COMPLETED", agent_name=agent_name, outcome=outcome)

    def log_agent_failed(self, state: AgentState, agent_name: str, error_code: str, error_msg: str) -> AuditEvent:
        # truncate error message to avoid leaking sensitive data
        safe_msg = error_msg[:200] if error_msg else ""
        return self._record(
            state, "AGENT_FAILED",
            agent_name=agent_name,
            error_code=error_code,
            error_message=safe_msg,
        )

    def log_policy_validation(self, state: AgentState, result: str) -> AuditEvent:
        return self._record(state, "POLICY_VALIDATION", outcome=result)

    def log_recommendation(self, state: AgentState, action: str) -> AuditEvent:
        return self._record(state, "RECOMMENDATION_CREATED", outcome=action)

    def log_feedback(self, state: AgentState, alert_id: str, disposition: str) -> AuditEvent:
        return self._record(
            state, "FEEDBACK_SUBMITTED",
            resource_type="alert",
            resource_id=alert_id,
            outcome=disposition,
        )

    def log_event(self, state: AgentState, event_type: str, **kwargs: Any) -> AuditEvent:
        """Generic event logging."""
        return self._record(state, event_type, **kwargs)

    # -- retrieval -----------------------------------------------------------

    def get_events(self, request_id: str | None = None, limit: int = 100) -> list[dict]:
        """Read events from the JSONL log, optionally filtered by request_id."""
        if not self._log_path.exists():
            return []
        events = []
        with open(self._log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if request_id and evt.get("request_id") != request_id:
                    continue
                events.append(evt)
            return events[-limit:]

    # -- internals -----------------------------------------------------------

    def _record(
        self,
        state: AgentState,
        event_type: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        agent_name: str | None = None,
        authorization_decision: str | None = None,
        outcome: str | None = None,
        metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            audit_id=str(uuid.uuid4()),
            request_id=state.request_id,
            timestamp=datetime.now(timezone.utc),
            actor_profile_id=state.user_context.profile_id,
            actor_role=state.user_context.role,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            agent_name=agent_name,
            authorization_decision=authorization_decision,
            query_type=state.query_type.value if state.query_type else None,
            outcome=outcome,
            metadata=metadata or {},
            error_code=error_code,
            error_message=error_message,
        )

        # persist to JSONL
        self._append(event)

        # also attach to the in-memory state
        state.audit_events.append(event)

        log.info(
            "audit_event",
            event_type=event_type,
            actor_role=state.user_context.role,
            request_id=state.request_id[:8],
        )
        return event

    def _append(self, event: AuditEvent) -> None:
        try:
            with open(self._log_path, "a") as f:
                f.write(event.model_dump_json() + "\n")
        except OSError as exc:
            log.error("audit_write_failed", error=str(exc))
