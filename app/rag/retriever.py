import uuid
from typing import Optional

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.rag.chunker import RegulatoryChunk
from app.rag.embeddings import EmbeddingEngine
from app.orchestration.state import RegulatoryEvidence

logger = structlog.get_logger(__name__)

class RegulatoryRetriever:
    def __init__(self, qdrant_url: str, collection_name: str, embedding_engine: EmbeddingEngine):
        self.collection_name = collection_name
        self.embedding_engine = embedding_engine
        logger.info("Connecting to Qdrant", url=qdrant_url)
        self.client = QdrantClient(url=qdrant_url)
        
    def ensure_collection(self, vector_size: int = 384):
        """Create collection if it doesn't exist."""
        try:
            collections = self.client.get_collections().collections
            collection_names = [col.name for col in collections]
            
            if self.collection_name not in collection_names:
                logger.info("Creating collection", name=self.collection_name)
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE
                    )
                )
            else:
                logger.info("Collection already exists", name=self.collection_name)
        except Exception as e:
            logger.error("Error ensuring collection", error=str(e))
            raise
            
    def index_chunks(self, chunks: list[RegulatoryChunk]):
        """Embed and upsert chunks into Qdrant."""
        if not chunks:
            return
            
        logger.info("Indexing chunks", count=len(chunks))
        texts = [chunk.text for chunk in chunks]
        embeddings = self.embedding_engine.embed_batch(texts)
        
        points = []
        for i, chunk in enumerate(chunks):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.chunk_id))
            payload = chunk.model_dump()
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=embeddings[i],
                    payload=payload
                )
            )
            
        self.client.upsert(
            collection_name=self.collection_name,
            points=points
        )
        logger.info("Successfully indexed chunks", count=len(chunks))
    
    def search(self, query: str, top_k: int = 5, jurisdiction: Optional[str] = None, document_id: Optional[str] = None) -> list[RegulatoryEvidence]:
        """Semantic search with optional metadata filtering."""
        logger.info("Searching Qdrant", query=query, top_k=top_k)
        query_vector = self.embedding_engine.embed_text(query)
        
        must_conditions = []
        if jurisdiction:
            must_conditions.append(
                models.FieldCondition(
                    key="jurisdiction",
                    match=models.MatchValue(value=jurisdiction)
                )
            )
        if document_id:
            must_conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id)
                )
            )
            
        query_filter = None
        if must_conditions:
            query_filter = models.Filter(must=must_conditions)
            
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=top_k
        )
        
        evidence_list = []
        for hit in results:
            payload = hit.payload or {}
            evidence = RegulatoryEvidence(
                evidence_id=payload.get("chunk_id", str(uuid.uuid4())),
                source_document=payload.get("document_name", "Unknown Document"),
                document_id=payload.get("document_id"),
                jurisdiction=payload.get("jurisdiction"),
                excerpt=payload.get("text", ""),
                relevance_score=hit.score,
                page_reference=str(payload.get("page")) if payload.get("page") else None
            )
            evidence_list.append(evidence)
            
        return evidence_list
