import re
import structlog
from typing import Optional, Dict, Any
from app.security.rbac import RBACEngine, FieldAccessLevel

logger = structlog.get_logger(__name__)

class MaskingEngine:
    def __init__(self, rbac_engine: RBACEngine):
        self.rbac_engine = rbac_engine

    def mask_record(self, record: Dict[str, Any], user_role: str, record_type: str = 'customer') -> Dict[str, Any]:
        masked_record = {}
        for key, value in record.items():
            # try dotted field name first (e.g., customer.full_name), then bare field name
            dotted_key = f"{record_type}.{key}"
            access = self.rbac_engine.get_field_access(user_role, dotted_key)
            if access == FieldAccessLevel.DENY:
                access_bare = self.rbac_engine.get_field_access(user_role, key)
                if access_bare == FieldAccessLevel.DENY:
                    continue  # field excluded entirely
                access = access_bare

            if isinstance(value, str):
                if access == FieldAccessLevel.MASK_PARTIAL:
                    masked_record[key] = self._apply_partial_mask(value, key)
                elif access in (FieldAccessLevel.ALLOW, FieldAccessLevel.ALLOW_SCOPED):
                    masked_record[key] = value
                # DENY already handled above
            else:
                masked_record[key] = value
        return masked_record

    def mask_value(self, value: str, field_name: str, user_role: str) -> Optional[str]:
        access_level = self.rbac_engine.get_field_access(user_role, field_name)
        
        if access_level == FieldAccessLevel.ALLOW or access_level == FieldAccessLevel.ALLOW_SCOPED:
            return value
        elif access_level == FieldAccessLevel.DENY:
            return None
        elif access_level == FieldAccessLevel.MASK_PARTIAL:
            return self._apply_partial_mask(value, field_name)
        
        return None

    def _apply_partial_mask(self, value: str, field_name: str) -> str:
        # Check if email
        if "@" in value:
            parts = value.split("@")
            if len(parts) == 2:
                name_part, domain = parts
                return f"{name_part[0]}***@{domain}" if len(name_part) > 0 else value
        
        # Check if phone number (starts with + or mostly digits)
        digits_only = re.sub(r'\D', '', value)
        if len(digits_only) >= 10 and any(c.isdigit() for c in value):
            return "********" + value[-4:]
            
        # Account numbers (mostly digits, often 8-12 length)
        if value.isdigit() and len(value) >= 8:
            return "*" * (len(value) - 4) + value[-4:]
            
        # Names (assume space separated)
        words = value.split()
        if len(words) >= 2:
            masked_words = []
            for w in words:
                if len(w) > 0:
                    masked_words.append(w[0] + "*" * (len(w) - 1))
            return " ".join(masked_words)
            
        # Default mask
        if len(value) > 4:
            return "*" * (len(value) - 4) + value[-4:]
        return "*" * len(value)
