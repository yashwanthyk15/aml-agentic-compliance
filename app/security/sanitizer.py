import re
import structlog

logger = structlog.get_logger(__name__)

class Sanitizer:
    @staticmethod
    def sanitize_query(query: str) -> str:
        sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', query)
        sanitized = re.sub(r'[ \t]+', ' ', sanitized)
        
        MAX_QUERY_LENGTH = 4000
        if len(sanitized) > MAX_QUERY_LENGTH:
            logger.warning("Query length exceeded maximum, truncating", original_length=len(sanitized))
            sanitized = sanitized[:MAX_QUERY_LENGTH]
            
        return sanitized.strip()

    @staticmethod
    def sanitize_field(value: str, max_length: int = 1000) -> str:
        sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
        sanitized = re.sub(r'\s+', ' ', sanitized)
        
        if len(sanitized) > max_length:
            return sanitized[:max_length]
        return sanitized.strip()

    @staticmethod
    def sanitize_for_logging(value: str, max_length: int = 200) -> str:
        sanitized = Sanitizer.sanitize_field(value, max_length)
        sanitized = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[EMAIL REDACTED]', sanitized)
        return sanitized
