#!/usr/bin/env python3
"""
CLI entry point for the AML Agentic Compliance System.

Usage:
    python query.py --role AML_ANALYST "Which transactions breach regulatory guidance?"
    python query.py --role CCO --profile PROFILE_01_CCO "Show customer info for CUST_001"
    python query.py --role EXTERNAL_AUDITOR "Show the audit trail"

The system identifies the required data, enforces RBAC, runs the agent
pipeline, and returns an explainable result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

from app.orchestration.orchestrator import Orchestrator
from app.orchestration.state import UserContext

log = structlog.get_logger(__name__)

# shorthand role → profile mapping
_DEFAULT_PROFILES = {
    "CCO": ("PROFILE_01_CCO", True),
    "AML_ANALYST": ("PROFILE_02_AML_ANALYST", False),
    "EXTERNAL_AUDITOR": ("PROFILE_03_EXTERNAL_AUDITOR", False),
    "RELATIONSHIP_MANAGER": ("PROFILE_04_RM", False),
}


def build_user_context(role: str, profile_id: str | None = None, portfolio_id: str | None = None) -> UserContext:
    role = role.upper()
    if profile_id is None:
        profile_id, admin = _DEFAULT_PROFILES.get(role, (f"PROFILE_{role}", False))
    else:
        admin = role == "CCO"

    return UserContext(
        profile_id=profile_id,
        role=role,
        admin=admin,
        portfolio_id=portfolio_id,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AML Agentic Compliance System — CLI Query Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python query.py --role AML_ANALYST "Show flagged transactions"\n'
            '  python query.py --role CCO "What does the RBI guidance say about KYC?"\n'
            '  python query.py --role EXTERNAL_AUDITOR "Show the audit trail"\n'
        ),
    )
    parser.add_argument("query", help="Natural-language compliance question")
    parser.add_argument("--role", required=True, help="User role: CCO, AML_ANALYST, EXTERNAL_AUDITOR, RELATIONSHIP_MANAGER")
    parser.add_argument("--profile", default=None, help="Override profile ID")
    parser.add_argument("--portfolio", default=None, help="Portfolio ID (for RELATIONSHIP_MANAGER)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON state instead of formatted text")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show debug-level logging")

    args = parser.parse_args()

    if not args.verbose:
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.filter_by_level,
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
        )

    user_ctx = build_user_context(args.role, args.profile, args.portfolio)

    print(f"\n{'='*60}")
    print(f"  AML Agentic Compliance System")
    print(f"  Role: {user_ctx.role} | Profile: {user_ctx.profile_id}")
    print(f"{'='*60}")
    print(f"\n  Query: {args.query}\n")

    try:
        orchestrator = Orchestrator()
        state = orchestrator.run(args.query, user_ctx)
    except Exception as exc:
        print(f"\n  ❌ System error: {exc}")
        sys.exit(1)

    if args.json:
        print(json.dumps(state.model_dump(mode="json"), indent=2, default=str))
    else:
        print(f"{'─'*60}")
        print(f"  Status: {state.status.value}")
        print(f"  Stages: {' → '.join(state.completed_stages)}")
        if state.missing_stages:
            print(f"  Missing: {', '.join(state.missing_stages)}")
        print(f"{'─'*60}")
        print()
        print(state.response_text)
        print()

        # show evidence sources
        if state.authorized_data.regulatory_evidence:
            print(f"{'─'*60}")
            print("  📚 Regulatory Sources:")
            for ev in state.authorized_data.regulatory_evidence[:5]:
                print(f"     • {ev.document_id} — {ev.section or 'N/A'} — p.{ev.page or '?'}")

        if state.authorized_data.sanctions_candidates:
            print(f"\n  🔍 Sanctions Candidates:")
            for sc in state.authorized_data.sanctions_candidates[:5]:
                print(f"     • {sc.matched_name} ({sc.match_state.value}, score={sc.match_score:.2f})")

        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
