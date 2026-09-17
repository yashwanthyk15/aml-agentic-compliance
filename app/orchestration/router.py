"""
Query router — deterministic classification of user queries.

The router decides which data paths and agent capabilities are needed.
It does NOT make authorization decisions (that's RBAC's job).
"""
from __future__ import annotations

import re

import structlog

from app.orchestration.state import QueryType

log = structlog.get_logger(__name__)

# keyword → query-type mapping, checked in priority order
_PATTERNS: list[tuple[QueryType, list[str]]] = [
    (QueryType.FEEDBACK, [
        r"\bfeedback\b", r"\bdisposition\b", r"\btrue.?hit\b",
        r"\bfalse.?positive\b", r"\bescalat",
    ]),
    (QueryType.ACCESS_REQUEST, [
        r"\baccess\b.*\brequest\b", r"\bpermission\b", r"\brole\b.*\baccess\b",
    ]),
    (QueryType.AUDIT, [
        r"\baudit\b", r"\baudit.?trail\b", r"\baudit.?log\b",
    ]),
    (QueryType.SANCTIONS, [
        r"\bsanction", r"\bofac\b", r"\bsdn\b", r"\bwatchlist\b",
        r"\bdesignated\b.*\bnational",
    ]),
    (QueryType.INVESTIGATION, [
        r"\binvestigat", r"\balt_", r"\balert\b.*\b(summariz|detail|review)",
        r"\bevidence\b.*\b(supporting|contradictory|missing)",
    ]),
]

# These keywords suggest regulatory content is needed
_REGULATORY_SIGNALS = [
    r"\bregulat", r"\bguidance\b", r"\bcompliance\b", r"\bobligation\b",
    r"\bfatf\b", r"\brbi\b", r"\bkyc\b", r"\baml\b", r"\bcdd\b",
    r"\bdue.?diligence\b", r"\brisk.?based\b", r"\bmaster.?direction\b",
    r"\bthreshold\b", r"\brecommendation\b.*\bfatf",
]

# These keywords suggest transaction data is needed
_TRANSACTION_SIGNALS = [
    r"\btransaction", r"\bsuspicious\b", r"\bflagged\b", r"\bstructuring\b",
    r"\bvelocity\b", r"\bcross.?border\b", r"\bwire\b", r"\btransfer\b",
    r"\bamount\b", r"\bcounterpart", r"\bthis month\b", r"\bbreach",
    r"\btrigger",
]


def classify_query(query: str) -> QueryType:
    """Classify a natural-language query into a QueryType.

    The classification is deterministic — no LLM involved.
    """
    q = query.lower().strip()

    # check special-purpose patterns first
    for qtype, patterns in _PATTERNS:
        if any(re.search(p, q) for p in patterns):
            log.debug("query_classified", query_type=qtype.value, trigger="pattern")
            return qtype

    has_reg = any(re.search(p, q) for p in _REGULATORY_SIGNALS)
    has_txn = any(re.search(p, q) for p in _TRANSACTION_SIGNALS)

    if has_reg and has_txn:
        qtype = QueryType.REGULATORY_PLUS_TRANSACTION
    elif has_reg:
        qtype = QueryType.REGULATORY_ONLY
    elif has_txn:
        qtype = QueryType.TRANSACTION_ONLY
    else:
        # default to regulatory — safer than guessing transaction
        qtype = QueryType.REGULATORY_ONLY

    log.debug(
        "query_classified",
        query_type=qtype.value,
        has_regulatory=has_reg,
        has_transaction=has_txn,
    )
    return qtype
