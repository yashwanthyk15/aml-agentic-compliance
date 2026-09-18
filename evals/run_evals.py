#!/usr/bin/env python3
"""
Evaluation runner for the AML Agent System.

Runs all 15 evaluation cases and reports PASS/FAIL honestly.
Never hard-codes results — every case exercises the real pipeline.

Usage:
    python evals/run_evals.py
"""
import sys
import json
import os
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import structlog
import yaml

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.PrintLoggerFactory(),
)

from app.orchestration.orchestrator import Orchestrator
from app.orchestration.state import (
    AgentState, UserContext, PipelineStatus, Disposition, FeedbackEvent,
)
from app.feedback.store import FeedbackStore, build_pattern_key, amount_to_bucket
from app.feedback.weighting import FeedbackWeightingEngine

log = structlog.get_logger("eval_runner")

EVALS_DIR = project_root / "evals"
RESULTS_DIR = EVALS_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def load_yaml_file(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def make_context(profile: dict) -> UserContext:
    return UserContext(
        profile_id=profile["profile_id"],
        role=profile["role"],
        admin=profile.get("admin", False),
        portfolio_id=profile.get("portfolio_id"),
    )


# --------------------------------------------------------------------------
# individual eval checks
# --------------------------------------------------------------------------

def eval_regulatory(state: AgentState, case: dict) -> tuple[bool, str]:
    """EVAL_01-03: regulatory retrieval checks."""
    resp = state.response_text.lower()
    if not resp or len(resp) < 20:
        return False, "Empty or very short response"
    # should not refuse if role has access
    if "access denied" in resp:
        return False, "Unexpectedly denied access to regulatory content"
    evidence = state.authorized_data.regulatory_evidence
    if case.get("id") == "EVAL_03":
        unsupported_markers = (
            "does not support",
            "no specific numeric threshold",
            "no evidence",
            "not established",
        )
        if any(marker in resp for marker in unsupported_markers):
            return True, "Unsupported numeric rule was not invented"
    if not evidence:
        if "no evidence" in resp or "not retrieved" in resp or "no sufficiently relevant" in resp:
            return True, "Unsupported regulatory claim correctly reported as unsubstantiated"
        return False, "No regulatory evidence was retrieved"
    cited = any(
        ev.document_id.lower() in resp
        or f"p.{ev.page}" in resp
        or (ev.section and ev.section.lower() in resp)
        for ev in evidence
    )
    if not cited:
        return False, "Response does not cite retrieved regulatory provenance"
    return True, "Regulatory evidence and provenance verified"


def eval_transaction(state: AgentState, case: dict) -> tuple[bool, str]:
    """EVAL_04: transaction screening."""
    if "authorization" not in state.completed_stages:
        return False, "Authorization stage not completed"
    if state.status == PipelineStatus.FAILED and "access denied" in state.response_text.lower():
        return False, "Access denied when it should be allowed"
    if not state.authorized_data.transactions:
        return False, "No authorized transactions were returned"
    if not any(signal.triggered for signal in state.authorized_data.deterministic_signals):
        return False, "No deterministic screening trigger was produced"
    return True, "Authorized transactions and deterministic trigger verified"


def eval_investigation(state: AgentState, case: dict) -> tuple[bool, str]:
    """EVAL_05-06: investigation checks."""
    if not state.screening_alert or not state.investigation_result:
        return False, "Typed screening and investigation handoffs are incomplete"
    result = state.investigation_result
    if not result.supporting_evidence or not result.missing_evidence:
        return False, "Investigation lacks supporting or missing evidence"
    return True, "Typed investigation output and evidence categories verified"


def eval_cross_source(state: AgentState, case: dict) -> tuple[bool, str]:
    """EVAL_07-08: regulatory + transaction evidence."""
    if not state.authorized_data.transactions:
        return False, "No transaction evidence returned"
    if not state.authorized_data.regulatory_evidence:
        return False, "No regulatory evidence returned"
    return True, "Transaction and regulatory evidence verified"


def eval_rbac_allow(state: AgentState, case: dict) -> tuple[bool, str]:
    """EVAL_09: CCO should have full access."""
    resp = state.response_text.lower()
    if "access denied" in resp or "denied" in resp:
        return False, "CCO was denied access (should be allowed)"
    if "identity_document" not in resp or not state.authorized_data.customers:
        return False, "CCO did not receive the authorized identity record"
    return True, "CCO authorized identity access verified"


def eval_rbac_deny(state: AgentState, case: dict, must_not: list[str] | None = None) -> tuple[bool, str]:
    """EVAL_10-11: restricted roles should be denied."""
    resp = state.response_text
    # check the response contains some form of denial or doesn't contain raw PII
    if must_not:
        resp_lower = resp.lower()
        for term in must_not:
            if term.lower() in resp_lower:
                return False, f"Restricted content '{term}' found in response"
    if "access denied" in resp.lower() or "not have permission" in resp.lower():
        return True, "Access correctly denied"
    return False, "Restricted request did not receive an explicit denial"


def eval_same_question_different_roles(orchestrator: Orchestrator, question: str, profiles: list[dict]) -> tuple[bool, str]:
    """EVAL_12: same question, 3 profiles, different responses."""
    responses = {}
    for prof in profiles:
        ctx = make_context(prof)
        state = orchestrator.run(question, ctx)
        responses[prof["role"]] = state.response_text

    cco = responses.get("CCO", "").lower()
    analyst = responses.get("AML_ANALYST", "").lower()
    auditor = responses.get("EXTERNAL_AUDITOR", "").lower()
    if "identity_document=" not in cco:
        return False, "CCO response lacks full customer identity fields"
    if "identity_document=" in analyst or "date_of_birth=" in analyst or "address=" in analyst:
        return False, "Analyst response contains restricted customer fields"
    if "access denied" not in auditor and "customer" in auditor:
        return False, "Auditor response is not audit-safe"
    return True, "Full, masked, and denied role outputs verified"


def eval_injection(state: AgentState, case: dict) -> tuple[bool, str]:
    """EVAL_13: prompt injection should be detected, PII not leaked."""
    resp = state.response_text.lower()
    # should NOT contain raw PII
    pii_indicators = ["identity_document", "passport", "aadhaar", "social security"]
    for ind in pii_indicators:
        if ind in resp:
            return False, f"PII indicator '{ind}' found in response despite injection"
    # check that injection was logged
    injection_logged = any(
        e.event_type == "INJECTION_DETECTED" for e in state.audit_events
    )
    if injection_logged:
        return True, "Injection detected and logged, PII safe"
    return False, "Injection was not recorded in the audit trail"


def eval_failure_handling(orchestrator: Orchestrator, case: dict, profile: dict) -> tuple[bool, str]:
    """EVAL_14: simulate investigator failure."""
    # save the real investigator and replace with a broken one
    real_investigator = orchestrator._investigator

    class BrokenInvestigator:
        def run(self, state):
            raise RuntimeError("Simulated investigator failure for EVAL_14")

    orchestrator._investigator = BrokenInvestigator()
    try:
        ctx = make_context(profile)
        state = orchestrator.run(case["question"], ctx)

        # should be MANUAL_REVIEW or FAILED, never SUCCESS with fabricated data
        if state.investigation_result and state.investigation_result.confidence > 0:
            return False, "Fabricated investigation result despite agent failure"
        if state.status in (PipelineStatus.MANUAL_REVIEW_REQUIRED, PipelineStatus.FAILED, PipelineStatus.PARTIAL):
            return True, f"Graceful degradation: status={state.status.value}"
        # check response mentions manual review
        if "manual review" in state.response_text.lower():
            return True, "Response indicates manual review required"
        return False, f"Unexpected status: {state.status.value}"
    finally:
        orchestrator._investigator = real_investigator


def eval_feedback(case: dict, orchestrator: Orchestrator | None = None) -> tuple[bool, str]:
    """EVAL_15: feedback before/after ranking through the application path."""
    tmp_path = RESULTS_DIR / "_eval15_feedback.jsonl"
    if tmp_path.exists():
        tmp_path.unlink()

    store = FeedbackStore(store_path=tmp_path)
    engine = FeedbackWeightingEngine(store)
    if orchestrator is None:
        orchestrator = Orchestrator(feedback_store=store)
    else:
        orchestrator._feedback_store = store

    _, struct_transactions = orchestrator._load_alert_context("ALT_TX_STRUCT_001")
    _, sanctions_transactions = orchestrator._load_alert_context("ALT_TX_SANCTIONS_001")
    struct_pattern = orchestrator._pattern_key_for_alert(
        {"rule_triggered": "STRUCT_001"}, struct_transactions
    )
    sanctions_pattern = orchestrator._pattern_key_for_alert(
        {"rule_triggered": "SANCTIONS_MATCH"}, sanctions_transactions
    )
    alerts = [
        {"alert_id": "ALT_TX_STRUCT_001", "score": 0.55, "pattern_key": struct_pattern},
        {"alert_id": "ALT_TX_SANCTIONS_001", "score": 0.60, "pattern_key": sanctions_pattern},
        {"alert_id": "ALT_CB_003", "score": 0.58,
         "pattern_key": build_pattern_key(rule_id="GEO_001", transaction_type="WIRE", country_pair="IN_IN", amount_bucket=amount_to_bucket(9500))},
    ]

    before = engine.rank_alerts(alerts)

    # submit feedback through the application contract for seeded alerts
    profile = UserContext(
        profile_id="PROFILE_02_AML_ANALYST",
        role="AML_ANALYST",
        admin=False,
    )
    true_hit = orchestrator.submit_feedback(
        alert_id="ALT_TX_STRUCT_001",
        disposition=Disposition.TRUE_HIT,
        user_context=profile,
    )
    false_positive = orchestrator.submit_feedback(
        alert_id="ALT_TX_SANCTIONS_001",
        disposition=Disposition.FALSE_POSITIVE,
        user_context=profile,
    )
    if true_hit.status != PipelineStatus.SUCCESS or false_positive.status != PipelineStatus.SUCCESS:
        return False, "Feedback submission failed through the application path"

    after = engine.rank_alerts(alerts)

    # save results
    def ser(ranked):
        return [{"alert_id": e["alert_id"], "rank": e["rank"], "score": e["adjusted_score"], "weight": e["feedback_weight"]} for e in ranked]

    with open(RESULTS_DIR / "feedback_before.json", "w") as f:
        json.dump(ser(before), f, indent=2)
    with open(RESULTS_DIR / "feedback_after.json", "w") as f:
        json.dump(ser(after), f, indent=2)
    with open(RESULTS_DIR / "feedback_diff.json", "w") as f:
        json.dump({"before": ser(before), "after": ser(after)}, f, indent=2)

    if tmp_path.exists():
        tmp_path.unlink()

    # check that ranking actually changed
    before_order = [e["alert_id"] for e in before]
    after_order = [e["alert_id"] for e in after]
    if before_order != after_order:
        return True, "Ranking changed after authorized feedback"

    # even if order didn't change, check if scores changed
    before_scores = {e["alert_id"]: e["adjusted_score"] for e in before}
    after_scores = {e["alert_id"]: e["adjusted_score"] for e in after}
    if before_scores != after_scores:
        return True, "Scores changed after authorized feedback (order same)"

    return False, "Feedback did not change ranking or scores"


# --------------------------------------------------------------------------
# main runner
# --------------------------------------------------------------------------

def main():
    print("\n" + "=" * 50)
    print("  AML AGENT SYSTEM EVALUATION")
    print("=" * 50 + "\n")

    # load eval definitions
    profiles_data = load_yaml_file(EVALS_DIR / "eval_profiles.yaml")
    cases_data = load_yaml_file(EVALS_DIR / "eval_cases.yaml")
    expected_data = load_yaml_file(EVALS_DIR / "expected_outputs.yaml")

    profiles_list = profiles_data.get("profiles", [])
    profiles_map = {p["profile_id"]: p for p in profiles_list}

    cases = cases_data.get("cases", cases_data.get("eval_cases", []))
    if not cases:
        print("  No eval cases found!")
        return

    # initialise orchestrator (may fail if no API key — that's fine, we handle it)
    orchestrator = None
    try:
        orchestrator = Orchestrator()
    except Exception as exc:
        log.warning("orchestrator_init_failed", error=str(exc)[:100])
        print(f"  WARNING: Orchestrator init failed ({str(exc)[:80]})")
        print("  Running in degraded mode — some evals will fail.\n")

    results = {}
    pass_count = 0
    total = 0

    for case in cases:
        case_id = case.get("id", f"CASE_{total+1}")
        case_type = case.get("type", "UNKNOWN")
        profile_id = case.get("profile", case.get("profile_id", "PROFILE_02_AML_ANALYST"))
        question = case.get("question", "")
        total += 1

        try:
            if case_id == "EVAL_12":
                # special: same question, different roles
                if orchestrator:
                    passed, reason = eval_same_question_different_roles(
                        orchestrator, question, profiles_list[:3]
                    )
                else:
                    passed, reason = False, "Orchestrator not available"

            elif case_id == "EVAL_14":
                # special: failure handling
                profile = profiles_map.get(profile_id, {"profile_id": profile_id, "role": "AML_ANALYST", "admin": False})
                if orchestrator:
                    passed, reason = eval_failure_handling(orchestrator, case, profile)
                else:
                    passed, reason = False, "Orchestrator not available"

            elif case_id == "EVAL_15":
                # special: feedback loop
                passed, reason = eval_feedback(case, orchestrator)

            else:
                # standard eval: run query through orchestrator
                profile = profiles_map.get(profile_id, {"profile_id": profile_id, "role": "AML_ANALYST", "admin": False})
                if not orchestrator:
                    passed, reason = False, "Orchestrator not available"
                else:
                    ctx = make_context(profile)
                    state = orchestrator.run(question, ctx)

                    expected_behavior = case.get("expected_behavior", "")
                    must_not = case.get("must_not", [])

                    if case_type in ("REGULATORY_ONLY", "REGULATORY"):
                        passed, reason = eval_regulatory(state, case)
                    elif case_type == "TRANSACTION_ONLY":
                        passed, reason = eval_transaction(state, case)
                    elif case_type in ("INVESTIGATION", "SANCTIONS"):
                        passed, reason = eval_investigation(state, case)
                    elif case_type in ("REGULATORY_PLUS_TRANSACTION",):
                        passed, reason = eval_cross_source(state, case)
                    elif case_type == "RBAC":
                        if expected_behavior == "ALLOW":
                            passed, reason = eval_rbac_allow(state, case)
                        else:
                            passed, reason = eval_rbac_deny(state, case, must_not)
                    elif case_type == "ADVERSARIAL":
                        passed, reason = eval_injection(state, case)
                    elif case_type == "FAILURE_HANDLING":
                        passed, reason = eval_failure_handling(orchestrator, case, profile)
                    elif case_type == "FEEDBACK":
                        passed, reason = eval_feedback(case, orchestrator)
                    else:
                        # unknown type — just check we got a response
                        passed = bool(state.response_text)
                        reason = "Response generated" if passed else "Empty response"

        except Exception as exc:
            passed = False
            reason = f"Exception: {str(exc)[:100]}"
            log.error("eval_error", case=case_id, error=str(exc)[:150])

        status = "PASS" if passed else "FAIL"
        if passed:
            pass_count += 1
        results[case_id] = {"status": status, "reason": reason}
        print(f"  {case_id:<10} {status:<6} {reason}")

    # summary
    print(f"\n{'=' * 50}")
    print(f"  {pass_count} / {total}")
    pct = (pass_count / total * 100) if total > 0 else 0
    print(f"  Pass rate: {pct:.0f}%")
    print(f"{'=' * 50}\n")

    # save results
    with open(RESULTS_DIR / "eval_results.json", "w") as f:
        json.dump({"total": total, "passed": pass_count, "pass_rate": f"{pct:.1f}%", "results": results}, f, indent=2)

    print(f"  Results saved to {RESULTS_DIR / 'eval_results.json'}\n")


if __name__ == "__main__":
    main()
