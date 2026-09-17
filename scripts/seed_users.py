import sys
import json
from pathlib import Path
import structlog

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

logger = structlog.get_logger(__name__)

PROFILES = [
    {"profile_id": "PROFILE_01_CCO", "role": "CCO", "admin": True},
    {"profile_id": "PROFILE_02_AML_ANALYST", "role": "AML_ANALYST", "admin": False},
    {"profile_id": "PROFILE_03_EXTERNAL_AUDITOR", "role": "EXTERNAL_AUDITOR", "admin": False},
    {"profile_id": "PROFILE_04_RM", "role": "RELATIONSHIP_MANAGER", "admin": False, "portfolio_id": "RM_001"},
]

def main():
    processed_dir = project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    
    out_path = processed_dir / "user_profiles.json"
    logger.info("Saving user profiles", path=str(out_path))
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(PROFILES, f, indent=2)
        
    logger.info("Successfully seeded users.")

if __name__ == "__main__":
    main()
