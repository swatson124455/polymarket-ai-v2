-- Migration 015: 2026 Alpha Roadmap infrastructure tables
-- Creates tables for new bot infrastructure, monitoring, and cross-platform tracking.

-- Cross-platform arbitrage opportunities (CrossPlatformArbBot)
CREATE TABLE IF NOT EXISTS cross_platform_arb_opportunities (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    platform_a      TEXT NOT NULL,
    platform_b      TEXT NOT NULL,
    price_a         DOUBLE PRECISION,
    price_b         DOUBLE PRECISION,
    spread          DOUBLE PRECISION,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    executed        BOOLEAN DEFAULT FALSE,
    execution_pnl   DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_cpao_market ON cross_platform_arb_opportunities(market_id);
CREATE INDEX IF NOT EXISTS idx_cpao_detected ON cross_platform_arb_opportunities(detected_at);

-- Sports game state cache (SportsBot)
CREATE TABLE IF NOT EXISTS sports_game_state (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    game_id         TEXT,
    sport           TEXT,
    home_team       TEXT,
    away_team       TEXT,
    current_score   TEXT,
    game_status     TEXT,  -- pregame, live, final
    live_odds       JSONB,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sgs_market ON sports_game_state(market_id);
CREATE INDEX IF NOT EXISTS idx_sgs_status ON sports_game_state(game_status);

-- Oracle / UMA proposal history (OracleBot)
CREATE TABLE IF NOT EXISTS oracle_proposals (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    proposal_hash   TEXT,
    proposed_price  DOUBLE PRECISION,
    proposer        TEXT,
    dispute_window  TIMESTAMPTZ,
    resolved        BOOLEAN DEFAULT FALSE,
    resolution      TEXT,
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_op_market ON oracle_proposals(market_id);

-- Regulatory alerts (RegulatoryMonitor)
CREATE TABLE IF NOT EXISTS regulatory_alerts (
    id              BIGSERIAL PRIMARY KEY,
    alert_type      TEXT NOT NULL,  -- ruling, legislation, enforcement
    jurisdiction    TEXT,
    summary         TEXT,
    impact_score    DOUBLE PRECISION,
    source_url      TEXT,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    acknowledged    BOOLEAN DEFAULT FALSE
);

-- Airdrop / incentive events (AirdropTracker)
CREATE TABLE IF NOT EXISTS airdrop_events (
    id              BIGSERIAL PRIMARY KEY,
    platform        TEXT NOT NULL,
    event_type      TEXT,  -- airdrop, points, rewards
    description     TEXT,
    estimated_value DOUBLE PRECISION,
    eligible        BOOLEAN DEFAULT FALSE,
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Wash trading alerts (WashTradingDetector)
CREATE TABLE IF NOT EXISTS wash_trading_alerts (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    suspicion_score DOUBLE PRECISION,
    pattern_type    TEXT,  -- circular, layering, spoofing
    addresses       JSONB,
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wta_market ON wash_trading_alerts(market_id);
