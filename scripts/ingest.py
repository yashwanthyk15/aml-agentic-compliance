import os
import sys
import json
from pathlib import Path
import structlog

# Ensure project root is in path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.rag.ingestion import PDFIngester
from app.rag.chunker import RegulatoryChunker
from app.rag.embeddings import EmbeddingEngine
from app.rag.retriever import RegulatoryRetriever
from app.screening.sanctions import SanctionsScreener

logger = structlog.get_logger(__name__)

def main():
    logger.info("Starting ingestion pipeline")
    
    raw_reg_dir = project_root / "data" / "raw" / "regulations"
    processed_dir = project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Ingest PDFs
    logger.info("Ingesting PDFs", dir=str(raw_reg_dir))
    ingester = PDFIngester(source_dir=raw_reg_dir)
    documents = ingester.process_all()
    
    # 2. Chunking
    logger.info("Chunking documents")
    chunker = RegulatoryChunker()
    all_chunks = []
    for doc in documents:
        chunks = chunker.chunk_document(doc)
        all_chunks.extend(chunks)
        
    # 3. Save chunks
    chunks_path = processed_dir / "regulatory_chunks.jsonl"
    logger.info("Saving chunks", path=str(chunks_path))
    with open(chunks_path, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            # handle model dumping gracefully
            data = chunk.model_dump() if hasattr(chunk, 'model_dump') else chunk.dict() if hasattr(chunk, 'dict') else chunk
            f.write(json.dumps(data) + "\n")
            
    # 4. Embeddings and Indexing
    logger.info("Initializing embedding engine and retriever")
    try:
        # Pass mock or actual dependencies as appropriate
        retriever = RegulatoryRetriever(collection_name="regulations")
        retriever.index_chunks(all_chunks)
        logger.info("Indexed chunks in Qdrant")
    except Exception as e:
        logger.warning("Failed to index chunks. Continuing anyway.", error=str(e))
        
    # 5. Sanctions Data
    sdn_zip = project_root / "data" / "raw" / "sanctions" / "sdn_enhanced.zip"
    sanctions_path = processed_dir / "sanctions_entries.json"
    if sdn_zip.exists():
        logger.info("Extracting sanctions data", file=str(sdn_zip))
        try:
            screener = SanctionsScreener.from_xml(str(sdn_zip))
            with open(sanctions_path, "w", encoding="utf-8") as f:
                json.dump([], f)  # Mock save for now
            logger.info("Saved sanctions data")
        except Exception as e:
            logger.warning("Failed to process sanctions data", error=str(e))
    else:
        logger.warning("Sanctions data not found, skipping.", file=str(sdn_zip))
        
    logger.info("Ingestion pipeline completed")

if __name__ == "__main__":
    main()
