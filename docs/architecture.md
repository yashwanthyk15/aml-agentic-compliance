# Architecture Overview

## System Design

The AML Agentic Compliance System is designed around five principles:

1. **Security before intelligence** — Authorization happens before data retrieval, not after
2. **Source before model** — Regulatory truth comes from documents, not LLM memory
3. **Evidence before conclusion** — Every recommendation has traceable provenance
4. **LLM as reasoning layer, not authority** — Deterministic systems enforce policy
5. **Minimal context** — Agents receive only authorized, relevant data

## Pipeline Flow

```
                     ┌───────────────────────┐
                     │         USER          │
                     └───────────┬───────────┘
                                │
                                ▼
                ┌──────────────────────────────┐
                │ AUTH / RBAC / RESOURCE SCOPE  │
                │ FIELD FILTERING / PII MASK    │
                └──────────────┬───────────────┘
                               │
                               ▼
                     ┌──────────────────┐
                     │   QUERY ROUTER   │
                     │  (deterministic) │
                     └────────┬─────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
  Regulatory RAG       Transaction Data       Audit/Scope
         │                    │
         └────────────┬───────┘
                      │
                      ▼
             ┌────────────────────┐
             │ AGENT 1: SCREENER  │
             └──────────┬─────────┘
                        │ ScreeningAlert (Pydantic)
                        ▼
            ┌────────────────────────┐
            │ AGENT 2: INVESTIGATOR  │
            └───────────┬────────────┘
                        │ InvestigationResult (Pydantic)
                        ▼
            ┌─────────────────────────┐
            │ AGENT 3: RECOMMENDER    │
            └────────────┬────────────┘
                         │ Recommendation (Pydantic)
                         ▼
             ┌────────────────────────┐
             │   POLICY VALIDATOR     │
             │ (deterministic, no LLM)│
             └───────────┬────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         SAFE RESULT           MANUAL REVIEW
              │
              ▼
     EXPLAINABLE RESPONSE
              │
              ▼
     ANALYST FEEDBACK → FeedbackWeightingEngine → Future Ranking
```

## Component Responsibilities

### Deterministic (No LLM)

| Component | Responsibility |
|-----------|---------------|
| RBAC Engine | Role-based authorization from YAML config |
| PII Masking | Field-level masking (ALLOW/MASK_PARTIAL/DENY) |
| Query Router | Classify query type via regex patterns |
| Screening Rules | Structuring, velocity, geographic, amount anomaly detection |
| Sanctions Matcher | Exact + fuzzy name matching via rapidfuzz |
| Policy Validator | Safety checks on recommendations |
| Audit Logger | Append-only event recording |
| Feedback Store | Disposition persistence and pattern aggregation |
| Injection Detector | Regex-based injection pattern scanning |

### LLM-Powered

| Component | Responsibility |
|-----------|---------------|
| Compliance Screener | Analyze evidence, identify suspicious indicators |
| Investigation Agent | Evaluate supporting/contradictory/missing evidence |
| Recommendation Engine | Produce constrained recommendation (ESCALATE/NO_ACTION/MANUAL_REVIEW) |

## Data Flow: Security Boundary

The critical design decision: **unauthorized data never enters the LLM context**.

```
WRONG:  retrieve all data → send to LLM → tell LLM to hide restricted fields
RIGHT:  authorize → filter → mask → build safe context → send to LLM
```

This means even if the LLM is compromised or jailbroken, it cannot reveal data it never received.

## Agent Handoff Contract

Agents communicate through typed Pydantic models, not text:

```python
# Agent 1 produces
ScreeningAlert(
    alert_id="ALT_TX123",
    triggered_indicators=["structuring", "velocity"],
    confidence=0.87,
    regulatory_evidence=[...],
    sanctions_matches=[...]
)

# Agent 2 consumes ScreeningAlert, produces
InvestigationResult(
    alert_id="ALT_TX123",
    supporting_evidence=["5 transactions below 50k in 24h"],
    contradictory_evidence=["customer is a registered business"],
    missing_evidence=["counterparty verification pending"],
    confidence=0.81
)

# Agent 3 consumes both, produces
Recommendation(
    suggested_action=ESCALATE,
    rationale="Strong structuring pattern with regulatory applicability",
    confidence=0.91
)
```

## Failure Handling

Every agent call may fail. The system degrades gracefully:

```
Agent call → success? → continue
                  ↓ no
             retry (max 2, exponential backoff)
                  ↓ still failing
             controlled degradation (MANUAL_REVIEW, no fabricated results)
```

A failed investigator produces `MANUAL_REVIEW_REQUIRED`, not a fake investigation.

## Sanctions Matching

The sanctions subsystem is deterministic-first:

```
normalize name → exact lookup → alias lookup → fuzzy match → candidate scoring
```

Match states:
- `NO_MATCH` — below threshold
- `POTENTIAL_MATCH` — fuzzy score ≥ 0.85
- `STRONG_POTENTIAL_MATCH` — fuzzy score ≥ 0.90
- `CONFIRMED_SOURCE_SUPPORTED_MATCH` — requires identifier/DOB confirmation

A fuzzy name match alone **cannot** create a confirmed hit.

## Feedback Engine

```
analyst disposition → pattern key → weighted multiplier → future ranking

Pattern key: rule_id|txn_type|channel|country_pair|amount_bucket|velocity_bucket|counterparty_pattern

TRUE_HIT      → 1.50× (promote similar patterns)
FALSE_POSITIVE → 0.70× (demote similar patterns)
ESCALATED     → 1.20× (more attention, not confirmation)
```

Feedback uses dampened counting (`√n`) to prevent a single analyst from wildly swinging weights.
