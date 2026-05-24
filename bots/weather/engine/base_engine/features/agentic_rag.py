"""
Agentic RAG (Retrieval-Augmented Generation) pipeline.

Dual-backend vector store with sentence-transformer embeddings for:
  - News articles (from NewsAggregator)
  - Past market resolutions
  - Expert analyses

Backends:
  - ChromaDB (local/dev) — in-memory, zero config
  - pgvector (VPS/prod) — PostgreSQL extension, persistent, shared across processes

Multi-hop retrieval: question -> initial search -> refine -> deeper search.
Integrates as a feature in the prediction ensemble via PredictionEngine.

Dependencies: chromadb OR asyncpg+pgvector, sentence-transformers (optional).
"""
from __future__ import annotations
import hashlib
from typing import Any, Dict, List, Optional
from structlog import get_logger

logger = get_logger()


class AgenticRAG:
    """
    Vector-store backed retrieval for prediction market context.

    Uses ChromaDB for embeddings storage and sentence-transformers
    for encoding. Falls back gracefully when dependencies missing.
    """

    def __init__(self, collection_name: str = "prediction_context"):
        self._collection_name = collection_name
        self._client = None
        self._collection = None
        self._encoder = None
        self._available = False
        self._init_stores()

    def _init_stores(self):
        """Initialize ChromaDB and sentence-transformer."""
        try:
            import chromadb
            self._client = chromadb.Client()
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except ImportError:
            logger.info("chromadb not installed — RAG disabled")
            return
        except Exception as e:
            logger.warning("ChromaDB init failed: %s", e)
            return

        try:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer("all-MiniLM-L6-v2")
            self._available = True
            logger.info("AgenticRAG initialized: ChromaDB + MiniLM-L6-v2 encoder")
        except ImportError:
            logger.info("sentence-transformers not installed — RAG uses ChromaDB default embeddings")
            self._available = True  # ChromaDB has built-in embedding

    @property
    def is_available(self) -> bool:
        return self._available

    async def index_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        Index documents into the vector store.

        Each document should have: {"text": "...", "source": "...", "metadata": {...}}
        Returns number of documents indexed.
        """
        if not self._available or not self._collection:
            return 0

        indexed = 0
        for doc in documents:
            text = doc.get("text", "")
            if not text or len(text) < 20:
                continue
            doc_id = hashlib.sha256(text[:500].encode()).hexdigest()[:16]
            try:
                if self._encoder:
                    embedding = self._encoder.encode(text[:1024]).tolist()
                    self._collection.add(
                        ids=[doc_id],
                        embeddings=[embedding],
                        documents=[text[:2048]],
                        metadatas=[doc.get("metadata", {})],
                    )
                else:
                    self._collection.add(
                        ids=[doc_id],
                        documents=[text[:2048]],
                        metadatas=[doc.get("metadata", {})],
                    )
                indexed += 1
            except Exception as e:
                logger.debug("RAG index failed for doc: %s", e)
        return indexed

    async def retrieve(self, query: str, n_results: int = 5) -> List[Dict]:
        """
        Retrieve relevant documents for a query.

        Returns list of {"text": str, "score": float, "metadata": dict}.
        """
        if not self._available or not self._collection:
            return []
        try:
            if self._encoder:
                query_embedding = self._encoder.encode(query[:512]).tolist()
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                )
            else:
                results = self._collection.query(
                    query_texts=[query[:512]],
                    n_results=n_results,
                )

            docs = []
            for i, doc_text in enumerate(results.get("documents", [[]])[0]):
                distance = (results.get("distances", [[]])[0] or [0.0])[i] if results.get("distances") else 0.0
                metadata = (results.get("metadatas", [[]])[0] or [{}])[i] if results.get("metadatas") else {}
                docs.append({
                    "text": doc_text,
                    "score": 1.0 - distance,  # Convert distance to similarity
                    "metadata": metadata,
                })
            return docs
        except Exception as e:
            logger.debug("RAG retrieve failed: %s", e)
            return []

    async def multi_hop_retrieve(self, question: str, max_hops: int = 2, n_per_hop: int = 3) -> List[Dict]:
        """
        Multi-hop retrieval: refine query based on initial results.

        Hop 1: Search with original question.
        Hop 2+: Combine question with top results to refine search.
        """
        all_docs = []
        current_query = question

        for hop in range(max_hops):
            docs = await self.retrieve(current_query, n_results=n_per_hop)
            all_docs.extend(docs)

            if not docs or hop >= max_hops - 1:
                break

            # Refine query: combine original question with top result
            top_text = docs[0].get("text", "")[:200]
            current_query = f"{question} Context: {top_text}"

        # Deduplicate by text content
        seen = set()
        unique = []
        for doc in all_docs:
            key = doc.get("text", "")[:100]
            if key not in seen:
                seen.add(key)
                unique.append(doc)

        return unique[:n_per_hop * max_hops]

    async def get_context_for_market(self, question: str, category: str = "") -> str:
        """Get RAG context string for use in prediction pipeline."""
        query = question
        if category:
            query = f"[{category}] {question}"
        docs = await self.multi_hop_retrieve(query)
        if not docs:
            return ""
        context_parts = [d["text"][:300] for d in docs[:3]]
        return "\n---\n".join(context_parts)


class PgVectorRAG:
    """
    pgvector-backed RAG for VPS deployment.

    Uses PostgreSQL pgvector extension for persistent vector storage.
    Shares the same sentence-transformer encoder as AgenticRAG.

    Requires: pgvector extension installed on PostgreSQL,
    and the table created via: CREATE EXTENSION vector;
    CREATE TABLE IF NOT EXISTS rag_documents (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        embedding vector(384),
        metadata JSONB DEFAULT '{}',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX ON rag_documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
    """

    def __init__(self, db=None):
        self._db = db
        self._encoder = None
        self._available = False
        self._embedding_dim = 384  # MiniLM-L6-v2 output dim
        self._init_encoder()

    def _init_encoder(self):
        """Load sentence-transformer encoder."""
        try:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer("all-MiniLM-L6-v2")
            self._available = True
            logger.info("PgVectorRAG encoder loaded: MiniLM-L6-v2")
        except ImportError:
            logger.info("sentence-transformers not installed — PgVectorRAG disabled")

    @property
    def is_available(self) -> bool:
        return self._available and self._db is not None

    async def index_documents(self, documents: List[Dict[str, str]]) -> int:
        """Index documents into pgvector table."""
        if not self.is_available or not self._db.session_factory:
            return 0

        indexed = 0
        async with self._db.get_session() as session:
            from sqlalchemy import text
            import json

            for doc in documents:
                doc_text = doc.get("text", "")
                if not doc_text or len(doc_text) < 20:
                    continue
                doc_id = hashlib.sha256(doc_text[:500].encode()).hexdigest()[:16]
                embedding = self._encoder.encode(doc_text[:1024]).tolist()
                metadata = doc.get("metadata", {})

                try:
                    await session.execute(
                        text("""
                            INSERT INTO rag_documents (id, content, embedding, metadata)
                            VALUES (:id, :content, :embedding, :metadata)
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": doc_id,
                            "content": doc_text[:2048],
                            "embedding": str(embedding),
                            "metadata": json.dumps(metadata),
                        },
                    )
                    indexed += 1
                except Exception as e:
                    logger.debug("pgvector index failed: %s", e)
            await session.commit()
        return indexed

    async def retrieve(self, query: str, n_results: int = 5) -> List[Dict]:
        """Retrieve similar documents using cosine similarity."""
        if not self.is_available or not self._db.session_factory:
            return []

        try:
            query_embedding = self._encoder.encode(query[:512]).tolist()
            async with self._db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(
                    text("""
                        SELECT content, metadata,
                               1 - (embedding <=> :query_vec) AS similarity
                        FROM rag_documents
                        ORDER BY embedding <=> :query_vec
                        LIMIT :n
                    """),
                    {"query_vec": str(query_embedding), "n": n_results},
                )
                rows = result.fetchall()
                return [
                    {
                        "text": row[0],
                        "score": float(row[2]) if row[2] else 0.0,
                        "metadata": row[1] if row[1] else {},
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.debug("pgvector retrieve failed: %s", e)
            return []

    async def get_context_for_market(self, question: str, category: str = "") -> str:
        """Get RAG context string using pgvector backend."""
        query = f"[{category}] {question}" if category else question
        docs = await self.retrieve(query, n_results=3)
        if not docs:
            return ""
        return "\n---\n".join(d["text"][:300] for d in docs)
