"""
RBAC engine — role-based access control enforced at the data layer.

Reads role definitions from config/roles.yaml and PII rules from
config/pii_fields.yaml.  Authorization decisions happen BEFORE data
retrieval — the LLM never sees unauthorized content.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel

log = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
    """Config-driven role-based access control."""

    def __init__(self, config_dir: Path | None = None):
        self._config_dir = config_dir or (_PROJECT_ROOT / "config")
        self._roles: dict[str, dict] = {}
        self._pii_fields: dict[str, dict] = {}
        self._load_configs()

    def _load_configs(self) -> None:
        roles_path = self._config_dir / "roles.yaml"
        pii_path = self._config_dir / "pii_fields.yaml"

        if roles_path.exists():
            with open(roles_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._roles = data.get("roles", {})
        else:
            log.warning("roles_config_missing", path=str(roles_path))

        if pii_path.exists():
            with open(pii_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._pii_fields = data.get("fields", data.get("pii_fields", {}))
        else:
            log.warning("pii_config_missing", path=str(pii_path))

    def authorize(
        self,
        user_role: str,
        resource_type: str,
        resource_id: str | None = None,
        portfolio_id: str | None = None,
    ) -> AuthorizationDecision:
        """Check whether a role can access a resource type."""
        role_cfg = self._roles.get(user_role, {})
        if not role_cfg:
            return AuthorizationDecision(
                allowed=False,
                reason=f"Unknown role: {user_role}",
                resource_type=resource_type,
                access_level="DENY",
            )

        permissions = role_cfg.get("permissions", {})
        access = permissions.get(resource_type)

        if access is None:
            return AuthorizationDecision(
                allowed=False,
                reason=f"Role '{user_role}' has no permission for '{resource_type}'",
                resource_type=resource_type,
                access_level="DENY",
            )

        # denied access levels
        if access == "denied":
            return AuthorizationDecision(
                allowed=False,
                reason=f"Access to '{resource_type}' is denied for role '{user_role}'",
                resource_type=resource_type,
                access_level="DENY",
            )

        # scoped access needs portfolio check for RM
        if access == "scoped" and user_role == "RELATIONSHIP_MANAGER" and not portfolio_id:
            return AuthorizationDecision(
                allowed=False,
                reason="Scoped access requires a portfolio_id",
                resource_type=resource_type,
                access_level="DENY",
            )

        return AuthorizationDecision(
            allowed=True,
            reason=f"Access granted ({access})",
            resource_type=resource_type,
            access_level=access,
        )

    def get_field_access(self, user_role: str, field_name: str) -> FieldAccessLevel:
        """Return the access level for a specific PII field and role."""
        field_cfg = self._pii_fields.get(field_name, {})
        if not field_cfg:
            return FieldAccessLevel.ALLOW  # non-PII fields are open

        role_access = field_cfg.get(user_role)
        if role_access is None:
            # if the field is classified as PII but no rule for this role, deny
            classification = field_cfg.get("classification", "")
            if "PII" in classification.upper() or "RESTRICTED" in classification.upper():
                return FieldAccessLevel.DENY
            return FieldAccessLevel.ALLOW

        try:
            return FieldAccessLevel(role_access)
        except ValueError:
            log.warning("unknown_access_level", field=field_name, role=user_role, level=role_access)
            return FieldAccessLevel.DENY

    def get_permitted_resources(self, user_role: str) -> dict[str, str]:
        """Return the permission map for a role."""
        role_cfg = self._roles.get(user_role, {})
        return role_cfg.get("permissions", {})

    def is_admin(self, user_role: str) -> bool:
        role_cfg = self._roles.get(user_role, {})
        return role_cfg.get("admin", False)
