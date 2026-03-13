-- 051: Lightweight ML model registry.
-- Replaces need for Feast/MLflow with simple PostgreSQL tables.
-- Tracks model versions, training metadata, performance metrics, and
-- feature set definitions used by each model version.

-- ============================================================
-- 1. model_registry — One row per model version
-- ============================================================
CREATE TABLE IF NOT EXISTS model_registry (
    id                  BIGSERIAL PRIMARY KEY,
    model_name          TEXT NOT NULL,
    model_version       INTEGER NOT NULL,
    model_type          TEXT NOT NULL CHECK (model_type IN (
                            'ensemble', 'gradient_boost', 'logistic',
                            'neural_net', 'calibration', 'onnx', 'custom'
                        )),
    status              TEXT NOT NULL DEFAULT 'staging' CHECK (status IN (
                            'staging', 'production', 'retired', 'failed'
                        )),

    -- Training metadata
    training_started_at TIMESTAMP,
    training_completed_at TIMESTAMP,
    training_samples    INTEGER,
    training_params     JSONB DEFAULT '{}',
    feature_set_id      BIGINT,                         -- FK to feature_sets

    -- Performance on held-out data
    metrics             JSONB DEFAULT '{}',              -- {accuracy, brier, log_loss, auc, ...}
    validation_samples  INTEGER,

    -- Artifact location (file path or object store key)
    artifact_path       TEXT,
    artifact_size_bytes BIGINT,
    artifact_hash       TEXT,                            -- SHA-256 for integrity

    -- Lifecycle
    promoted_at         TIMESTAMP,                       -- When moved to production
    retired_at          TIMESTAMP,
    retirement_reason   TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by          TEXT DEFAULT 'system',

    UNIQUE (model_name, model_version)
);

CREATE INDEX idx_model_registry_active ON model_registry (model_name, status)
    WHERE status = 'production';
CREATE INDEX idx_model_registry_name_version ON model_registry (model_name, model_version DESC);

-- ============================================================
-- 2. feature_sets — Defines which features a model version uses
-- ============================================================
CREATE TABLE IF NOT EXISTS feature_sets (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    description         TEXT,
    feature_names       TEXT[] NOT NULL,                  -- Ordered list of feature names
    feature_types       TEXT[],                           -- Corresponding types (numeric, categorical, etc.)
    preprocessing       JSONB DEFAULT '{}',               -- Normalization, encoding, etc.
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (name, version)
);

CREATE INDEX idx_feature_sets_name ON feature_sets (name, version DESC);

-- ============================================================
-- 3. model_predictions_log — Links predictions to model versions
--    (Supplements existing prediction_log with model version context)
-- ============================================================
CREATE TABLE IF NOT EXISTS model_performance_daily (
    id                  BIGSERIAL PRIMARY KEY,
    perf_date           DATE NOT NULL,
    model_name          TEXT NOT NULL,
    model_version       INTEGER NOT NULL,
    bot_name            TEXT NOT NULL,

    -- Daily aggregates
    prediction_count    INTEGER NOT NULL DEFAULT 0,
    trade_count         INTEGER NOT NULL DEFAULT 0,
    hit_count           INTEGER NOT NULL DEFAULT 0,      -- Correct direction
    miss_count          INTEGER NOT NULL DEFAULT 0,

    -- Calibration
    avg_predicted_prob  NUMERIC(6,4),
    avg_realized_prob   NUMERIC(6,4),
    brier_score         NUMERIC(8,6),
    log_loss            NUMERIC(8,6),

    -- Financial
    total_pnl           NUMERIC(18,4) DEFAULT 0,
    avg_edge            NUMERIC(6,4),

    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (perf_date, model_name, model_version, bot_name)
);

CREATE INDEX idx_model_perf_daily_model ON model_performance_daily
    (model_name, model_version, perf_date DESC);
CREATE INDEX idx_model_perf_daily_date ON model_performance_daily
    USING BRIN (perf_date);
