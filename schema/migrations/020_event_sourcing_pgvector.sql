-- Event Sourcing + pgvector — Tier 4 #42-43
-- Apply on VPS PostgreSQL.

-- ── Event Sourcing: append-only decision log ────────────────────
CREATE TABLE IF NOT EXISTS decision_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    correlation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decision_events_type
    ON decision_events (event_type);
CREATE INDEX IF NOT EXISTS idx_decision_events_created
    ON decision_events (created_at);
CREATE INDEX IF NOT EXISTS idx_decision_events_corr
    ON decision_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- ── pgvector: market similarity via embeddings ──────────────────
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        BEGIN
            CREATE EXTENSION vector;
            RAISE NOTICE 'pgvector extension created';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pgvector not available — skipping vector tables: %', SQLERRM;
            RETURN;
        END;
    END IF;

    -- RAG documents table (used by PgVectorRAG in agentic_rag.py)
    CREATE TABLE IF NOT EXISTS rag_documents (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        embedding vector(384),
        metadata JSONB DEFAULT '{}',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- Market embeddings table (for market similarity search)
    CREATE TABLE IF NOT EXISTS market_embeddings (
        market_id TEXT PRIMARY KEY REFERENCES markets(id),
        question_embedding vector(384),
        category TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- IVFFlat index for fast cosine similarity search
    -- lists=100 is good for up to ~100K documents
    BEGIN
        CREATE INDEX IF NOT EXISTS idx_rag_documents_embedding
            ON rag_documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
    EXCEPTION WHEN OTHERS THEN
        -- IVFFlat requires at least 100 rows to build; create after data exists
        RAISE NOTICE 'IVFFlat index deferred (needs data): %', SQLERRM;
    END;

    BEGIN
        CREATE INDEX IF NOT EXISTS idx_market_embeddings_vec
            ON market_embeddings USING ivfflat (question_embedding vector_cosine_ops) WITH (lists = 100);
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'Market embedding index deferred: %', SQLERRM;
    END;

END $$;
