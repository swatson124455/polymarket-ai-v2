-- PG LISTEN/NOTIFY triggers — Tier 4 #38
-- Run on VPS PostgreSQL after deployment.

-- 1. Market resolution notification
CREATE OR REPLACE FUNCTION notify_market_resolved() RETURNS trigger AS $$
BEGIN
    IF NEW.resolved IS TRUE AND (OLD.resolved IS NULL OR OLD.resolved IS FALSE) THEN
        PERFORM pg_notify('market_resolved', json_build_object(
            'market_id', NEW.id,
            'condition_id', NEW.condition_id,
            'resolution', NEW.resolution,
            'question', LEFT(NEW.question, 200)
        )::text);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_market_resolved ON markets;
CREATE TRIGGER trg_market_resolved
    AFTER UPDATE ON markets
    FOR EACH ROW EXECUTE FUNCTION notify_market_resolved();

-- 2. Large trade notification (>= $5K)
CREATE OR REPLACE FUNCTION notify_large_trade() RETURNS trigger AS $$
BEGIN
    IF ABS(NEW.size * NEW.price) >= 5000 THEN
        PERFORM pg_notify('large_trade', json_build_object(
            'trade_id', NEW.id,
            'market_id', NEW.market_id,
            'user_address', NEW.user_address,
            'size', NEW.size,
            'price', NEW.price,
            'side', NEW.side
        )::text);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_large_trade ON trades;
CREATE TRIGGER trg_large_trade
    AFTER INSERT ON trades
    FOR EACH ROW EXECUTE FUNCTION notify_large_trade();
