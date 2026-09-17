import structlog
from sentence_transformers import SentenceTransformer

logger = structlog.get_logger(__name__)

class EmbeddingEngine:
    def __init__(self, model_name: str = 'all-MiniLM-L6-v2'):
        self.model_name = model_name
        logger.info("Initializing EmbeddingEngine", model_name=model_name)
        try:
            self.model = SentenceTransformer(model_name)
        except Exception as e:
            logger.error("Failed to load SentenceTransformer model", error=str(e), model_name=model_name)
            raise
            
    def embed_text(self, text: str) -> list[float]:
        try:
            embedding = self.model.encode(text)
            return embedding.tolist()
        except Exception as e:
            logger.error("Failed to embed text", error=str(e))
            raise
    
    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        logger.debug("Embedding batch", num_texts=len(texts), batch_size=batch_size)
        try:
            embeddings = self.model.encode(texts, batch_size=batch_size)
            return [emb.tolist() for emb in embeddings]
        except Exception as e:
            logger.error("Failed to embed batch", error=str(e))
            raise
    
    @property
    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension()
