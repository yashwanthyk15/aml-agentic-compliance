#!/usr/bin/env python3
"""
Ingestion script to load regulatory PDFs and OFAC sanctions lists into Qdrant and the Sanctions Screener.
"""
import sys
from pathlib import Path
import structlog
import json

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.rag.ingestion import PDFIngester
from app.rag.chunker import RegulatoryChunker
from app.rag.embeddings import EmbeddingEngine
from app.rag.retriever import RegulatoryRetriever
from app.screening.sanctions import SanctionsScreener

log = structlog.get_logger(__name__)

def main():
    log.info("Starting ingestion pipeline")
    
    raw_dir = project_root / "data" / "raw"
    processed_dir = project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Ingest PDFs
    log.info("Ingesting PDFs", dir=str(raw_dir / "regulations"))
    ingester = PDFIngester(raw_dir=raw_dir, processed_dir=processed_dir)
    docs = ingester.ingest_all()
    
    # 2. Chunking
    log.info("Chunking documents")
    chunker = RegulatoryChunker()
    all_chunks = []
    for meta, pages in docs:
        chunks = chunker.chunk_document(meta.document_id, pages, meta)
        all_chunks.extend(chunks)
        
    log.info("Generated chunks", count=len(all_chunks))
        
    # 3. Save chunks locally for reference
    chunks_path = processed_dir / "regulatory_chunks.jsonl"
    with open(chunks_path, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(chunk.model_dump_json() + "\n")
            
    # 4. Embeddings & Qdrant
    log.info("Generating embeddings and indexing in Qdrant")
    embedder = EmbeddingEngine()
    retriever = RegulatoryRetriever(
        qdrant_url="http://localhost:6333", 
        collection_name="regulations", 
        embedding_engine=embedder
    )
    retriever.ensure_collection()
    retriever.index_chunks(all_chunks)
    
    # 5. Sanctions Ingestion
    log.info("Ingesting OFAC SDN list")
    sanctions_dir = raw_dir / "sanctions"
    if sanctions_dir.exists():
        screener = SanctionsScreener()
        screener.load_ofac_sdn(sanctions_dir)
    else:
        log.warning("Sanctions directory not found, skipping", path=str(sanctions_dir))
        
    log.info("Ingestion pipeline completed successfully")

if __name__ == "__main__":
    main()
