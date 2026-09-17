"""
Feedback weighting engine — adjusts alert ranking based on analyst dispositions.

Formula:  new_score = base_score × feedback_multiplier(pattern)

The multipliers are configurable.  Feedback changes ranking/prioritization
only — it cannot modify regulatory truth, sanctions source data, or
authorization rules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from app.feedback.store import FeedbackStore, PatternStats

log = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# defaults if config is missing
_DEFAULT_MULTIPLIERS = {
    "TRUE_HIT": 1.50,
    "FALSE_POSITIVE": 0.70,
    "ESCALATED": 1.20,
}


class FeedbackWeightingEngine:
    """Computes a feedback-adjusted score for alert ranking.

    The engine looks up the pattern key's feedback history and applies
    disposition-weighted multipliers to a base score.  This means alerts
    matching patterns previously confirmed as TRUE_HIT get promoted,
    while FALSE_POSITIVE patterns get demoted.
    """

    def __init__(self, feedback_store: FeedbackStore, multipliers: dict[str, float] | None = None):
        self._store = feedback_store
        self._multipliers = multipliers or self._load_multipliers()

    def compute_weight(self, pattern_key: str) -> float:
        """Return a multiplicative weight for the given pattern.

        Returns 1.0 if no feedback exists for the pattern (neutral).
        """
        stats = self._store.get_pattern_stats(pattern_key)
        if stats.total == 0:
            return 1.0

        # weighted average of disposition multipliers
        weight = 1.0
        m = self._multipliers

        if stats.true_hit_count > 0:
            weight *= m["TRUE_HIT"] ** _dampened_count(stats.true_hit_count)

        if stats.false_positive_count > 0:
            weight *= m["FALSE_POSITIVE"] ** _dampened_count(stats.false_positive_count)

        if stats.escalated_count > 0:
            weight *= m["ESCALATED"] ** _dampened_count(stats.escalated_count)

        return round(weight, 4)

    def adjust_score(self, base_score: float, pattern_key: str) -> float:
        """Apply feedback weight to a base alert score."""
        weight = self.compute_weight(pattern_key)
        adjusted = base_score * weight
        # clamp between 0 and 1
        return round(max(0.0, min(1.0, adjusted)), 4)

    def rank_alerts(self, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Re-rank a list of alert dicts by feedback-adjusted score.

        Each alert dict must have 'score' (float) and 'pattern_key' (str).
        Returns a new list sorted by adjusted_score descending, with
        'adjusted_score', 'feedback_weight', and 'rank' added.
        """
        scored = []
        for alert in alerts:
            base = alert.get("score", 0.5)
            pk = alert.get("pattern_key", "")
            weight = self.compute_weight(pk)
            adjusted = round(max(0.0, min(1.0, base * weight)), 4)
            entry = {**alert, "adjusted_score": adjusted, "feedback_weight": weight}
            scored.append(entry)

        scored.sort(key=lambda a: a["adjusted_score"], reverse=True)

        for rank, entry in enumerate(scored, start=1):
            entry["rank"] = rank

        return scored

    @staticmethod
    def _load_multipliers() -> dict[str, float]:
        cfg_path = _PROJECT_ROOT / "config" / "settings.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            fb_cfg = cfg.get("feedback", {}).get("multipliers", {})
            if fb_cfg:
                return {
                    "TRUE_HIT": float(fb_cfg.get("TRUE_HIT", 1.50)),
                    "FALSE_POSITIVE": float(fb_cfg.get("FALSE_POSITIVE", 0.70)),
                    "ESCALATED": float(fb_cfg.get("ESCALATED", 1.20)),
                }
        return dict(_DEFAULT_MULTIPLIERS)


def _dampened_count(n: int) -> float:
    """Dampen repeated feedback so one rogue analyst can't swing weights wildly.

    Uses sqrt to compress: 1→1, 4→2, 9→3, etc.
    """
    import math
    return math.sqrt(n)
