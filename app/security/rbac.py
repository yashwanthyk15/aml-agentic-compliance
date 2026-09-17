import yaml
from pathlib import Path
from enum import Enum
from pydantic import BaseModel
import structlog
from typing import Optional, Dict

logger = structlog.get_logger(__name__)

class FieldAccessLevel(Enum):
    ALLOW = "ALLOW"
    MASK_PARTIAL = "MASK_PARTIAL"
    DENY = "DENY"
    ALLOW_SCOPED = "ALLOW_SCOPED"

class AuthorizationDecision(BaseModel):
    allowed: bool
    reason: str
    resource_type: str
    access_level: str

class RBACEngine:
    def __init__(self):
        self._roles: Dict = {}
        self._pii_fields: Dict = {}
        self._load_configs()

    def _get_project_root(self) -> Path:
        current_dir = Path(__file__).parent
        while current_dir != current_dir.parent:
            if (current_dir / "config").is_dir():
                return current_dir
            current_dir = current_dir.parent
        # Fallback if config is not found
        return Path(__file__).parent.parent.parent

    def _load_configs(self):
        root = self._get_project_root()
        roles_path = root / "config" / "roles.yaml"
        pii_path = root / "config" / "pii_fields.yaml"

        if roles_path.exists():
            with open(roles_path, "r", encoding="utf-8") as f:
                self._roles = yaml.safe_load(f) or {}
        else:
            logger.warning("roles.yaml not found", path=str(roles_path))
            
        if pii_path.exists():
            with open(pii_path, "r", encoding="utf-8") as f:
                self._pii_fields = yaml.safe_load(f) or {}
        else:
            logger.warning("pii_fields.yaml not found", path=str(pii_path))

    def authorize(self, user_role: str, resource_type: str, resource_id: Optional[str] = None, portfolio_id: Optional[str] = None) -> AuthorizationDecision:
        role_config = self._roles.get(user_role, {})
        resources = role_config.get("resources", {})
        
        if resource_type not in resources:
            return AuthorizationDecision(allowed=False, reason="Resource type not permitted", resource_type=resource_type, access_level="DENY")
        
        access_level = resources[resource_type]
        
        if access_level == "ALLOW":
            return AuthorizationDecision(allowed=True, reason="Full access granted", resource_type=resource_type, access_level=access_level)
        elif access_level == "ALLOW_SCOPED":
            return AuthorizationDecision(allowed=True, reason="Scoped access granted", resource_type=resource_type, access_level=access_level)
        
        return AuthorizationDecision(allowed=False, reason="Access denied", resource_type=resource_type, access_level="DENY")

    def get_field_access(self, user_role: str, field_name: str) -> FieldAccessLevel:
        role_config = self._roles.get(user_role, {})
        fields = role_config.get("fields", {})
        
        if field_name in fields:
            try:
                return FieldAccessLevel(fields[field_name])
            except ValueError:
                return FieldAccessLevel.DENY
                
        pii_rule = self._pii_fields.get(field_name)
        if pii_rule:
            return FieldAccessLevel.MASK_PARTIAL
            
        return FieldAccessLevel.ALLOW

    def get_permitted_resources(self, user_role: str) -> Dict[str, str]:
        return self._roles.get(user_role, {}).get("resources", {})

    def is_admin(self, user_role: str) -> bool:
        return self._roles.get(user_role, {}).get("is_admin", False)
