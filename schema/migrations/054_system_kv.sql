-- 054: Generic key-value store for persistent runtime state (canary stage, etc.)
CREATE TABLE IF NOT EXISTS system_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
