"""
Market Embedding Script — Tier 4 #43

Embed all market questions using sentence-transformers (all-MiniLM-L6-v2, 22MB)
and store in pgvector for similarity search.

Usage: python scripts/embed_markets.py
Requires: sentence-transformers, asyncpg, pgvector extension on PostgreSQL.

After running, query similar markets:
  SELECT m.question, 1 - (me.question_embedding <=> :query_vec) AS similarity
  FROM market_embeddings me JOIN markets m ON m.id = me.market_id
  ORDER BY me.question_embedding <=> :query_vec LIMIT 10;
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from structlog import get_logger

logger = get_logger()


async def embed_all_markets():
    """Embed all market questions and store in pgvector."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("sentence-transformers not installed. Run: pip install sentence-transformers")
        sys.exit(1)

    from base_engine.data.database import Database
    from config.settings import settings

    print("Loading MiniLM-L6-v2 encoder...")
    encoder = SentenceTransformer("all-MiniLM-L6-v2")

    print("Connecting to database...")
    db = Database()
    await db.initialize()

    if not db.session_factory:
        print("Database connection failed")
        return

    # Fetch all markets with questions
    async with db.get_session() as session:
        from sqlalchemy import text
        result = await session.execute(
            text("SELECT id, question, category FROM markets WHERE question IS NOT NULL AND question != ''")
        )
        markets = result.fetchall()

    print(f"Found {len(markets)} markets to embed")

    # Batch encode
    batch_size = 64
    total_embedded = 0

    for i in range(0, len(markets), batch_size):
        batch = markets[i : i + batch_size]
        questions = [m[1][:512] for m in batch]  # Truncate to 512 chars
        embeddings = encoder.encode(questions, show_progress_bar=False)

        async with db.get_session() as session:
            from sqlalchemy import text
            for j, (market_id, question, category) in enumerate(batch):
                embedding_list = embeddings[j].tolist()
                try:
                    await session.execute(
                        text("""
                            INSERT INTO market_embeddings (market_id, question_embedding, category, updated_at)
                            VALUES (:market_id, :embedding, :category, NOW())
                            ON CONFLICT (market_id) DO UPDATE SET
                                question_embedding = EXCLUDED.question_embedding,
                                updated_at = NOW()
                        """),
                        {
                            "market_id": str(market_id),
                            "embedding": str(embedding_list),
                            "category": category or "",
                        },
                    )
                    total_embedded += 1
                except Exception as e:
                    logger.debug("Embed failed for market %s: %s", market_id, e)
            await session.commit()

        print(f"  Embedded {min(i + batch_size, len(markets))}/{len(markets)}")

    print(f"\nDone: {total_embedded} markets embedded in pgvector")

    # Build IVFFlat index if enough data
    if total_embedded >= 100:
        try:
            async with db.get_session() as session:
                from sqlalchemy import text
                await session.execute(
                    text("""
                        CREATE INDEX IF NOT EXISTS idx_market_embeddings_vec
                        ON market_embeddings USING ivfflat (question_embedding vector_cosine_ops)
                        WITH (lists = 100)
                    """)
                )
                await session.commit()
            print("IVFFlat index created for fast similarity search")
        except Exception as e:
            print(f"Index creation skipped: {e}")


async def find_similar_markets(question: str, top_k: int = 10):
    """Find markets similar to a given question."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("sentence-transformers not installed")
        return

    from base_engine.data.database import Database

    encoder = SentenceTransformer("all-MiniLM-L6-v2")
    query_embedding = encoder.encode(question[:512]).tolist()

    db = Database()
    await db.initialize()

    if not db.session_factory:
        print("Database connection failed")
        return

    async with db.get_session() as session:
        from sqlalchemy import text
        result = await session.execute(
            text("""
                SELECT m.id, m.question, m.category,
                       1 - (me.question_embedding <=> :query_vec) AS similarity
                FROM market_embeddings me
                JOIN markets m ON m.id = me.market_id
                ORDER BY me.question_embedding <=> :query_vec
                LIMIT :top_k
            """),
            {"query_vec": str(query_embedding), "top_k": top_k},
        )
        rows = result.fetchall()

    print(f"\nTop {top_k} similar markets to: '{question}'")
    print("-" * 80)
    for row in rows:
        print(f"  [{row[3]:.3f}] [{row[2] or 'N/A'}] {row[1][:100]}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Will Bitcoin reach $100,000?"
        asyncio.run(find_similar_markets(query))
    else:
        asyncio.run(embed_all_markets())
