import re
import structlog
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

class InjectionScanResult(BaseModel):
    detected: bool
    patterns_matched: List[str]
    original_text: str
    field_name: Optional[str] = None

class InjectionDetector:
    def __init__(self):
        self.patterns = [
            "ignore previous instructions",
            "ignore all previous",
            "system:",
            "developer:",
            "reveal hidden",
            "show all customers",
            "show all pii",
            "forget rbac",
            "bypass security",
            "output secrets",
            "export pii",
            "disregard",
            "override",
            "you are now",
            "act as",
            "pretend you",
            "new instructions",
            r"\]\s*\[",
            "<script",
            "</s>"
        ]
        
        self._compiled_patterns = []
        for p in self.patterns:
            try:
                self._compiled_patterns.append(re.compile(p, re.IGNORECASE))
            except re.error:
                self._compiled_patterns.append(re.compile(re.escape(p), re.IGNORECASE))

    def scan(self, text: str, field_name: Optional[str] = None) -> InjectionScanResult:
        if not text:
            return InjectionScanResult(detected=False, patterns_matched=[], original_text=text, field_name=field_name)
            
        matched = []
        for pattern in self._compiled_patterns:
            if pattern.search(text):
                matched.append(pattern.pattern)
                
        return InjectionScanResult(
            detected=len(matched) > 0,
            patterns_matched=matched,
            original_text=text,
            field_name=field_name
        )

    def scan_record(self, record: Dict[str, Any]) -> List[InjectionScanResult]:
        results = []
        for key, value in record.items():
            if isinstance(value, str):
                scan_res = self.scan(value, field_name=key)
                if scan_res.detected:
                    results.append(scan_res)
            elif isinstance(value, dict):
                results.extend(self.scan_record(value))
        return results
