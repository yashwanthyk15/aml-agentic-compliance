import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

class DocumentMetadata(BaseModel):
    document_id: str
    document_name: str
    source_path: str
    total_pages: int
    total_chars: int
    sections_detected: list[str]
    jurisdiction: Optional[str] = None
    effective_date: Optional[str] = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)

class PageContent(BaseModel):
    page_number: int
    text: str
    sections: list[str]

class PDFIngester:
    def __init__(self, raw_dir: Path, processed_dir: Path):
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        
    def _generate_doc_id(self, filename: str) -> str:
        return hashlib.sha256(filename.encode()).hexdigest()[:16]
        
    def _detect_jurisdiction(self, filename: str, content: str) -> str:
        text = (filename + " " + content[:2000]).upper()
        if "RESERVE BANK OF INDIA" in text or "RBI" in text or "_IN_" in filename.upper():
            return "IN"
        if "FATF" in text or "FINANCIAL ACTION TASK FORCE" in text or "_INTL_" in filename.upper():
            return "INTL"
        return "UNKNOWN"
        
    def _detect_sections(self, text: str) -> list[str]:
        sections = []
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Look for numbered headers (1., 1.1, Chapter X) or all caps headers
            if re.match(r'^(\d+\.)+\s+.*|^CHAPTER\s+[A-Z0-9]+\b|^[A-Z\s]{10,}$', line) and len(line) < 150:
                sections.append(line)
        return sections

    def ingest_pdf(self, pdf_path: Path) -> tuple[DocumentMetadata, list[PageContent]]:
        """Extract text from PDF preserving page numbers and basic structure."""
        logger.info("Ingesting PDF", path=str(pdf_path))
        if not pdf_path.exists():
            logger.error("File not found", path=str(pdf_path))
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
            
        try:
            doc = fitz.open(str(pdf_path))
        except Exception as e:
            logger.error("Failed to open PDF", error=str(e), path=str(pdf_path))
            raise
            
        if len(doc) == 0:
            logger.error("Empty PDF", path=str(pdf_path))
            raise ValueError(f"Empty PDF: {pdf_path}")
            
        total_chars = 0
        all_sections = []
        page_contents = []
        first_few_pages_text = ""
        
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if i < 3:
                first_few_pages_text += text + " "
            
            if len(text) < 100:
                logger.warning("Short text on page, possibly scanned", page=i+1, length=len(text))
                
            page_sections = self._detect_sections(text)
            all_sections.extend(page_sections)
            
            page_contents.append(PageContent(
                page_number=i+1,
                text=text,
                sections=page_sections
            ))
            total_chars += len(text)
            
        doc_id = self._generate_doc_id(pdf_path.name)
        jurisdiction = self._detect_jurisdiction(pdf_path.name, first_few_pages_text)
        
        metadata = DocumentMetadata(
            document_id=doc_id,
            document_name=pdf_path.name,
            source_path=str(pdf_path),
            total_pages=len(doc),
            total_chars=total_chars,
            sections_detected=list(set(all_sections)),
            jurisdiction=jurisdiction,
            effective_date=None,  # Hard to extract accurately without LLM
        )
        
        logger.info("Successfully ingested PDF", doc_id=doc_id, pages=len(doc), chars=total_chars)
        return metadata, page_contents

    def ingest_all(self) -> list[tuple[DocumentMetadata, list[PageContent]]]:
        """Ingest all PDFs in raw_dir/regulations/"""
        regulations_dir = self.raw_dir / "regulations"
        if not regulations_dir.exists():
            logger.warning("Regulations directory not found", path=str(regulations_dir))
            return []
            
        results = []
        for pdf_path in regulations_dir.glob("*.pdf"):
            try:
                results.append(self.ingest_pdf(pdf_path))
            except Exception as e:
                logger.error("Failed to ingest PDF during batch", error=str(e), path=str(pdf_path))
                
        return results
