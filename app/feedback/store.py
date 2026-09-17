"""
Feedback store — persists analyst dispositions and pattern statistics.

Feedback is stored as JSONL events and aggregated into pattern-level
statistics that the FeedbackWeightingEngine uses for ranking.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.orchestration.state import Disposition, FeedbackEvent

log = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FEEDBACK_DIR = _PROJECT_ROOT / "data" / "processed"


def build_pattern_key(
    rule_id: str = "",
    transaction_type: str = "",
    channel: str = "",
    country_pair: str = "",
    amount_bucket: str = "",
    velocity_bucket: str = "",
    counterparty_pattern: str = "",
) -> str:
    """Deterministic pattern key for grouping similar alerts."""
    parts = [
        rule_id or "UNKNOWN",
        transaction_type or "UNKNOWN",
        channel or "UNKNOWN",
        country_pair or "UNKNOWN",
        amount_bucket or "UNKNOWN",
        velocity_bucket or "UNKNOWN",
        counterparty_pattern or "UNKNOWN",
    ]
    return "|".join(parts)


def amount_to_bucket(amount: float) -> str:
    if amount < 1000:
        return "MICRO"
    if amount < 10000:
        return "SMALL"
    if amount < 50000:
        return "MEDIUM"
    if amount < 200000:
        return "LARGE"
    return "VERY_LARGE"


def velocity_to_bucket(txn_count_24h: int) -> str:
    if txn_count_24h <= 3:
        return "LOW"
    if txn_count_24h <= 10:
        return "MEDIUM"
    return "HIGH"


class PatternStats:
    """Aggregated feedback statistics for one pattern."""

    def __init__(self):
        self.true_hit_count: int = 0
        self.false_positive_count: int = 0
        self.escalated_count: int = 0

    @property
    def total(self) -> int:
        return self.true_hit_count + self.false_positive_count + self.escalated_count

    def to_dict(self) -> dict:
        return {
            "true_hit_count": self.true_hit_count,
            "false_positive_count": self.false_positive_count,
            "escalated_count": self.escalated_count,
            "total": self.total,
        }


class FeedbackStore:
    """Append-only feedback log with pattern-level aggregation."""

    def __init__(self, store_path: Path | None = None):
        self._store_path = store_path or (_FEEDBACK_DIR / "feedback_log.jsonl")
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._patterns: dict[str, PatternStats] = defaultdict(PatternStats)
        self._events: list[FeedbackEvent] = []
        self._load_existing()

    def submit(self, event: FeedbackEvent) -> None:
        """Record an analyst disposition and update pattern stats."""
        # append to log
        with open(self._store_path, "a") as f:
            f.write(event.model_dump_json() + "\n")

        self._events.append(event)

        # update pattern stats
        if event.pattern_key:
            self._update_pattern(event.pattern_key, event.disposition)

        log.info(
            "feedback_submitted",
            alert_id=event.alert_id,
            disposition=event.disposition.value,
            pattern_key=event.pattern_key,
        )

    def get_pattern_stats(self, pattern_key: str) -> PatternStats:
        return self._patterns.get(pattern_key, PatternStats())

    def get_all_patterns(self) -> dict[str, PatternStats]:
        return dict(self._patterns)

    def get_feedback_for_alert(self, alert_id: str) -> list[FeedbackEvent]:
        return [e for e in self._events if e.alert_id == alert_id]

    def get_all_events(self) -> list[FeedbackEvent]:
        return list(self._events)

    def clear(self) -> None:
        """Reset all feedback (useful for testing)."""
        self._patterns.clear()
        self._events.clear()
        if self._store_path.exists():
            self._store_path.unlink()

    def _update_pattern(self, pattern_key: str, disposition: Disposition) -> None:
        stats = self._patterns[pattern_key]
        if disposition == Disposition.TRUE_HIT:
            stats.true_hit_count += 1
        elif disposition == Disposition.FALSE_POSITIVE:
            stats.false_positive_count += 1
        elif disposition == Disposition.ESCALATED:
            stats.escalated_count += 1

    def _load_existing(self) -> None:
        """Replay the JSONL log to rebuild in-memory state."""
        if not self._store_path.exists():
            return
        with open(self._store_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = FeedbackEvent.model_validate(data)
                    self._events.append(event)
                    if event.pattern_key:
                        self._update_pattern(event.pattern_key, event.disposition)
                except Exception as exc:
                    log.warning("feedback_parse_error", error=str(exc)[:100])
