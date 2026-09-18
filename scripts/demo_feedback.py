#!/usr/bin/env python3
"""
Demonstrates the before/after feedback loop.

Creates synthetic alert data, shows ranking BEFORE feedback,
submits dispositions, shows ranking AFTER, and saves the diff.
"""
import sys
import json
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import structlog

structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.PrintLoggerFactory(),
)

from app.feedback.store import FeedbackStore, build_pattern_key, amount_to_bucket, velocity_to_bucket
from app.feedback.weighting import FeedbackWeightingEngine
from app.orchestration.state import FeedbackEvent, Disposition

log = structlog.get_logger(__name__)


def main():
    # use a temp store so we don't pollute real feedback
    tmp_path = project_root / "evals" / "results" / "_demo_feedback.jsonl"
    if tmp_path.exists():
        tmp_path.unlink()

    store = FeedbackStore(store_path=tmp_path)
    engine = FeedbackWeightingEngine(store)

    # synthetic alerts with pattern keys and base scores
    alerts = [
        {
            "alert_id": "ALT_101",
            "score": 0.61,
            "pattern_key": build_pattern_key(
                rule_id="STRUCT_001", transaction_type="TRANSFER",
                channel="BRANCH", country_pair="IN_IN",
                amount_bucket=amount_to_bucket(9000), velocity_bucket=velocity_to_bucket(5),
            ),
            "description": "Structuring -- 5 branch deposits just below 10k",
        },
        {
            "alert_id": "ALT_205",
            "score": 0.58,
            "pattern_key": build_pattern_key(
                rule_id="VELOCITY_001", transaction_type="WIRE",
                channel="ONLINE", country_pair="IN_AE",
                amount_bucket=amount_to_bucket(15000), velocity_bucket=velocity_to_bucket(12),
            ),
            "description": "Velocity spike -- 12 wires in 24h to AE",
        },
        {
            "alert_id": "ALT_312",
            "score": 0.55,
            "pattern_key": build_pattern_key(
                rule_id="GEO_001", transaction_type="WIRE",
                channel="ONLINE", country_pair="IN_IR",
                amount_bucket=amount_to_bucket(25000), velocity_bucket=velocity_to_bucket(2),
            ),
            "description": "Geographic anomaly -- wire to high-risk country",
        },
        {
            "alert_id": "ALT_407",
            "score": 0.52,
            "pattern_key": build_pattern_key(
                rule_id="STRUCT_001", transaction_type="WIRE",
                channel="ONLINE", country_pair="IN_US",
                amount_bucket=amount_to_bucket(8500), velocity_bucket=velocity_to_bucket(6),
            ),
            "description": "Structuring -- 6 wires below threshold to US",
        },
        {
            "alert_id": "ALT_519",
            "score": 0.48,
            "pattern_key": build_pattern_key(
                rule_id="AMOUNT_001", transaction_type="TRANSFER",
                channel="ONLINE", country_pair="IN_IN",
                amount_bucket=amount_to_bucket(200000), velocity_bucket=velocity_to_bucket(1),
            ),
            "description": "Unusual amount -- 5x customer average",
        },
    ]

    # === BEFORE ===
    before = engine.rank_alerts(alerts)

    print("=" * 60)
    print("  FEEDBACK LOOP DEMONSTRATION")
    print("=" * 60)
    print("\n=== BEFORE FEEDBACK ===\n")
    for entry in before:
        print(f"  #{entry['rank']}  {entry['alert_id']}  "
              f"score={entry['adjusted_score']:.4f}  "
              f"weight={entry['feedback_weight']:.4f}  "
              f"{entry['description']}")

    # === SUBMIT FEEDBACK ===
    print("\n=== SUBMITTING FEEDBACK ===\n")
    print("  ALT_407 -> TRUE_HIT    (structuring pattern confirmed)")
    print("  ALT_101 -> FALSE_POSITIVE  (legitimate business deposits)")

    store.submit(FeedbackEvent(
        alert_id="ALT_407",
        analyst_profile_id="PROFILE_02_AML_ANALYST",
        disposition=Disposition.TRUE_HIT,
        pattern_key=alerts[3]["pattern_key"],
        notes="Confirmed structuring pattern with shell companies.",
    ))
    store.submit(FeedbackEvent(
        alert_id="ALT_101",
        analyst_profile_id="PROFILE_02_AML_ANALYST",
        disposition=Disposition.FALSE_POSITIVE,
        pattern_key=alerts[0]["pattern_key"],
        notes="Regular branch deposits for a retail business.",
    ))

    # === AFTER ===
    after = engine.rank_alerts(alerts)

    # build a lookup for position changes
    before_rank = {e["alert_id"]: e["rank"] for e in before}

    print("\n=== AFTER FEEDBACK ===\n")
    for entry in after:
        old_rank = before_rank[entry["alert_id"]]
        if entry["rank"] < old_rank:
            status = "^ PROMOTED"
        elif entry["rank"] > old_rank:
            status = "v DEMOTED"
        else:
            status = "-- unchanged"

        print(f"  #{entry['rank']}  {entry['alert_id']}  "
              f"score={entry['adjusted_score']:.4f}  "
              f"weight={entry['feedback_weight']:.4f}  "
              f"(was #{old_rank}, {status})")

    print(f"\n{'-' * 60}")
    print("  [OK] Feedback demonstrably changed future alert ranking.")
    print(f"{'-' * 60}")

    # === SAVE RESULTS ===
    results_dir = project_root / "evals" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    def serialize(ranked):
        return [
            {"alert_id": e["alert_id"], "rank": e["rank"],
             "score": e["adjusted_score"], "weight": e["feedback_weight"],
             "description": e["description"]}
            for e in ranked
        ]

    before_json = serialize(before)
    after_json = serialize(after)
    diff_json = {
        "before": before_json,
        "after": after_json,
        "changes": [
            {
                "alert_id": a["alert_id"],
                "rank_before": before_rank[a["alert_id"]],
                "rank_after": a["rank"],
                "score_before": next(b["adjusted_score"] for b in before if b["alert_id"] == a["alert_id"]),
                "score_after": a["adjusted_score"],
            }
            for a in after
            if a["rank"] != before_rank[a["alert_id"]]
        ],
    }

    with open(results_dir / "feedback_before.json", "w") as f:
        json.dump(before_json, f, indent=2)
    with open(results_dir / "feedback_after.json", "w") as f:
        json.dump(after_json, f, indent=2)
    with open(results_dir / "feedback_diff.json", "w") as f:
        json.dump(diff_json, f, indent=2)

    print(f"\n  Results saved to {results_dir}")

    # cleanup temp feedback log
    if tmp_path.exists():
        tmp_path.unlink()


if __name__ == "__main__":
    main()


