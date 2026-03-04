-- Migration 001: Schema migrations tracking table (run first, by migration runner)
-- Do not run manually; use: python scripts/run_migrations.py

CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_schema_migrations_name ON schema_migrations(name);
