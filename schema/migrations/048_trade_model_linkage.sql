-- 048: trade_model_linkage — ML attribution linking model versions to trade outcomes.
-- Also adds bi-temporal knowledge_time to ml_features for look-ahead bias prevention.

CREATE TABLE IF NOT EXISTS trade_model_linkage (
    id                      BIGSERIAL PRIMARY KEY,
    trade_event_seq         BIGINT NOT NULL,
    prediction_source       TEXT NOT NULL CHECK (prediction_source IN (
                                'prediction_log', 'esports_prediction_log'
                            )),
    prediction_id           BIGINT NOT NULL,
    model_name              TEXT NOT NULL,
    model_version           INTEGER,
    predicted_prob          NUMERIC(6,4),
    market_price_at_prediction NUMERIC(6,4),
    edge_at_prediction      NUMERIC(6,4),
    kelly_fraction          NUMERIC(6,4),
    feature_snapshot        JSONB,
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_trade_model_link_event ON trade_model_linkage (trade_event_seq);
CREATE INDEX idx_trade_model_link_model ON trade_model_linkage (model_name, model_version);
CREATE INDEX idx_trade_model_link_prediction ON trade_model_linkage (prediction_source, prediction_id);

-- Bi-temporal enhancement on existing ml_features (if table exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'ml_features') THEN
        ALTER TABLE ml_features ADD COLUMN IF NOT EXISTS knowledge_time TIMESTAMP DEFAULT NOW();
        ALTER TABLE ml_features ADD COLUMN IF NOT EXISTS feature_version INTEGER DEFAULT 1;

        -- Backfill existing rows
        UPDATE ml_features SET knowledge_time = created_at WHERE knowledge_time IS NULL;

        -- Point-in-time retrieval index
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_ml_features_pit
            ON ml_features (market_id, knowledge_time DESC)';
    END IF;
END $$;

-- Model version on prediction_log
ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS model_version INTEGER;
CREATE INDEX IF NOT EXISTS idx_prediction_log_model
    ON prediction_log (model_name, model_version);
