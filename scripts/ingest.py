#!/usr/bin/env python3
"""
Ingestion script to load regulatory PDFs and OFAC sanctions lists into Qdrant and the Sanctions Screener.
"""
import sys
import os
import yaml
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
    with open(project_root / "config" / "settings.yaml", encoding="utf-8") as source:
        settings = yaml.safe_load(source) or {}
    
    # 1. Ingest PDFs
    log.info("Ingesting PDFs", dir=str(raw_dir / "regulations"))
    ingester = PDFIngester(raw_dir=raw_dir, processed_dir=processed_dir)
    docs = ingester.ingest_all()
    if not docs:
        raise RuntimeError(
            f"No regulatory PDFs found under {raw_dir / 'regulations'}. "
            "Place the supplied PDFs there or configure the raw data directory."
        )
    
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
    try:
        embedder = EmbeddingEngine()
        qdrant_cfg = settings.get("qdrant", {})
        retriever = RegulatoryRetriever(
            qdrant_url=os.getenv("QDRANT_URL") or qdrant_cfg.get("url", "http://localhost:6333"),
            collection_name=os.getenv("QDRANT_COLLECTION") or qdrant_cfg.get("collection_name", "regulatory_chunks"),
            embedding_engine=embedder,
        )
        retriever.ensure_collection()
        retriever.index_chunks(all_chunks)
    except Exception as exc:
        log.warning("qdrant_indexing_skipped", error=str(exc)[:200])
    
    # 5. Sanctions Ingestion
    log.info("Ingesting OFAC SDN list")
    sanctions_zip = raw_dir / "sanctions" / "sdn_enhanced.zip"
    if sanctions_zip.exists():
        screener = SanctionsScreener.from_xml(sanctions_zip)
        log.info("Loaded sanctions", count=len(screener.entries))
        with open(processed_dir / "sanctions_entries.jsonl", "w", encoding="utf-8") as output:
            for entry in screener.entries:
                output.write(entry.model_dump_json() + "\n")
    else:
        log.warning("Sanctions zip not found, skipping", path=str(sanctions_zip))
        
    log.info("Ingestion pipeline completed successfully")

if __name__ == "__main__":
    main()
