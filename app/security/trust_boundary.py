import structlog
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel
from app.security.injection_detector import InjectionDetector

logger = structlog.get_logger(__name__)

class TrustLabel(Enum):
    TRUSTED_SYSTEM = "TRUSTED_SYSTEM"
    TRUSTED_SOURCE = "TRUSTED_SOURCE"
    DERIVED_DATA = "DERIVED_DATA"
    USER_INPUT = "USER_INPUT"
    ATTACKER_CONTROLLED_DATA = "ATTACKER_CONTROLLED_DATA"
    MODEL_GENERATED = "MODEL_GENERATED"

class TrustedContent(BaseModel):
    content: str
    trust_label: TrustLabel
    source: Optional[str] = None
    field_name: Optional[str] = None
    injection_detected: bool = False

class TrustBoundary:
    def __init__(self, detector: InjectionDetector):
        self.detector = detector

    def label_content(self, content: str, source_type: str, field_name: Optional[str] = None) -> TrustedContent:
        scan_res = self.detector.scan(content, field_name=field_name)
        
        label_map = {
            "system": TrustLabel.TRUSTED_SYSTEM,
            "regulatory": TrustLabel.TRUSTED_SOURCE,
            "derived": TrustLabel.DERIVED_DATA,
            "user": TrustLabel.USER_INPUT,
            "attacker": TrustLabel.ATTACKER_CONTROLLED_DATA,
            "model": TrustLabel.MODEL_GENERATED
        }
        
        label = label_map.get(source_type, TrustLabel.USER_INPUT)
        
        return TrustedContent(
            content=content,
            trust_label=label,
            source=source_type,
            field_name=field_name,
            injection_detected=scan_res.detected
        )

    def label_user_query(self, query: str) -> TrustedContent:
        return self.label_content(query, "user", field_name="user_query")

    def label_transaction_field(self, value: str, field_name: str) -> TrustedContent:
        return self.label_content(value, "attacker", field_name=field_name)

    def label_regulatory_content(self, text: str, source: str) -> TrustedContent:
        scan_res = self.detector.scan(text, field_name="regulatory_text")
        return TrustedContent(
            content=text,
            trust_label=TrustLabel.TRUSTED_SOURCE,
            source=source,
            field_name="regulatory_text",
            injection_detected=scan_res.detected
        )

    def build_safe_context(self, contents: List[TrustedContent]) -> str:
        safe_blocks = []
        for item in contents:
            if item.injection_detected:
                logger.warning("Omitting injected content from safe context", field=item.field_name, source=item.source)
                continue
                
            block = f"--- START {item.trust_label.value} (Field: {item.field_name}) ---\n"
            block += f"{item.content}\n"
            block += f"--- END {item.trust_label.value} ---"
            safe_blocks.append(block)
            
        return "\n\n".join(safe_blocks)
