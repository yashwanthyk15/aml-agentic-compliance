# Security Threat Model

## Threat Inventory

| ID | Threat | Attack Surface | Control |
|----|--------|---------------|---------|
| T1 | Unauthorized PII request | User query | RBAC data filtering before retrieval |
| T2 | Model accidentally receives PII | LLM context | Minimum-context construction; field-level masking |
| T3 | Prompt injection in transaction narrative | Counterparty name, remittance text | Trust boundary labeling + injection detector |
| T4 | Prompt injection in user query | Query input | Query sanitization + injection scanning |
| T5 | Prompt injection in regulatory document | PDF text extraction | Source-as-data policy; trust labels |
| T6 | Indirect inference leak | Aggregation queries | Minimum-group-size suppression |
| T7 | Cross-portfolio access | RM querying another RM's data | Resource scope filtering before retrieval |
| T8 | Sanctions false positive escalation | Fuzzy matching | Multi-state matching (POTENTIAL vs CONFIRMED) |
| T9 | Regulatory hallucination | LLM generation | Source-only answers + citation requirement |
| T10 | Stale regulatory version | Multiple document versions | Versioned retrieval with effective_date |
| T11 | Agent failure propagation | LLM API errors | Retry + controlled degradation |
| T12 | Audit log PII leakage | Error messages, metadata | Sanitized logging; no raw PII in audit |
| T13 | Feedback poisoning | Malicious analyst dispositions | Dampened weighting (√n); bounded influence |
| T14 | Duplicate feedback manipulation | Repeated submissions | Idempotent pattern-key based aggregation |
| T15 | LLM API outage | Provider unavailability | Retry with backoff + MANUAL_REVIEW fallback |

## Defense-in-Depth Layers

### Layer 1: Authorization (before retrieval)
- Role checked against config/roles.yaml
- Resource-type permission checked
- Portfolio scope applied for RELATIONSHIP_MANAGER
- Denied requests never reach the data layer

### Layer 2: Data Filtering (before context)
- Only authorized records are queried
- DENY fields are excluded from query results entirely
- MASK_PARTIAL fields are transformed before inclusion
- Transaction filtering: flagged_only for analysts, scoped for RMs

### Layer 3: Trust Boundary (before LLM)
- Every text field receives a trust label
- Transaction narratives: ATTACKER_CONTROLLED_DATA
- Regulatory text: TRUSTED_SOURCE
- User queries: USER_INPUT
- System prompts: TRUSTED_SYSTEM

### Layer 4: Injection Detection
- Regex pattern matching for known injection phrases
- Audit event logged when injection detected
- Content preserved as evidence but instructions not executed

### Layer 5: Output Validation
- Pydantic schema validation on LLM responses
- Policy validator checks for safety violations
- Conflicting/missing evidence triggers MANUAL_REVIEW

### Layer 6: Audit Trail
- Every authorization decision logged
- Every agent invocation logged
- Every PII masking event logged
- Injection events logged
- No raw restricted PII in any log

## Key Design Decisions

### Why not "retrieve then hide"?
If restricted data enters the LLM context, the LLM might leak it through:
- Direct inclusion in the response
- Indirect inference ("the customer with account ending in 3728...")
- Side channels in structured output

By never retrieving restricted data, these attack vectors don't exist.

### Why deterministic injection detection + trust boundary?
The injection detector alone is bypassable (novel injection patterns). The actual security property is the trust boundary: even if detection misses a payload, the content is labeled as ATTACKER_CONTROLLED_DATA and the LLM is instructed to treat it as data, not instructions. Combined with output validation, this provides defense-in-depth.

### Why dampened feedback weights?
Without dampening, a single analyst submitting 100 FALSE_POSITIVE dispositions on the same pattern could effectively suppress all alerts matching that pattern. The √n dampening means: 1 feedback → 1× effect, 4 → 2×, 9 → 3×, etc. This bounds the maximum influence of any individual.
