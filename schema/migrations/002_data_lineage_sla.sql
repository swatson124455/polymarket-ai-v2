-- Migration 002: Data Lineage (#38) and Data Quality SLA (#48)
-- Run this if you already applied the main supabase_schema.sql and need only these tables.
-- Usage: psql $DATABASE_URL -f schema/migrations/002_data_lineage_sla.sql
-- Or: Supabase SQL Editor -> paste and run.

-- ============================================
-- DATA LINEAGE (#38)
-- ============================================
CREATE TABLE IF NOT EXISTS data_lineage (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT,
    target_type TEXT NOT NULL,
    target_id TEXT,
    operation TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_data_lineage_source ON data_lineage(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_data_lineage_target ON data_lineage(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_data_lineage_created ON data_lineage(created_at DESC);

-- ============================================
-- DATA QUALITY SLA (#48)
-- ============================================
CREATE TABLE IF NOT EXISTS data_quality_sla (
    id SERIAL PRIMARY KEY,
    sla_name TEXT NOT NULL UNIQUE,
    metric_name TEXT NOT NULL,
    threshold_min DOUBLE PRECISION,
    threshold_max DOUBLE PRECISION,
    window_minutes INTEGER NOT NULL DEFAULT 60,
    alert_on_violation BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_data_quality_sla_name ON data_quality_sla(sla_name);
