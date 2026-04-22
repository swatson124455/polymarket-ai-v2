-- S172 Phase 7B Phase A: mirror_rejected_signals table.
--
-- Captures every RTDS/WebSocket whale signal that reached MirrorBot and was
-- rejected at a gate. Used by Phase B counterfactual PnL ranking to tell
-- "rejected noise" from "rejected signal" and feed wallet-level evidence back
-- into watchlist inclusion (currently copy-PnL only affects sizing).
--
-- See: docs/7B_wallet_overhaul_design.md §5 Phase A.
-- Scope: mirror_bot.py rejection sites only. EliteWatchlist RTDS-ingress
-- dedup is deliberately EXCLUDED per S187 §2.1 decision (transport artifact,
-- would pollute wallet counterfactual ranking).
--
-- Idempotency: CREATE IF NOT EXISTS on table + all four indexes.
-- Reversibility: see schema/migrations/down/073_drop_mirror_rejected_signals.sql
-- (manual rollback only — not part of normal release pipeline).

CREATE TABLE IF NOT EXISTS mirror_rejected_signals (
    id                BIGSERIAL PRIMARY KEY,
    event_time        TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    trader_address    TEXT NOT NULL,
    market_id         TEXT NOT NULL,
    token_id          TEXT,
    side              TEXT,
    price             DOUBLE PRECISION,
    whale_trade_usd   DOUBLE PRECISION,
    rejection_reason  TEXT NOT NULL,
    rejection_stage   TEXT NOT NULL,
    metadata          JSONB,
    -- Phase A3: backfilled by backfill_mirror_rejected_signals_resolution() once market resolves.
    resolution        VARCHAR(16),
    resolved_at       TIMESTAMP WITHOUT TIME ZONE
);

-- Wallet-scan: per-trader time-descending scans for Phase B counterfactual roll-up.
CREATE INDEX IF NOT EXISTS idx_mirror_rej_trader_time
    ON mirror_rejected_signals (trader_address, event_time DESC);

-- Market-scan: join to markets.resolution on market_id when backfilling.
CREATE INDEX IF NOT EXISTS idx_mirror_rej_market_time
    ON mirror_rejected_signals (market_id, event_time DESC);

-- Resolution-scan: speed the unresolved→resolved backfill predicate.
CREATE INDEX IF NOT EXISTS idx_mirror_rej_unresolved
    ON mirror_rejected_signals (market_id)
    WHERE resolution IS NULL;

-- Stage-scan: per-stage counts for Phase B's reason-vs-stage breakdown.
CREATE INDEX IF NOT EXISTS idx_mirror_rej_stage_time
    ON mirror_rejected_signals (rejection_stage, event_time DESC);

COMMENT ON TABLE mirror_rejected_signals IS
    'S172 7B Phase A: rejected whale signals for Phase B counterfactual PnL ranking. See docs/7B_wallet_overhaul_design.md.';

COMMENT ON COLUMN mirror_rejected_signals.rejection_stage IS
    'pre_gate | gate | post_gate — matches §A2 stage buckets. watchlist stage is excluded (EliteWatchlist out of scope per S187 §2.1).';

COMMENT ON COLUMN mirror_rejected_signals.metadata IS
    'JSONB for site-specific context (confidence, gate_score, min_edge, cooldown_secs, etc). Structured keys expected by Phase B retune script.';
