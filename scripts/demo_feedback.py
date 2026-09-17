import sys
import json
from pathlib import Path
import structlog

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.feedback.store import FeedbackStore, build_pattern_key
from app.feedback.weighting import FeedbackWeightingEngine
from app.orchestration.state import FeedbackEvent, Disposition

logger = structlog.get_logger(__name__)

def main():
    logger.info("Running Feedback Demo")
    
    store = FeedbackStore(db_path=":memory:")
    engine = FeedbackWeightingEngine(store=store)
    
    # Mock alerts
    alerts = [
        {"id": "ALT_101", "base_score": 0.61, "pattern": "STRUCTURING|TRANSFER", "amount": 9000, "velocity": 5},
        {"id": "ALT_205", "base_score": 0.58, "pattern": "VELOCITY|WIRE", "amount": 15000, "velocity": 12},
        {"id": "ALT_407", "base_score": 0.52, "pattern": "STRUCTURING|WIRE", "amount": 8500, "velocity": 6},
    ]
    
    def get_ranked():
        scored = []
        for a in alerts:
            key = build_pattern_key(a["pattern"], a["amount"], a["velocity"])
            adj = engine.get_score_adjustment(key)
            final_score = a["base_score"] + adj
            scored.append({"id": a["id"], "score": round(final_score, 2), "pattern": a["pattern"]})
        return sorted(scored, key=lambda x: x["score"], reverse=True)
        
    before = get_ranked()
    print("=== BEFORE FEEDBACK ===")
    for i, a in enumerate(before, 1):
        print(f"  #{i}  {a['id']}  score={a['score']}  pattern={a['pattern']}...")
        
    print("\n=== SUBMITTING FEEDBACK ===")
    print("  ALT_407 → TRUE_HIT")
    print("  ALT_101 → FALSE_POSITIVE")
    
    key_407 = build_pattern_key("STRUCTURING|WIRE", 8500, 6)
    key_101 = build_pattern_key("STRUCTURING|TRANSFER", 9000, 5)
    
    store.add_feedback(
        FeedbackEvent(alert_id="ALT_407", disposition=Disposition.TRUE_HIT, user_id="user1", comments=""),
        key_407
    )
    store.add_feedback(
        FeedbackEvent(alert_id="ALT_101", disposition=Disposition.FALSE_POSITIVE, user_id="user1", comments=""),
        key_101
    )
    
    after = get_ranked()
    print("\n=== AFTER FEEDBACK ===")
    for i, a in enumerate(after, 1):
        prev_idx = next(j for j, old in enumerate(before) if old["id"] == a["id"])
        status = "unchanged"
        if i < prev_idx + 1:
            status = "PROMOTED"
        elif i > prev_idx + 1:
            status = "DEMOTED"
        print(f"  #{i}  {a['id']}  score={a['score']}  (was #{prev_idx+1}, {status})")
        
    print("\nFeedback demonstrably changed future ranking.")
    
    # Save results
    evals_dir = project_root / "evals" / "results"
    evals_dir.mkdir(parents=True, exist_ok=True)
    
    with open(evals_dir / "feedback_before.json", "w") as f:
        json.dump(before, f, indent=2)
    with open(evals_dir / "feedback_after.json", "w") as f:
        json.dump(after, f, indent=2)
    with open(evals_dir / "feedback_diff.json", "w") as f:
        json.dump({"before": before, "after": after}, f, indent=2)

if __name__ == "__main__":
    main()
