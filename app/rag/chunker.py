import re
from typing import Optional

import structlog
from pydantic import BaseModel
from app.rag.ingestion import DocumentMetadata, PageContent

logger = structlog.get_logger(__name__)

class RegulatoryChunk(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    section_path: list[str]
    page: int
    page_end: Optional[int] = None
    text: str
    jurisdiction: Optional[str] = None
    effective_date: Optional[str] = None
    char_count: int
    token_estimate: int

class RegulatoryChunker:
    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
    def _estimate_tokens(self, text: str) -> int:
        # Rough estimate: 1 token ~= 4 chars for English text
        return len(text) // 4
        
    def chunk_document(self, doc_id: str, pages: list[PageContent], metadata: DocumentMetadata) -> list[RegulatoryChunk]:
        """Split document into chunks preserving section hierarchy."""
        logger.info("Chunking document", doc_id=doc_id, pages=len(pages))
        
        chunks = []
        current_section_path = []
        current_chunk_text = ""
        current_chunk_start_page = 1
        sequence_number = 0
        
        for page in pages:
            lines = page.text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                # Basic section detection
                if re.match(r'^(\d+\.)+\s+.*|^CHAPTER\s+[A-Z0-9]+\b|^[A-Z\s]{10,}$', line) and len(line) < 150:
                    current_section_path.append(line)
                    if len(current_section_path) > 3:
                        current_section_path = current_section_path[-3:]
                        
                    # Also, force a chunk break if current chunk is reasonably large
                    if len(current_chunk_text) > self.chunk_size // 2:
                        chunks.append(self._create_chunk(
                            doc_id, metadata, sequence_number, current_section_path,
                            current_chunk_start_page, page.page_number, current_chunk_text
                        ))
                        sequence_number += 1
                        current_chunk_text = self._overlap_text(current_chunk_text)
                        current_chunk_start_page = page.page_number
                
                current_chunk_text += line + " "
                
                if len(current_chunk_text) >= self.chunk_size:
                    # Find last period to break cleanly
                    last_period = current_chunk_text.rfind('. ')
                    if last_period != -1 and last_period > self.chunk_size // 2:
                        split_idx = last_period + 1
                    else:
                        split_idx = len(current_chunk_text)
                        
                    chunk_text = current_chunk_text[:split_idx].strip()
                    chunks.append(self._create_chunk(
                        doc_id, metadata, sequence_number, current_section_path,
                        current_chunk_start_page, page.page_number, chunk_text
                    ))
                    sequence_number += 1
                    
                    # Keep overlap
                    current_chunk_text = self._overlap_text(current_chunk_text[:split_idx]) + " "
                    current_chunk_start_page = page.page_number
                    
        if current_chunk_text.strip():
            chunks.append(self._create_chunk(
                doc_id, metadata, sequence_number, current_section_path,
                current_chunk_start_page, pages[-1].page_number, current_chunk_text.strip()
            ))
            
        logger.info("Created chunks", doc_id=doc_id, num_chunks=len(chunks))
        return chunks

    def _overlap_text(self, text: str) -> str:
        """Return overlap starting at a complete sentence boundary."""
        clean_text = text.strip()
        if not clean_text or self.chunk_overlap <= 0:
            return ""
        start = max(0, len(clean_text) - self.chunk_overlap)
        boundary = clean_text.rfind(". ", 0, start + 1)
        if boundary >= 0:
            return clean_text[boundary + 2:].strip()
        return ""
        
    def _create_chunk(self, doc_id: str, metadata: DocumentMetadata, sequence_number: int, 
                      section_path: list[str], start_page: int, end_page: int, text: str) -> RegulatoryChunk:
        return RegulatoryChunk(
            chunk_id=f"{doc_id}_{sequence_number}",
            document_id=doc_id,
            document_name=metadata.document_name,
            section_path=list(section_path),
            page=start_page,
            page_end=end_page if end_page != start_page else None,
            text=text,
            jurisdiction=metadata.jurisdiction,
            effective_date=metadata.effective_date,
            char_count=len(text),
            token_estimate=self._estimate_tokens(text)
        )
