import asyncio
import json
from typing import Optional, List, Dict, Any, Tuple
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Session
from sqlalchemy import Column, String, Float, Integer, BigInteger, Boolean, DateTime, Text, Index, UniqueConstraint, text, and_, LargeBinary, JSON, event, insert, update, bindparam, ARRAY, Numeric, Date
from sqlalchemy.types import TypeDecorator
from structlog import get_logger
from config.settings import settings
from base_engine.exceptions import DatabaseError

logger = get_logger()
Base = declarative_base()


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize datetime to naive UTC for PostgreSQL TIMESTAMP WITHOUT TIME ZONE / asyncpg.
    Idempotent: naive datetimes are returned unchanged; aware are converted to UTC then tz stripped.
    """
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class NaiveUTCDateTime(TypeDecorator):
    """
    Stores datetime as naive UTC for PostgreSQL TIMESTAMP WITHOUT TIME ZONE.
    Coerces timezone-aware datetimes to naive UTC on bind (avoids asyncpg error).
    """
    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        return _naive_utc(value)

    def process_result_value(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        return value


class Market(Base):
    __tablename__ = "markets"
    
    id = Column(String, primary_key=True)
    condition_id = Column(String, index=True)
    question = Column(Text)
    description = Column(Text)  # Market description/context
    slug = Column(String, unique=True, index=True)
    category = Column(String, index=True)
    resolution_source = Column(String)
    end_date_iso = Column(NaiveUTCDateTime)
    image = Column(String)
    active = Column(Boolean, index=True)
    liquidity = Column(Float)
    volume = Column(Float)
    
    # V2 CLOB Token IDs (CRITICAL for price history)
    yes_token_id = Column(String, index=True)  # Token ID for YES outcome
    no_token_id = Column(String, index=True)   # Token ID for NO outcome
    
    # Current prices (from outcomePrices)
    yes_price = Column(Float)  # Current YES price
    no_price = Column(Float)    # Current NO price
    outcome_prices = Column(Text)  # JSON array: ["0.65", "0.35"]
    
    # Resolution fields (for learnable data)
    resolved = Column(Boolean, default=False, index=True)
    resolution = Column(String)  # YES, NO, etc.
    resolution_source_method = Column(String)  # 'gamma_api' or 'blockchain'
    resolved_at = Column(NaiveUTCDateTime)
    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)))
    updated_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), onupdate=lambda: _naive_utc(datetime.now(timezone.utc)))
    # P4: empty price-fetch tracking (run schema/migrations/011_price_fetch_empty_tracking.sql)
    price_fetch_attempts = Column(Integer, default=0)
    last_price_fetch_empty = Column(NaiveUTCDateTime, nullable=True)
    # NegRisk defense columns — migration 017 applied 2026-02-22
    neg_risk = Column(Boolean, default=False, nullable=True)
    outcome_count = Column(Integer, default=2, nullable=True)

    __table_args__ = (
        Index("idx_markets_active_category", "active", "category"),
        Index("idx_markets_liquidity", "liquidity"),
        Index("idx_markets_resolved", "resolved"),
        Index("idx_markets_yes_token", "yes_token_id"),
        Index("idx_markets_no_token", "no_token_id"),
    )


class MarketPrice(Base):
    __tablename__ = "market_prices"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(String, index=True)
    token_id = Column(String, index=True)
    price = Column(Float)
    side = Column(String)  # BUY or SELL (for learnable data)
    timestamp = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)
    partition_month = Column(String(7), index=True)  # YYYY-MM for partitioning
    
    __table_args__ = (
        Index("idx_prices_market_timestamp", "market_id", "timestamp"),
        Index("idx_prices_side", "side"),  # For filtering by order side
        Index("idx_prices_partition", "partition_month", "market_id"),  # Composite index for partitioning
        UniqueConstraint("market_id", "token_id", "timestamp", name="uq_market_prices_market_token_timestamp"),
    )


# Per-mapper cache of datetime attribute keys — avoids rescanning ALL column attrs on every flush.
# Populated lazily on first flush of each ORM class; never cleared (mappers don't change at runtime).
_dt_attr_cache: dict = {}


def _coerce_datetimes_naive_utc(session: Session, _flush_context: Any, _instances: Any) -> None:
    """
    Before flush: coerce any timezone-aware datetime to naive UTC.
    Required for PostgreSQL TIMESTAMP WITHOUT TIME ZONE + asyncpg (rejects aware datetimes).
    One bad object is logged and skipped so the rest still get coerced.

    Perf: caches datetime column names per mapper class so the full column scan
    (previously: ALL columns × ALL objects on every flush) only happens once.
    """
    for obj in list(session.new) + list(session.dirty):
        try:
            mapper = obj.__mapper__
        except Exception as e:
            logger.debug("before_flush: skip object without mapper", error=str(e))
            continue

        cls = type(obj)
        if cls not in _dt_attr_cache:
            dt_keys = []
            for attr in mapper.column_attrs:
                try:
                    if not hasattr(attr, "columns") or not attr.columns:
                        continue
                    if isinstance(attr.columns[0].type, (DateTime, NaiveUTCDateTime)):
                        dt_keys.append(attr.key)
                except Exception as _dt_err:
                    logger.debug("db_datetime_attr_check_failed", attr=str(getattr(attr, 'key', '?')), error=str(_dt_err))
            _dt_attr_cache[cls] = dt_keys

        for key in _dt_attr_cache[cls]:
            try:
                val = getattr(obj, key, None)
                if val is None or getattr(val, "tzinfo", None) is None:
                    continue
                setattr(obj, key, _naive_utc(val))
            except Exception as e:
                logger.warning(
                    "before_flush: failed to coerce datetime",
                    attr=key,
                    error=str(e),
                    exc_info=True,
                )


event.listen(Session, "before_flush", _coerce_datetimes_naive_utc)


class _SemaphoreSession:
    """
    Async context manager that wraps session creation with semaphore.
    Limits concurrent database operations to prevent connection pool exhaustion.
    Optional timeout kills hung queries before they block the pool.
    """
    def __init__(self, session_factory, semaphore: Optional[asyncio.Semaphore],
                 timeout: Optional[float] = None):
        self.session_factory = session_factory
        self.semaphore = semaphore
        self.session = None
        self.timeout = timeout
        self._timeout_ctx = None

    async def __aenter__(self):
        if self.semaphore:
            try:
                await asyncio.wait_for(self.semaphore.acquire(), timeout=15)
            except asyncio.TimeoutError:
                raise DatabaseError(
                    "DB semaphore timeout — all slots occupied for 15s",
                    operation="get_session",
                    table=None,
                )
        if self.timeout is not None:
            self._timeout_ctx = asyncio.timeout(self.timeout)
            await self._timeout_ctx.__aenter__()
        self.session = self.session_factory()
        result = await self.session.__aenter__()
        # S159 C20: Per-session statement timeout. PgBouncer transaction mode compatible
        # (proven by S157 prune fix). Default 60s; server-side ALTER SYSTEM is 300s fallback.
        _timeout_ms = 60000  # default outside try — prevents UnboundLocalError in except
        try:
            from config.settings import settings as _settings
            _timeout_ms = getattr(_settings, "DB_STATEMENT_TIMEOUT_MS", 60000)
            _idle_txn_ms = getattr(_settings, "DB_IDLE_IN_TXN_TIMEOUT_MS", 60000)
            from sqlalchemy import text as _sa_text
            await result.execute(_sa_text(f"SET statement_timeout = '{_timeout_ms}'"))
            # S168: Apply idle_in_transaction_session_timeout — kills sessions that sit
            # idle inside an open transaction (e.g. after SAVEPOINT rollback in price
            # fallback chain). Without this, connections hold locks forever, causing
            # cascade: lock waits → statement timeouts → pool exhaustion → bot stall.
            # Setting was defined in settings.py since S152 but never applied to connections.
            await result.execute(_sa_text(f"SET idle_in_transaction_session_timeout = '{_idle_txn_ms}'"))
            # S161: Clear autobegin triggered by SET so callers can use session.begin().
            # SET statement_timeout (without LOCAL) is session-scoped and survives COMMIT.
            await result.commit()
        except Exception as _set_err:
            import structlog as _sl
            _sl.get_logger().warning("set_statement_timeout_failed", timeout_ms=_timeout_ms, error=str(_set_err))
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.session:
                await self.session.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            try:
                if self._timeout_ctx is not None:
                    await self._timeout_ctx.__aexit__(exc_type, exc_val, exc_tb)
            finally:
                if self.semaphore:
                    self.semaphore.release()
        return False


class Trade(Base):
    __tablename__ = "trades"
    
    id = Column(String, primary_key=True)
    market_id = Column(String, index=True)
    token_id = Column(String, index=True)
    user_address = Column(String, index=True)
    bot_id = Column(String, index=True, nullable=True)
    side = Column(String)
    size = Column(Float)
    price = Column(Float)
    pnl = Column(Float, nullable=True)
    entry_time = Column(NaiveUTCDateTime, index=True, nullable=True)
    exit_time = Column(NaiveUTCDateTime, nullable=True)
    # NaiveUTCDateTime: bulk_insert_trades and callers may pass aware datetimes; PG TIMESTAMP WITHOUT TZ requires naive
    timestamp = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)
    partition_month = Column(String(7), index=True)  # YYYY-MM for partitioning
    
    __table_args__ = (
        Index("idx_trades_user_timestamp", "user_address", "timestamp"),
        Index("idx_trades_market_timestamp", "market_id", "timestamp"),
        Index("idx_trades_partition", "partition_month", "market_id"),  # Composite index for partitioning
    )


class User(Base):
    __tablename__ = "users"
    
    address = Column(String, primary_key=True)
    total_profit = Column(Float, default=0.0)
    total_volume = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    roi = Column(Float, default=0.0)
    is_elite = Column(Boolean, default=False, index=True)
    is_likely_market_maker = Column(Boolean, default=False)  # Trades both sides on >60% of markets
    last_updated = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), onupdate=lambda: _naive_utc(datetime.now(timezone.utc)))


class Prediction(Base):
    __tablename__ = "predictions"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(String, index=True)
    token_id = Column(String, index=True)
    confidence = Column(Float)
    model_type = Column(String)
    features = Column(Text)
    timestamp = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)

    __table_args__ = (
        Index("idx_predictions_market_timestamp", "market_id", "timestamp"),
    )


class Position(Base):
    __tablename__ = "positions"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bot_id = Column(String, index=True)
    source_bot = Column(String, index=True, nullable=True)  # Bot that placed order (EnsembleBot, MirrorBot, etc.) for per-bot P&L
    market_id = Column(String, index=True)
    token_id = Column(String, index=True)
    side = Column(String)
    size = Column(Float)
    entry_price = Column(Float)
    current_price = Column(Float)
    unrealized_pnl = Column(Float)
    entry_cost = Column(Float, nullable=True)       # Total entry cost $ (slippage + fee) — Session 45 cost-aware exits
    breakeven_price = Column(Float, nullable=True)   # Active SELL breakeven price (entry + round-trip costs / size)
    opened_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)
    status = Column(String, default="open", index=True)  # 'open' | 'reserving' | 'closed'
    is_paper = Column(Boolean, default=False, index=True)  # True for SIMULATION_MODE positions; excluded from real metrics
    trader_addresses = Column(ARRAY(String), nullable=True, server_default="{}")  # Migration 035: elite trader addresses tracking

    __table_args__ = (
        UniqueConstraint("bot_id", "market_id", "side", name="uq_positions_bot_market_side"),
        Index("idx_positions_bot_id", "bot_id"),
        Index("idx_positions_market_id", "market_id"),
        Index("idx_positions_status", "status"),
    )
    
    @property
    def bot_name(self) -> str:
        """Backward compat: bot_id exposed as bot_name for callers."""
        return self.bot_id or ""
    
    @property
    def closed(self) -> bool:
        """Backward compat: true iff status == 'closed'."""
        return (self.status or "open") == "closed"
    
    @property
    def timestamp(self) -> Optional[datetime]:
        """Backward compat: opened_at as timestamp."""
        return self.opened_at


class SystemConfig(Base):
    """Key-value store for Kill Switch and other system flags."""
    __tablename__ = "system_config"
    key = Column(String, primary_key=True)
    value = Column(Text)


class SyncLog(Base):
    """Ingestion run tracking for monitoring and health checks."""
    __tablename__ = "sync_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sync_type = Column(String, nullable=False, index=True)
    component = Column(String, nullable=False, index=True)
    started_at = Column(NaiveUTCDateTime, nullable=False)
    completed_at = Column(NaiveUTCDateTime, nullable=True)
    status = Column(String, nullable=False, index=True)
    records_processed = Column(Integer, nullable=True)
    records_inserted = Column(Integer, nullable=True)
    records_failed = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    extra = Column("metadata", JSON, nullable=True)  # Python name 'extra' to avoid Base.metadata conflict


class Snapshot(Base):
    """Pre-operation stats for rollback verification (SnapshotManager)."""
    __tablename__ = "snapshots"
    id = Column(String, primary_key=True)
    description = Column(Text, nullable=False)
    created_at = Column(NaiveUTCDateTime, nullable=False)
    statistics = Column(JSON, nullable=False)


class HealingLog(Base):
    """AutoHealer audit trail."""
    __tablename__ = "healing_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(NaiveUTCDateTime, nullable=False)
    issues_detected = Column(Integer, nullable=False)
    fixes_applied = Column(Integer, nullable=False)
    details = Column(JSON, nullable=True)


class MLFeatures(Base):
    """Pre-computed ML features per market (FeatureStore)."""
    __tablename__ = "ml_features"
    market_id = Column(String, primary_key=True)
    computed_at = Column(NaiveUTCDateTime, nullable=False)
    features = Column(JSON, nullable=False)
    updated_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), onupdate=lambda: _naive_utc(datetime.now(timezone.utc)))


class FillAnalysis(Base):
    """
    Per-fill adverse selection for Kyle's lambda and spread decomposition.
    price_30s / adverse_move_30s: column names are legacy; both use a 30-MINUTE window
    (price 30 minutes after fill, adverse move over that window).
    price_60s / price_300s / adverse_move_300s: extended windows from migration 013.
    """
    __tablename__ = "fill_analysis"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(String, nullable=False, index=True)
    source_bot = Column(String, nullable=True, index=True)
    fill_price = Column(Float, nullable=False)
    fill_side = Column(String, nullable=False)  # YES, NO
    fill_time = Column(NaiveUTCDateTime, nullable=False)
    price_30s = Column(Float, nullable=True)   # Price 30 MINUTES after fill (legacy name)
    price_60s = Column(Float, nullable=True)   # Price 60s after fill
    price_300s = Column(Float, nullable=True)  # Price 300s (5 min) after fill
    adverse_move_30s = Column(Float, nullable=True)  # Adverse move over 30 MINUTES (legacy name)
    adverse_move_300s = Column(Float, nullable=True)  # Adverse move over 300s window
    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)))


class LearningPattern(Base):
    """Persistence for LearningEngine patterns."""
    __tablename__ = "learning_patterns"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pattern_type = Column(String, nullable=False, index=True)
    pattern_key = Column(String, nullable=False, index=True)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    total = Column(Integer, default=0)
    confidence = Column(Float, default=0.0)
    sample_size = Column(Integer, default=0)
    updated_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), onupdate=lambda: _naive_utc(datetime.now(timezone.utc)))
    __table_args__ = (UniqueConstraint("pattern_type", "pattern_key", name="uq_learning_patterns_type_key"),)


class PredictionLog(Base):
    """Drift detection, live performance tracking. Log every prediction for post-resolution analysis."""
    __tablename__ = "prediction_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(String, nullable=False, index=True)
    token_id = Column(String)
    model_name = Column(String, nullable=False)
    predicted_prob = Column(Float, nullable=False)
    market_price = Column(Float, nullable=False)
    edge = Column(Float, nullable=False)
    prediction_time = Column(NaiveUTCDateTime, nullable=False)
    fallback_level = Column(Integer)
    confidence = Column(Float)
    resolution = Column(String)
    resolved_at = Column(NaiveUTCDateTime)
    was_correct = Column(Boolean)
    realized_edge = Column(Float)
    trade_executed = Column(Boolean, default=False)
    trade_side = Column(String)
    trade_size = Column(Float)
    trade_price = Column(Float)
    trade_pnl = Column(Float)
    ensemble_pred = Column(Float, nullable=True)
    learning_conf = Column(Float, nullable=True)
    feature_snapshot = Column(JSON, nullable=True)
    correlation_id = Column(String, nullable=True, index=True)
    bot_name = Column(String(64), nullable=True, index=True)  # Session 47: per-bot prediction tracking
    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)))


class PaperTradeRecord(Base):
    """Paper (simulated) trades for SIMULATION_MODE. Persisted so we can compute hypothetical P&L as markets resolve."""
    __tablename__ = "paper_trades"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_id = Column(String, nullable=False, index=True)
    market_id = Column(String, nullable=False, index=True)
    token_id = Column(String, nullable=True)
    bot_name = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    size = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    confidence = Column(Float, nullable=True)
    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), nullable=False)
    resolution = Column(String, nullable=True)
    resolved_at = Column(NaiveUTCDateTime, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    correlation_id = Column(String, nullable=True, index=True)
    latency_ms = Column(Float, nullable=True)  # Order execution latency (ms) — S43
    # Order state machine: PENDING→SUBMITTED→FILLED (migration 039)
    status = Column(String, nullable=False, default="filled")
    submitted_at = Column(NaiveUTCDateTime, nullable=True)
    filled_at = Column(NaiveUTCDateTime, nullable=True)


class TradeEvent(Base):
    """Immutable append-only event store for trade audit trail (migration 043)."""
    __tablename__ = "trade_events"
    sequence_num = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    execution_mode = Column(String, nullable=False, default="paper")
    event_time = Column(NaiveUTCDateTime, nullable=False)
    knowledge_time = Column(NaiveUTCDateTime, nullable=False, default=lambda: _naive_utc(datetime.now(timezone.utc)))
    recorded_at = Column(NaiveUTCDateTime, nullable=False, default=lambda: _naive_utc(datetime.now(timezone.utc)))
    bot_name = Column(String, nullable=False)
    market_id = Column(String, nullable=False)
    token_id = Column(String, nullable=True)
    correlation_id = Column(String, nullable=True)
    order_id = Column(String, nullable=True)
    side = Column(String, nullable=True)
    size = Column(Numeric(18, 8), nullable=True)
    price = Column(Numeric(18, 8), nullable=True)
    fees = Column(Numeric(18, 8), nullable=True, default=0)
    realized_pnl = Column(Numeric(18, 4), nullable=True)
    confidence = Column(Numeric(6, 4), nullable=True)
    predicted_probability = Column(Numeric(6, 4), nullable=True)
    model_version = Column(Integer, nullable=True)
    model_name = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True, unique=True)
    event_data = Column(JSON, nullable=True, default=dict)


class EquitySnapshot(Base):
    """Portfolio-level daily snapshot with peak/drawdown/Sharpe (migration 045)."""
    __tablename__ = "equity_snapshots"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False)
    bot_name = Column(String, nullable=False)
    total_capital = Column(Numeric(18, 4), nullable=False)
    deployed_capital = Column(Numeric(18, 4), nullable=False)
    realized_pnl = Column(Numeric(18, 4), nullable=False)
    unrealized_pnl = Column(Numeric(18, 4), nullable=False)
    total_equity = Column(Numeric(18, 4), nullable=False)
    open_positions = Column(Integer, nullable=False, default=0)
    daily_trades = Column(Integer, nullable=False, default=0)
    win_count = Column(Integer, nullable=False, default=0)
    loss_count = Column(Integer, nullable=False, default=0)
    peak_equity = Column(Numeric(18, 4), nullable=True)
    drawdown_pct = Column(Numeric(8, 6), nullable=True)
    rolling_sharpe = Column(Numeric(8, 4), nullable=True)
    execution_mode = Column(String, nullable=False, default="paper")

    __table_args__ = (
        UniqueConstraint("snapshot_date", "bot_name",
                         name="uq_equity_snapshots_date_bot"),
    )


class ReconciliationBreak(Base):
    """Automated integrity check results (migration 046)."""
    __tablename__ = "reconciliation_breaks"
    break_id = Column(BigInteger, primary_key=True, autoincrement=True)
    recon_date = Column(Date, nullable=False)
    recon_type = Column(String, nullable=False)
    bot_name = Column(String, nullable=False)
    market_id = Column(String, nullable=True)
    internal_value = Column(Numeric(18, 8), nullable=True)
    external_value = Column(Numeric(18, 8), nullable=True)
    difference = Column(Numeric(18, 8), nullable=True)
    severity = Column(String, nullable=True, default="WARNING")
    status = Column(String, nullable=True, default="OPEN")
    details = Column(JSON, nullable=True, default=dict)
    detected_at = Column(NaiveUTCDateTime, nullable=True, default=lambda: _naive_utc(datetime.now(timezone.utc)))
    resolved_at = Column(NaiveUTCDateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)
    audit_run_id = Column(BigInteger, nullable=True)
    violation_hash = Column(String, nullable=True)


class AuditRun(Base):
    """Audit run metadata (migration 062)."""
    __tablename__ = "audit_runs"
    run_id        = Column(BigInteger, primary_key=True, autoincrement=True)
    run_type      = Column(String, nullable=False)
    started_at    = Column(NaiveUTCDateTime, nullable=False, default=lambda: _naive_utc(datetime.now(timezone.utc)))
    completed_at  = Column(NaiveUTCDateTime, nullable=True)
    status        = Column(String, nullable=False, default="running")
    checks_run    = Column(Integer, nullable=True)
    checks_passed = Column(Integer, nullable=True)
    checks_failed = Column(Integer, nullable=True)
    checks_warned = Column(Integer, nullable=True)
    total_breaks  = Column(Integer, nullable=True)
    summary       = Column(JSON, nullable=True, default=dict)
    error_message = Column(Text, nullable=True)
    triggered_by  = Column(String, nullable=False, default="scheduler")


# TradeModelLinkage ORM class removed — migration 052 drops table (0 readers)


class MLModel(Base):
    """Persistence for PredictionEngine models."""
    __tablename__ = "ml_models"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    model_name = Column(String, nullable=False, index=True)
    model_type = Column(String, nullable=False)
    model_data = Column(LargeBinary, nullable=False)
    scaler_data = Column(LargeBinary, nullable=True)
    metrics = Column(JSON, nullable=True)
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True, index=True)
    trained_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)))
    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)))


class Signal(Base):
    """Trading signals from external sources (news, social, whale tracking, etc.)"""
    __tablename__ = "signals"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(String, index=True, nullable=False)
    source_type = Column(String, nullable=False)  # 'news', 'social', 'whale', 'calendar', 'cross_platform'
    source_name = Column(String, nullable=False)  # 'reuters', 'twitter', 'whale_tracker', etc.
    direction = Column(String, nullable=False)  # 'YES', 'NO'
    confidence = Column(Float, nullable=False)  # 0.0 to 1.0
    raw_text = Column(Text)  # Original text/data
    extracted_entities = Column(Text)  # JSON string of extracted entities
    time_sensitivity = Column(String)  # 'immediate', 'hours', 'days'
    is_breaking = Column(Boolean, default=False)
    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)
    expires_at = Column(NaiveUTCDateTime, index=True)
    acted_on = Column(Boolean, default=False, index=True)
    priority_score = Column(Float, default=0.0)  # Calculated priority for queue
    # Outcome learning: filled when market resolves
    outcome_correct = Column(Boolean, nullable=True, index=True)  # True=signal matched resolution
    resolution_at = Column(NaiveUTCDateTime, nullable=True)
    market_resolution = Column(String, nullable=True)  # YES, NO, etc.

    __table_args__ = (
        Index("idx_signals_market_created", "market_id", "created_at"),
        Index("idx_signals_active", "expires_at", "acted_on"),
        Index("idx_signals_priority", "priority_score", "created_at"),
        Index("idx_signals_outcome", "outcome_correct"),
    )


class ScheduledEvent(Base):
    """Scheduled events that may affect markets (court dates, earnings, elections, etc.)"""
    __tablename__ = "scheduled_events"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(String, index=True)  # Optional - may affect multiple markets
    event_type = Column(String, nullable=False)  # 'court_date', 'earnings', 'election', 'announcement', etc.
    event_name = Column(String, nullable=False)
    scheduled_time = Column(NaiveUTCDateTime, nullable=False, index=True)
    source_url = Column(String)
    description = Column(Text)
    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)))
    notified = Column(Boolean, default=False)  # Whether bots have been notified
    
    __table_args__ = (
        Index("idx_events_upcoming", "scheduled_time", "notified"),
        Index("idx_events_market", "market_id", "scheduled_time"),
    )


class TradeSignal(Base):
    """
    R2: Stores signal scores at the moment a trade is placed.
    Allows prediction_engine to JOIN signal context back into training data,
    enabling the ML to learn signal × price × outcome correlations.
    One row per paper trade. trade_id references paper_trades.id.
    """
    __tablename__ = "trade_signals"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_id = Column(String, index=True, nullable=False)        # FK → paper_trades.id (soft ref)
    market_id = Column(String, index=True, nullable=False)
    bot_name = Column(String, index=True, nullable=True)

    # Signal ingestion (news / social consensus)
    signal_direction = Column(String(10), nullable=True)         # "YES", "NO", or None
    signal_confidence = Column(Float, nullable=True)             # 0.0–1.0 (best priority_score signal)
    signal_source = Column(String(64), nullable=True)            # e.g. "gdelt", "bluesky", "reddit"
    signal_multiplier = Column(Float, nullable=True)             # actual multiplier applied (1.2 / 0.6 etc.)

    # Order flow
    order_flow_direction = Column(String(10), nullable=True)     # "bullish", "bearish", "neutral"
    order_flow_multiplier = Column(Float, nullable=True)         # 1.1 / 0.85 / 1.0

    # Google Trends
    trends_signal = Column(String(10), nullable=True)            # "bullish", "bearish", "neutral"
    trends_multiplier = Column(Float, nullable=True)             # 1.05 / 1.0

    created_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)

    __table_args__ = (
        Index("idx_trade_signals_trade_id", "trade_id"),
        Index("idx_trade_signals_market_created", "market_id", "created_at"),
    )


class BotMarketParam(Base):
    """
    R5: Per-bot, per-market adaptive parameter store.
    Allows bots (MirrorBot, etc.) to persist learned thresholds
    across restarts. One row per (bot_name, market_id, param_name).
    """
    __tablename__ = "bot_market_params"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bot_name = Column(String, index=True, nullable=False)
    market_id = Column(String, index=True, nullable=False)
    param_name = Column(String(64), nullable=False)              # e.g. "z_threshold", "consensus_min"
    param_value = Column(Float, nullable=False)
    sample_n = Column(Integer, default=0)                        # Number of resolved trades used
    accuracy = Column(Float, nullable=True)                      # Win rate on this param value
    updated_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), onupdate=lambda: _naive_utc(datetime.now(timezone.utc)))

    __table_args__ = (
        UniqueConstraint("bot_name", "market_id", "param_name", name="uq_bot_market_param"),
        Index("idx_bot_market_params_lookup", "bot_name", "market_id"),
    )


class PerformanceRecord(Base):
    """Performance tracking by multiple dimensions for pattern analysis"""
    __tablename__ = "performance_records"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_id = Column(String, index=True)  # Reference to original trade
    bot_name = Column(String, index=True)
    market_id = Column(String, index=True)
    
    # Dimension values
    market_category = Column(String, index=True)  # politics, crypto, sports, etc.
    entry_price_range = Column(String, index=True)  # 0-20, 20-40, 40-60, 60-80, 80-100
    time_to_resolution_days = Column(Integer, index=True)
    liquidity_level = Column(String, index=True)  # thin, moderate, deep
    signal_source = Column(String, index=True)  # what triggered the entry
    market_regime = Column(String, index=True)  # CALM, VOLATILE, TRENDING, etc.
    day_of_week = Column(Integer, index=True)  # 0-6
    hour_of_day = Column(Integer, index=True)  # 0-23
    
    # Outcome metrics
    profit = Column(Float, nullable=False)
    profit_pct = Column(Float)
    hold_time_hours = Column(Float)
    was_winner = Column(Boolean, index=True)
    
    # Timestamps
    entry_time = Column(NaiveUTCDateTime, index=True)
    exit_time = Column(NaiveUTCDateTime)
    recorded_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)
    
    __table_args__ = (
        Index("idx_perf_category", "market_category", "was_winner"),
        Index("idx_perf_bot", "bot_name", "was_winner"),
        Index("idx_perf_regime", "market_regime", "was_winner"),
    )


class WhaleMovement(Base):
    """Track large trades and smart money movements"""
    __tablename__ = "whale_movements"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_id = Column(String, unique=True, index=True)  # Reference to Trade.id
    user_address = Column(String, index=True)
    market_id = Column(String, index=True)
    token_id = Column(String)
    side = Column(String)  # YES or NO
    size = Column(Float)
    price = Column(Float)
    value_usd = Column(Float)  # size * price
    timestamp = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)

    # Smart money metrics
    smart_money_rank = Column(Float)  # 0.0 to 1.0
    trader_category_accuracy = Column(Float)  # Accuracy in this market category
    is_clustered = Column(Boolean, default=False)  # Part of wallet cluster
    cluster_id = Column(String, index=True)  # Cluster identifier
    
    __table_args__ = (
        Index("idx_whale_user_time", "user_address", "timestamp"),
        Index("idx_whale_market_time", "market_id", "timestamp"),
        Index("idx_whale_smart_money", "smart_money_rank", "timestamp"),
    )


class DataQualityIssue(Base):
    """Tracks data quality issues detected during validation"""
    __tablename__ = "data_quality_issues"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(String, index=True)  # NULL for system-wide issues
    issue_type = Column(String, nullable=False, index=True)  # 'missing_token_id', 'price_anomaly', 'stale_prices', etc.
    description = Column(Text)
    detected_at = Column(NaiveUTCDateTime, default=lambda: _naive_utc(datetime.now(timezone.utc)), index=True)
    # L5: Soft-resolve instead of deleting. NULL = unresolved, timestamp = resolved.
    # Enables 30-day retention purge: DELETE WHERE resolved_at < NOW() - 30 days
    resolved_at = Column(NaiveUTCDateTime, nullable=True, default=None)

    __table_args__ = (
        Index("idx_quality_market", "market_id"),
        Index("idx_quality_type", "issue_type"),
        Index("idx_quality_detected", "detected_at"),
    )


class BotHealthState(Base):
    """Per-bot health state machine snapshots (migration 021)."""
    __tablename__ = "bot_health_states"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bot_name = Column(String(100), nullable=False, index=True)
    state = Column(String(50), nullable=False)          # healthy, degraded, failed, recovering, safe_mode
    failure_count = Column(Integer, nullable=False, default=0)
    sizing_multiplier = Column(Float, nullable=False, default=1.0)
    state_entered_at = Column(NaiveUTCDateTime, nullable=True)
    recorded_at = Column(NaiveUTCDateTime, nullable=False,
                         default=lambda: _naive_utc(datetime.now(timezone.utc)))
    details = Column(JSON, nullable=True)

    __table_args__ = (
        Index("idx_bot_health_bot_name", "bot_name"),
        Index("idx_bot_health_state", "state"),
        Index("idx_bot_health_recorded_at", "recorded_at"),
    )


class ConfigHistory(Base):
    """Audit trail for configuration parameter changes (migration 021)."""
    __tablename__ = "config_history"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    patch_id = Column(String(36), nullable=False)        # UUID string
    applied_at = Column(NaiveUTCDateTime, nullable=False,
                        default=lambda: _naive_utc(datetime.now(timezone.utc)))
    trigger_type = Column(String(50), nullable=False)    # 'auto_patch', 'manual', 'canary', 'rollback'
    component = Column(String(100), nullable=False)
    param_key = Column(String(200), nullable=False)
    before_value = Column(JSON, nullable=True)
    after_value = Column(JSON, nullable=True)
    action_taken = Column(String(100), nullable=True)
    outcome = Column(String(50), nullable=True)
    approved_by = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_config_history_component", "component"),
        Index("idx_config_history_applied_at", "applied_at"),
    )


class DeadLetterQueue(Base):
    """Failed async operations capture for inspection and replay (migration 021)."""
    __tablename__ = "dead_letter_queue"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)  # 'signal_write', 'trade_persist', etc.
    payload = Column(JSON, nullable=False)
    error_message = Column(Text, nullable=True)
    error_type = Column(String(100), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    status = Column(String(20), nullable=False, default="pending", index=True)
    created_at = Column(NaiveUTCDateTime, nullable=False,
                        default=lambda: _naive_utc(datetime.now(timezone.utc)))
    next_retry_at = Column(NaiveUTCDateTime, nullable=True)
    replayed_at = Column(NaiveUTCDateTime, nullable=True)
    source_bot = Column(String(100), nullable=True)
    market_id = Column(String(200), nullable=True, index=True)

    __table_args__ = (
        Index("idx_dlq_status", "status"),
        Index("idx_dlq_event_type", "event_type"),
        Index("idx_dlq_created_at", "created_at"),
    )


class WeatherForecast(Base):
    """Cached ensemble forecast snapshots for WeatherBot (migration 022)."""
    __tablename__ = "weather_forecasts"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    station_id = Column(String(20), nullable=False, index=True)
    target_date = Column(NaiveUTCDateTime, nullable=False)
    forecast_time = Column(NaiveUTCDateTime, nullable=False)
    lead_time_hours = Column(Float, nullable=False)
    ensemble_members = Column(JSON, nullable=False)  # List[float]
    deterministic_high = Column(Float, nullable=True)
    model_spread = Column(Float, nullable=True)
    models_used = Column(JSON, nullable=True)  # List[str]
    created_at = Column(NaiveUTCDateTime, nullable=False,
                        default=lambda: _naive_utc(datetime.now(timezone.utc)))

    __table_args__ = (
        Index("idx_weather_fc_station_date", "station_id", "target_date"),
        UniqueConstraint("station_id", "target_date", "forecast_time",
                         name="uq_weather_fc_station_date_time"),
    )


class WeatherCalibration(Base):
    """Historical forecast-vs-actual for bias calibration (migration 022)."""
    __tablename__ = "weather_calibration"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    station_id = Column(String(20), nullable=False, index=True)
    target_date = Column(NaiveUTCDateTime, nullable=False)
    forecast_temp = Column(Float, nullable=False)
    actual_temp = Column(Float, nullable=True)  # Null until resolved
    lead_time_hours = Column(Float, nullable=False)
    bias = Column(Float, nullable=True)  # actual - forecast
    crps = Column(Float, nullable=True)  # Continuous Ranked Probability Score (migration 032)
    model_name = Column(String(50), nullable=True)
    created_at = Column(NaiveUTCDateTime, nullable=False,
                        default=lambda: _naive_utc(datetime.now(timezone.utc)))

    __table_args__ = (
        Index("idx_weather_cal_station_date", "station_id", "target_date"),
    )


class SportsCalibration(Base):
    """
    I43: Per-(sport, market_type) calibration record — Brier scores and Kelly fractions.

    Written by HealthScheduler._run_sports_calibration() (migration 023_sports_tables.sql).
    Read by sports.kelly.adaptive_kelly.get_kelly_fraction() to size bets correctly.

    Schema matches 023_sports_tables.sql:
      id BIGSERIAL PK, sport VARCHAR(20), market_type VARCHAR(50),
      bet_count INTEGER, correct_count INTEGER, brier_score FLOAT,
      kelly_fraction FLOAT DEFAULT 0.25, last_updated TIMESTAMP WITHOUT TIME ZONE
      UNIQUE(sport, market_type)
    """
    __tablename__ = "sports_calibration"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sport = Column(String(20), nullable=False)
    market_type = Column(String(50), nullable=False)
    bet_count = Column(Integer, nullable=False, default=0)
    correct_count = Column(Integer, nullable=False, default=0)
    brier_score = Column(Float, nullable=True)
    kelly_fraction = Column(Float, nullable=False, default=0.25)
    last_updated = Column(NaiveUTCDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("sport", "market_type", name="uq_sports_cal_sport_market_type"),
        Index("idx_sports_cal_sport", "sport"),
    )


class Database:
    def __init__(self) -> None:
        self.engine = None
        self.session_factory: Optional[async_sessionmaker] = None
        self._engine_loop_id: Optional[int] = None  # id(loop) that created engine; avoid dispose() from other loop

    async def init(self) -> None:
        """
        Initialize PostgreSQL database connection with verification.
        Skips initialization when DATABASE_URL is not set (API-only mode).
        Safe to call multiple times: clears existing engine (dispose only when in same loop to avoid "different loop").
        """
        if self.engine is not None:
            try:
                current_loop_id = id(asyncio.get_running_loop())
                if current_loop_id == self._engine_loop_id:
                    await self.engine.dispose()
            except Exception as e:
                logger.debug("Disposing previous engine: %s", e)
            self.engine = None
            self.session_factory = None
            self._engine_loop_id = None

        db_url = (settings.DATABASE_URL or "").strip()
        if not db_url:
            logger.warning("DATABASE_URL not set - skipping database initialization (API-only mode)")
            self.engine = None
            self.session_factory = None
            self._engine_loop_id = None
            return
        try:
            await self._init_postgres(db_url)
            try:
                await self._verify_database()
                logger.info("Database initialized and verified successfully")
            except Exception as verify_err:
                logger.warning(
                    "Database engine created but verification failed: %s \u2014 keeping session_factory alive for retry",
                    type(verify_err).__name__,
                )
        except Exception as e:
            logger.error("PostgreSQL database initialization failed: %s", ascii(str(e)))
            self.engine = None
            self.session_factory = None
            self._engine_loop_id = None

    async def _verify_database(self) -> None:
        """
        Verify database is actually working by performing test operations.
        Retries up to 3 times with backoff for transient connection drops.
        """
        if not self.session_factory:
            raise DatabaseError(
                "Database session_factory is None - initialization failed",
                operation="verify_database",
                table=None
            )

        last_err = None
        for attempt in range(3):
            try:
                async with self.get_session() as session:
                    from sqlalchemy import text
                    # S166: removed asyncio.wait_for — client-side cancellation corrupts
                    # asyncpg protocol state (S162 P0). Server-side SET statement_timeout
                    # (set by get_session) handles hung queries safely via PG wire protocol.
                    result = await session.execute(text("SELECT 1"))
                    test_value = result.scalar()
                    if test_value != 1:
                        raise DatabaseError(
                            f"Database verification failed - test query returned {test_value} instead of 1",
                            operation="verify_database",
                            table=None,
                            test_value=test_value
                        )

                logger.info("Database verification passed - connection is working")
                return
            except DatabaseError:
                raise
            except Exception as e:
                last_err = e
                if attempt < 2:
                    wait = 2 ** attempt
                    logger.warning("DB verify attempt %d failed (%s), retrying in %ds", attempt + 1, type(e).__name__, wait)
                    # Dispose poisoned connections before retry
                    try:
                        if self.engine:
                            await self.engine.dispose()
                    except Exception as _disp_err:
                        logger.debug("db_engine_dispose_failed", error=str(_disp_err))
                    await asyncio.sleep(wait)
        logger.error("Database verification failed after 3 attempts: %s", ascii(str(last_err)))
        raise DatabaseError(
            f"Database verification failed: {str(last_err)}",
            operation="verify_database",
            table=None
        ) from last_err

    async def _init_postgres(self, database_url: str) -> None:
        """Initialize PostgreSQL database connection."""
        url = database_url.strip()
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif not url.startswith("postgresql+asyncpg://"):
            url = f"postgresql+asyncpg://{url}" if "://" not in url else url
        
        logger.info("Initializing PostgreSQL database")
        pool_size = settings.DB_POOL_SIZE  # Default: 12 (settings.py)
        max_overflow = settings.DB_MAX_OVERFLOW  # Default: 2 (settings.py)
        pool_timeout = settings.DB_POOL_TIMEOUT  # Default: 30 (settings.py)
        connect_timeout = getattr(settings, "DB_CONNECT_TIMEOUT", 15)
        db_ssl = getattr(settings, "DB_SSL", False)

        stmt_cache = 0  # PgBouncer transaction mode — prepared statements incompatible

        # S152: PgBouncer rejects server_settings startup params (transaction pooling).
        # Server-side ALTER SYSTEM (300s) is the fallback. S159 C20 adds per-session
        # SET statement_timeout in _SemaphoreSession.__aenter__ (default 60s from
        # DB_STATEMENT_TIMEOUT_MS setting). This is PgBouncer-compatible (proven S157).
        connect_args: dict = {"statement_cache_size": stmt_cache, "timeout": connect_timeout, "ssl": db_ssl}
        _pool_recycle = int(getattr(settings, "DB_POOL_RECYCLE", 600))  # S141: 1h→10min default
        self.engine = create_async_engine(
            url,
            echo=False,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=_pool_recycle,
            pool_timeout=pool_timeout,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("session_factory created [id=%s, engine=%s]", id(self.session_factory), id(self.engine))
        total_connections = pool_size + max_overflow
        # S151: Use PgBouncer effective pool for warning thresholds when configured
        _effective_pool = getattr(settings, "DB_EFFECTIVE_POOL_SIZE", 0) if settings else 0
        _warn_pool = _effective_pool if _effective_pool > 0 else total_connections
        # Application-level semaphore — prevents thundering herd on DB pool.
        # Set to total_connections (pool_size + overflow) so ALL available connections
        # can be used. The 30s timeout on acquire() prevents permanent hangs.
        _sem_limit = max(total_connections, 3)
        self._db_semaphore = asyncio.Semaphore(_sem_limit)
        logger.info("DB semaphore initialized: limit=%d (pool_size=%d, total=%d)", _sem_limit, pool_size, total_connections)
        # Pool event listeners for monitoring
        from sqlalchemy import event as sa_event
        warn_threshold = max(_warn_pool - 1, int(_warn_pool * 0.9))  # Only warn when nearly exhausted
        import time as _time_mod
        _last_pool_warn = [0.0]  # Rate-limit: max once per 60 seconds
        @sa_event.listens_for(self.engine.sync_engine, "connect")
        def _on_connect(dbapi_conn, connection_record):
            logger.debug("DB connection opened", pool_size=self.engine.pool.size(), checked_out=self.engine.pool.checkedout())
        @sa_event.listens_for(self.engine.sync_engine, "close")
        def _on_close(dbapi_conn, connection_record):
            checked_out = self.engine.pool.checkedout()
            now = _time_mod.monotonic()
            if checked_out >= warn_threshold and now - _last_pool_warn[0] > 60:
                _last_pool_warn[0] = now
                logger.warning(f"DB pool near exhaustion: {checked_out}/{_warn_pool} connections checked out")

        # S136 FIX: Invalidate dead connections so the pool replaces them.
        # Without this, ConnectionDoesNotExistError on mid-query connection death
        # returns the broken connection to the pool for reuse, causing cascading
        # failures across all bots.
        @sa_event.listens_for(self.engine.sync_engine, "handle_error")
        def _on_handle_error(context):
            _err_str = str(context.original_exception)
            if ("connection was closed" in _err_str
                    or "ConnectionDoesNotExistError" in type(context.original_exception).__name__
                    or "InterfaceError" in type(context.original_exception).__name__):
                if context.connection is not None:
                    try:
                        context.invalidate_pool_on_disconnect = True
                    except AttributeError:
                        pass
                    try:
                        context.connection.invalidate()
                    except Exception as _inv_err:
                        logger.debug("db_connection_invalidation_failed", error=str(_inv_err))
                logger.warning("DB connection invalidated (dead connection detected)",
                               error=type(context.original_exception).__name__)
        try:
            async with asyncio.timeout(10):
                async with self.engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
        except Exception as create_err:
            # Downgrade from WARNING to DEBUG. create_all fires on every startup and
            # may fail when idle-in-transaction sessions hold table locks.
            # All schema is managed via run_migrations.py; create_all is just a safety net.
            logger.debug("create_all skipped (tables should exist from migrations): %s", type(create_err).__name__)
        self._engine_loop_id = id(asyncio.get_running_loop())
        # S145: Periodic pool health logging — 60s interval, visible in journalctl
        self._pool_health_task = asyncio.create_task(self._log_pool_health(total_connections))
        logger.info("PostgreSQL database initialized successfully")

    async def _log_pool_health(self, total_connections: int) -> None:
        """S145: Log pool status every 60s for connection leak visibility."""
        while True:
            try:
                await asyncio.sleep(60)
                pool = self.engine.pool
                checked_out = pool.checkedout()
                checked_in = pool.checkedin()
                overflow = pool.overflow()
                try:
                    sem_available = self._db_semaphore._value if self._db_semaphore else -1
                except AttributeError:
                    sem_available = -1  # Semaphore internal API changed
                logger.info("db_pool_health",
                            checked_out=checked_out,
                            checked_in=checked_in,
                            overflow=overflow,
                            total=total_connections,
                            semaphore_available=sem_available)
            except asyncio.CancelledError:
                break
            except Exception as _ph_err:
                logger.debug("db_pool_health_check_failed", error=str(_ph_err))

    def get_session(self, timeout: Optional[float] = None):
        """
        Get a database session (use with async context manager).
        Acquires semaphore to limit concurrent sessions and prevent pool exhaustion.
        Usage: async with self.db.get_session() as session:
               async with self.db.get_session(timeout=10) as session:  # 10s timeout
        """
        if self.session_factory is None:
            raise DatabaseError(
                "Database not initialized. Set DATABASE_URL and initialize.",
                operation="get_session",
                table=None
            )
        # Return a context manager that acquires semaphore before creating session
        return _SemaphoreSession(self.session_factory, getattr(self, '_db_semaphore', None),
                                 timeout=timeout)

    def get_raw_session(self):
        """
        Get a database session WITHOUT semaphore protection.
        Used ONLY for lightweight operations that must not deadlock with semaphore:
        - PostgreSQL advisory locks (held for duration of caller's work)
        This bypasses the semaphore so it cannot cause deadlocks when the caller
        needs additional get_session() calls inside the locked section.
        The SQLAlchemy pool still limits total connections via max_overflow.
        """
        if self.session_factory is None:
            raise DatabaseError(
                "Database not initialized. Set DATABASE_URL and initialize.",
                operation="get_raw_session",
                table=None
            )
        return _SemaphoreSession(self.session_factory, None)  # No semaphore

    async def close(self) -> None:
        _task = getattr(self, "_pool_health_task", None)
        if _task and not _task.done():
            _task.cancel()
            try:
                await _task
            except (asyncio.CancelledError, Exception):
                pass
        if self.engine:
            try:
                await self.engine.dispose()
                logger.info("Database closed successfully")
            except Exception as e:
                logger.warning(f"Error disposing database engine: {str(e)}")
        else:
            logger.debug("No database engine to close")
    
    async def get_flag(self, flag_name: str, default: bool = True) -> bool:
        """Read a feature flag from the database.  Returns `default` on any error (fail-open).

        Flags are seeded by migration 038_feature_flags.sql.  Update a flag at runtime via:
            UPDATE feature_flags SET enabled = false, updated_at = NOW()
              WHERE flag_name = 'mirrorbot_buy_enabled';
        The change propagates to the bot within one scan cycle (no restart needed).
        """
        if self.session_factory is None:
            return default
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as sess:
                row = await sess.execute(
                    _sa_text("SELECT enabled FROM feature_flags WHERE flag_name = :n"),
                    {"n": flag_name},
                )
                result = row.scalar_one_or_none()
                return bool(result) if result is not None else default
        except Exception as _ff_err:
            logger.debug("db_feature_flag_query_failed", error=str(_ff_err))
            return default

    async def bulk_insert_markets(self, markets: List[Dict[str, Any]]) -> Tuple[int, int]:
        """Bulk insert or update markets in the database. Returns (processed_count, failed_count)."""
        if self.session_factory is None:
            error_msg = "Database not available. Check database connection and ensure PostgreSQL is running."
            logger.error(error_msg)
            logger.error("Database session_factory is None - database may not have been initialized properly")
            raise DatabaseError(
                error_msg,
                operation="bulk_insert_markets",
                table="markets",
                market_count=len(markets)
            )
        
        # Phase 1: validate and build dicts (no DB, CPU only).
        # This separates validation from I/O so we can do a single bulk upsert.
        failed_count = 0
        markets_with_tokens = 0
        valid_dicts: List[Dict[str, Any]] = []

        for market_data in markets:
            try:
                if getattr(settings, "PRE_INSERT_VALIDATION", True):
                    from base_engine.utils.validation import validate_market_dict
                    ok, err = validate_market_dict(market_data)
                    if not ok:
                        logger.debug("Pre-insert validation skipped market: %s", err)
                        failed_count += 1
                        continue
                end_date_iso = market_data.get("end_date_iso")
                if isinstance(end_date_iso, datetime) and getattr(end_date_iso, "tzinfo", None) is not None:
                    end_date_iso = _naive_utc(end_date_iso)
                _liq = market_data.get("liquidity", 0.0)
                _vol = market_data.get("volume", 0.0)
                try:
                    liquidity = float(_liq) if _liq is not None else 0.0
                    volume = float(_vol) if _vol is not None else 0.0
                except (TypeError, ValueError):
                    liquidity, volume = 0.0, 0.0
                resolved_at_val = market_data.get("resolved_at")
                if isinstance(resolved_at_val, datetime) and getattr(resolved_at_val, "tzinfo", None) is not None:
                    resolved_at_val = _naive_utc(resolved_at_val)
                market_dict = {
                    "id": market_data.get("id"),
                    "condition_id": market_data.get("condition_id"),
                    "question": market_data.get("question"),
                    "description": market_data.get("description"),
                    # Normalize empty-string slugs to NULL — empty strings cause UniqueViolation
                    # on ix_markets_slug when multiple markets share "" as their slug.
                    "slug": market_data.get("slug") or None,
                    "category": market_data.get("category"),
                    "resolution_source": market_data.get("resolution_source"),
                    "end_date_iso": end_date_iso,
                    "image": market_data.get("image"),
                    "active": market_data.get("active", True),
                    "liquidity": liquidity,
                    "volume": volume,
                    "resolved": bool(market_data.get("resolved", False)),
                    "resolution": market_data.get("resolution"),
                    "resolved_at": resolved_at_val,
                    "yes_token_id": market_data.get("yes_token_id"),
                    "no_token_id": market_data.get("no_token_id"),
                    "yes_price": market_data.get("yes_price"),
                    "no_price": market_data.get("no_price"),
                    "outcome_prices": market_data.get("outcome_prices"),
                    "neg_risk": market_data.get("neg_risk", False),
                    "outcome_count": market_data.get("outcome_count", 2),
                }
                if market_dict.get("yes_token_id"):
                    markets_with_tokens += 1
                valid_dicts.append(market_dict)
            except Exception as e:
                failed_count += 1
                logger.warning(f"Failed to prepare market {market_data.get('id', 'unknown')}: {str(e)}")

        if not valid_dicts:
            logger.warning("No markets were processed (all failed validation)")
            raise DatabaseError(
                f"All {len(markets)} markets failed validation or processing. "
                "Check logs for specific errors.",
                operation="bulk_insert_markets",
                table="markets",
                market_count=len(markets),
                processed_count=0,
            )

        # Phase 2: single bulk upsert (ON CONFLICT ON PK → UPDATE).
        # Replaces N individual merge() calls — 5-10s for 1,000 markets → <500ms.
        # Falls back to per-row merge if bulk fails (e.g. slug unique violation).
        #
        # Slug collision defence:
        #   1. Empty slugs → NULL (done above in Phase 1).
        #   2. Deduplicate within batch: if two markets share a non-NULL slug, nullify all but last.
        #   3. ON CONFLICT update deliberately excludes `slug` to avoid stomping another market's slug.
        _seen_slugs: set = set()
        for _d in reversed(valid_dicts):
            _sl = _d.get("slug")
            if _sl is not None:
                if _sl in _seen_slugs:
                    _d["slug"] = None  # Nullify duplicate slug within this batch
                else:
                    _seen_slugs.add(_sl)

        from sqlalchemy.dialects.postgresql import insert as pg_insert
        processed_count = 0

        # S120: Nullify slugs that collide with EXISTING rows under a different id.
        # Without this, new markets reusing old slugs cause UniqueViolation on ix_markets_slug,
        # failing the entire bulk INSERT (which uses ON CONFLICT on PK 'id', not 'slug').
        _batch_slugs = {d["slug"]: d["id"] for d in valid_dicts if d.get("slug")}
        if _batch_slugs:
            try:
                from sqlalchemy import text as _sa_text
                async with self.get_session() as _slug_sess:
                    _slug_rows = await _slug_sess.execute(
                        _sa_text("SELECT id, slug FROM markets WHERE slug = ANY(:slugs)"),
                        {"slugs": list(_batch_slugs.keys())},
                    )
                    for _row in _slug_rows.all():
                        _existing_id, _existing_slug = str(_row[0]), _row[1]
                        _batch_id = str(_batch_slugs.get(_existing_slug, ""))
                        if _existing_slug and _batch_id != _existing_id:
                            # Slug belongs to a different id — nullify in batch to avoid collision
                            for _d in valid_dicts:
                                if _d.get("slug") == _existing_slug and str(_d["id"]) != _existing_id:
                                    _d["slug"] = None
            except Exception as _slug_err:
                logger.debug("Slug collision pre-check failed (non-critical): %s", _slug_err)

        async with self.get_session() as session:
            try:
                async with session.begin():
                    stmt = pg_insert(Market.__table__).values(valid_dicts)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "condition_id": stmt.excluded.condition_id,
                            "question": stmt.excluded.question,
                            "description": stmt.excluded.description,
                            "category": stmt.excluded.category,
                            "resolution_source": stmt.excluded.resolution_source,
                            "end_date_iso": stmt.excluded.end_date_iso,
                            "image": stmt.excluded.image,
                            "active": stmt.excluded.active,
                            "liquidity": stmt.excluded.liquidity,
                            "volume": stmt.excluded.volume,
                            "resolved": stmt.excluded.resolved,
                            "resolution": stmt.excluded.resolution,
                            "resolved_at": stmt.excluded.resolved_at,
                            "yes_token_id": stmt.excluded.yes_token_id,
                            "no_token_id": stmt.excluded.no_token_id,
                            "yes_price": stmt.excluded.yes_price,
                            "no_price": stmt.excluded.no_price,
                            "outcome_prices": stmt.excluded.outcome_prices,
                            "neg_risk": stmt.excluded.neg_risk,
                            "outcome_count": stmt.excluded.outcome_count,
                            # slug intentionally excluded from UPDATE — preserves existing slug
                            # to avoid UniqueViolation when the same slug is held by a different id
                        },
                    )
                    await session.execute(stmt)
                    processed_count = len(valid_dicts)
            except Exception as e:
                # Bulk path failed — fall back to per-row merge.
                logger.warning(
                    "bulk_insert_markets fast-path failed, falling back to per-row merge: %s", e
                )

        if processed_count == 0:
            # Fallback: per-row merge with SAVEPOINTs so one row failure
            # doesn't kill the transaction for remaining rows (S49 fix).
            async with self.get_session() as session:
                try:
                    async with session.begin():
                        for md in valid_dicts:
                            try:
                                async with session.begin_nested():
                                    await session.merge(Market(**md))
                                processed_count += 1
                            except Exception as row_err:
                                failed_count += 1
                                logger.debug("market merge failed %s: %s", md.get("id"), row_err)
                except Exception as e:
                    logger.error(f"Failed to bulk process markets: {str(e)}", exc_info=True)
                    raise

        if processed_count > 0:
            if failed_count > 0:
                logger.warning(
                    f"Bulk processed {processed_count}/{len(markets)} markets ({failed_count} failed)",
                    markets_with_token_ids=markets_with_tokens,
                )
            else:
                logger.debug(
                    f"Bulk processed {processed_count} markets successfully (inserted/updated)",
                    markets_with_token_ids=markets_with_tokens,
                )
        # H7 FIX: Moved return to after the optional verification block.
        # Previously: return was on line 872, making lines 873-886 dead code — verification NEVER ran.
        if getattr(settings, "VERIFY_SAVE_AFTER_INSERT", False) and markets:
            first_id = (markets[0] or {}).get("id") if markets else None
            if first_id:
                try:
                    async with self.get_session() as session:
                        from sqlalchemy import select
                        r = await session.execute(select(Market).where(Market.id == first_id).limit(1))
                        if r.scalar_one_or_none() is None:
                            logger.warning(
                                "Post-save verify: sample market not found after bulk_insert_markets",
                                market_id=first_id,
                            )
                except Exception as verify_err:
                    logger.debug("Post-save verify check failed (non-fatal): %s", verify_err)
        return (processed_count, failed_count)

    async def bulk_insert_prices(self, prices: List[Dict[str, Any]]) -> int:
        """Bulk insert or update price records in the database. Returns count of successfully processed rows."""
        if self.session_factory is None:
            raise DatabaseError(
                "Database not available. Cannot insert prices.",
                operation="bulk_insert_prices",
                table="market_prices",
                price_count=len(prices)
            )
        
        async with self.get_session() as session:
            try:
                from base_engine.data.database_partitioning import get_partition_key
                
                async with session.begin():
                    success_count = 0
                    fail_count = 0
                    for price_data in prices:
                        try:
                            # Set partition_month if timestamp is provided; normalize for TIMESTAMP WITHOUT TIME ZONE
                            if 'timestamp' in price_data and price_data['timestamp']:
                                timestamp = price_data['timestamp']
                                if isinstance(timestamp, str):
                                    from dateutil.parser import parse
                                    timestamp = parse(timestamp)
                                timestamp = _naive_utc(timestamp)
                                price_data['timestamp'] = timestamp
                                price_data['partition_month'] = get_partition_key(timestamp)
                            # Final coercion: ensure timestamp in dict is naive before MarketPrice() (asyncpg)
                            if price_data.get('timestamp') is not None:
                                price_data['timestamp'] = _naive_utc(price_data['timestamp'])
                            
                            price = MarketPrice(**price_data)
                            await session.merge(price)
                            success_count += 1
                        except Exception as e:
                            fail_count += 1
                            if fail_count <= 5:  # Only log first 5 failures to avoid log spam
                                logger.warning(f"Failed to process price data: {str(e)}")
                            continue
                    # S156: Upsert latest prices into market_prices_latest (ORM write path)
                    try:
                        _latest_by_token: dict = {}
                        for pd in prices:
                            tid = pd.get("token_id")
                            ts = pd.get("timestamp")
                            if tid and ts and (tid not in _latest_by_token or ts > _latest_by_token[tid]["timestamp"]):
                                _latest_by_token[tid] = pd
                        if _latest_by_token:
                            _lp_sql = text(
                                "INSERT INTO market_prices_latest (token_id, market_id, price, timestamp) "
                                "VALUES (:token_id, :market_id, :price, :timestamp) "
                                "ON CONFLICT (token_id) DO UPDATE SET "
                                "  price = EXCLUDED.price, market_id = EXCLUDED.market_id, "
                                "  timestamp = EXCLUDED.timestamp "
                                "WHERE EXCLUDED.timestamp > market_prices_latest.timestamp"
                            )
                            for _lrow in _latest_by_token.values():
                                await session.execute(_lp_sql, {
                                    "token_id": _lrow.get("token_id"),
                                    "market_id": _lrow.get("market_id"),
                                    "price": float(_lrow.get("price", 0)),
                                    "timestamp": _lrow.get("timestamp"),
                                })
                    except Exception as _lp_err:
                        logger.debug("market_prices_latest orm upsert failed (non-fatal): %s", _lp_err)
                    if fail_count > 0:
                        logger.warning(f"Bulk insert prices: {success_count} succeeded, {fail_count} failed out of {len(prices)}")
                    else:
                        logger.debug(f"Bulk processed {len(prices)} price records")
                    # FIX NEW-7: Return actual success count, not None
                    return success_count
            except Exception as e:
                logger.error(f"Failed to bulk process prices: {str(e)}", exc_info=True)
                await session.rollback()
                raise

    async def bulk_insert_prices_raw(self, prices: List[Dict[str, Any]], batch_size: int = 100) -> int:
        """
        Bulk insert price records using raw INSERT ... ON CONFLICT DO NOTHING.
        Requires unique constraint uq_market_prices_market_token_timestamp on (market_id, token_id, timestamp).
        Run schema/add_market_prices_unique_constraint.sql once. Returns count of rows inserted (approx).
        """
        if self.session_factory is None:
            raise DatabaseError(
                "Database not available. Cannot insert prices.",
                operation="bulk_insert_prices_raw",
                table="market_prices",
                price_count=len(prices)
            )
        from base_engine.data.database_partitioning import get_partition_key
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from dateutil.parser import parse as date_parse

        normalized: List[Dict[str, Any]] = []
        for price_data in prices:
            try:
                ts = price_data.get("timestamp")
                if ts is None:
                    continue
                if isinstance(ts, str):
                    ts = date_parse(ts)
                ts = _naive_utc(ts)
                row = {
                    "market_id": price_data.get("market_id"),
                    "token_id": price_data.get("token_id"),
                    "price": float(price_data.get("price", 0)),
                    "side": price_data.get("side"),
                    "timestamp": ts,
                    "partition_month": get_partition_key(ts),
                }
                if row["market_id"] is None or row["token_id"] is None:
                    continue
                if getattr(settings, "PRE_INSERT_VALIDATION", True):
                    from base_engine.utils.validation import validate_price_row
                    ok, _ = validate_price_row(row)
                    if not ok:
                        continue
                normalized.append(row)
            except (ValueError, TypeError) as e:
                logger.debug("Skip price row: %s", e)
                continue

        _filtered_count = len(prices) - len(normalized)
        if _filtered_count > 0:
            logger.warning(
                "bulk_insert_prices_raw: %d/%d rows filtered during normalization",
                _filtered_count, len(prices),
            )

        if not normalized:
            if prices:
                logger.warning(
                    "bulk_insert_prices_raw: all %s rows filtered (check timestamp, market_id, token_id, price 0-1)",
                    len(prices),
                )
            return 0

        inserted = 0
        # Use get_raw_session() — streaming persister calls this every 10s,
        # and the semaphore must stay available for bot scan queries.
        async with self.get_raw_session() as session:
            try:
                for i in range(0, len(normalized), batch_size):
                    chunk = normalized[i : i + batch_size]
                    stmt = pg_insert(MarketPrice).values(chunk).on_conflict_do_nothing(
                        index_elements=["market_id", "token_id", "timestamp"]
                    )
                    await session.execute(stmt)
                    inserted += len(chunk)
                # S156: Upsert latest prices into market_prices_latest (tiny table for O(1) lookups)
                try:
                    _latest_sql = text(
                        "INSERT INTO market_prices_latest (token_id, market_id, price, timestamp) "
                        "VALUES (:token_id, :market_id, :price, :timestamp) "
                        "ON CONFLICT (token_id) DO UPDATE SET "
                        "  price = EXCLUDED.price, market_id = EXCLUDED.market_id, "
                        "  timestamp = EXCLUDED.timestamp "
                        "WHERE EXCLUDED.timestamp > market_prices_latest.timestamp"
                    )
                    # Deduplicate: keep only latest per token_id from this batch
                    _latest_by_token: dict = {}
                    for row in normalized:
                        tid = row.get("token_id")
                        if tid and (tid not in _latest_by_token or row["timestamp"] > _latest_by_token[tid]["timestamp"]):
                            _latest_by_token[tid] = row
                    for _lrow in _latest_by_token.values():
                        await session.execute(_latest_sql, {
                            "token_id": _lrow["token_id"],
                            "market_id": _lrow.get("market_id"),
                            "price": _lrow["price"],
                            "timestamp": _lrow["timestamp"],
                        })
                except Exception as _lp_err:
                    logger.debug("market_prices_latest upsert failed (non-fatal): %s", _lp_err)
                await session.commit()
                logger.debug("bulk_insert_prices_raw: %s rows (batches of %s)", inserted, batch_size)
                if getattr(settings, "VERIFY_SAVE_AFTER_INSERT", False) and normalized:
                    from sqlalchemy import select, func
                    sample = normalized[0]
                    r = await session.execute(
                        select(func.count())
                        .select_from(MarketPrice)
                        .where(
                            and_(
                                MarketPrice.market_id == sample.get("market_id"),
                                MarketPrice.token_id == sample.get("token_id"),
                                MarketPrice.timestamp == sample.get("timestamp"),
                            )
                        )
                    )
                    if (r.scalar() or 0) == 0:
                        logger.warning(
                            "Post-save verify: sample price row not found after bulk_insert_prices_raw",
                            market_id=sample.get("market_id"),
                            token_id=sample.get("token_id"),
                        )
            except Exception as e:
                await session.rollback()
                if "uq_market_prices_market_token_timestamp" in str(e) or "unique" in str(e).lower():
                    logger.warning("Unique constraint missing? Run schema/add_market_prices_unique_constraint.sql: %s", e)
                raise
        return inserted

    async def delete_old_market_prices(self, older_than_days: int) -> int:
        """
        Delete market_prices older than N days (retention policy).
        Returns count of rows deleted.
        """
        if self.session_factory is None or older_than_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cutoff = _naive_utc(cutoff)
        try:
            async with self.get_session() as session:
                stmt = text(
                    "DELETE FROM market_prices WHERE timestamp < :cutoff"
                )
                result = await session.execute(stmt, {"cutoff": cutoff})
                await session.commit()
                count = result.rowcount or 0
                if count > 0:
                    logger.info("delete_old_market_prices: deleted %s rows older than %s days", count, older_than_days)
                return count
        except Exception as e:
            logger.warning("delete_old_market_prices failed: %s", e)
            return 0

    async def bulk_insert_trades(self, trades: List[Dict[str, Any]]) -> None:
        """
        Bulk insert trades with partition_month support.
        Retries on deadlock/transient errors (same pattern as bulk_insert_markets).
        
        Args:
            trades: List of trade dictionaries with fields:
                - id, market_id, token_id, user_address, side, size, price, timestamp
        """
        if self.session_factory is None:
            raise DatabaseError(
                "Database not available. Cannot insert trades.",
                operation="bulk_insert_trades",
                table="trades",
                trade_count=len(trades)
            )
        from base_engine.data.database_partitioning import get_partition_key

        # Pre-process all trades before touching the DB session
        prepared: List[Dict[str, Any]] = []
        for trade_data in trades:
            try:
                if getattr(settings, "PRE_INSERT_VALIDATION", True):
                    from base_engine.utils.validation import validate_trade_dict
                    ok, err = validate_trade_dict(trade_data)
                    if not ok:
                        logger.debug("Pre-insert validation skipped trade: %s", err)
                        continue
                if 'timestamp' in trade_data and trade_data['timestamp']:
                    timestamp = trade_data['timestamp']
                    if isinstance(timestamp, str):
                        from dateutil.parser import parse
                        timestamp = parse(timestamp)
                    timestamp = _naive_utc(timestamp)
                    trade_data['timestamp'] = timestamp
                    trade_data['partition_month'] = get_partition_key(timestamp)
                prepared.append(trade_data)
            except Exception as e:
                logger.warning(f"Failed to prepare trade data: {str(e)}")
                continue

        if not prepared:
            logger.debug("No valid trades to insert after preparation")
            return

        # Deduplicate within the batch by trade id (keep last occurrence)
        seen_ids: Dict[str, int] = {}
        for idx, td in enumerate(prepared):
            tid = td.get("id")
            if tid:
                seen_ids[tid] = idx
        if len(seen_ids) < len(prepared):
            prepared = [prepared[i] for i in sorted(seen_ids.values())]

        # Use raw INSERT ON CONFLICT to handle both PK and unique-constraint duplicates
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        chunk_size = 100
        max_retries = 3
        total_processed = 0
        for i in range(0, len(prepared), chunk_size):
            chunk = prepared[i:i + chunk_size]
            for attempt in range(max_retries):
                try:
                    # Use get_raw_session() — streaming persister calls this every 10s
                    async with self.get_raw_session() as session:
                        async with session.begin():
                            stmt = pg_insert(Trade.__table__).values(chunk)
                            # ON CONFLICT on PK → update size/price/side (upsert)
                            stmt = stmt.on_conflict_do_update(
                                index_elements=["id"],
                                set_={
                                    "size": stmt.excluded.size,
                                    "price": stmt.excluded.price,
                                    "side": stmt.excluded.side,
                                    "partition_month": stmt.excluded.partition_month,
                                },
                            )
                            await session.execute(stmt)
                    total_processed += len(chunk)
                    break  # chunk succeeded
                except Exception as e:
                    err_str = str(e).lower()
                    # Unique constraint violation (non-PK) — skip duplicates.
                    # Previously fell back to one-by-one INSERT per row (N+1 pattern).
                    # Now use a single bulk INSERT ... ON CONFLICT DO NOTHING to avoid
                    # 200+ serial INSERTs on conflict storms (e.g. 20% dup rate on 1000 trades).
                    if "unique" in err_str or "duplicate key" in err_str:
                        try:
                            async with self.get_session() as session:
                                async with session.begin():
                                    stmt = pg_insert(Trade.__table__).values(chunk)
                                    stmt = stmt.on_conflict_do_nothing()
                                    await session.execute(stmt)
                            total_processed += len(chunk)
                        except Exception as e2:
                            logger.warning(f"bulk_insert_trades fallback failed: {e2}")
                        break
                    is_retryable = (
                        "DeadlockDetectedError" in type(e).__name__
                        or "deadlock detected" in err_str
                        or "connection" in err_str
                        or "timeout" in err_str
                        or "closed" in err_str
                        or "transaction" in err_str
                    )
                    if is_retryable and attempt < max_retries - 1:
                        delay = 0.5 * (attempt + 1)
                        logger.warning(
                            "bulk_insert_trades chunk retrying",
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay_sec=delay,
                            chunk_offset=i,
                            error=str(e),
                        )
                        await asyncio.sleep(delay)
                    elif attempt == max_retries - 1:
                        logger.error(
                            f"bulk_insert_trades chunk failed after {max_retries} attempts: {str(e)}",
                            exc_info=True,
                        )
                    else:
                        logger.error(f"Failed to bulk process trades chunk: {str(e)}", exc_info=True)
                        break

        if total_processed > 0:
            logger.debug(f"Bulk processed {total_processed}/{len(prepared)} trade records")
        elif prepared:
            raise DatabaseError(
                f"bulk_insert_trades failed: 0/{len(prepared)} trades processed",
                operation="bulk_insert_trades",
                table="trades",
                trade_count=len(trades),
            )
    
    async def save_market_resolution(
        self,
        market_id: str,
        resolved: bool,
        resolution: Optional[str] = None,
        resolution_source_method: Optional[str] = None,
        resolved_at: Optional[datetime] = None
    ) -> None:
        """
        Save market resolution data to database.
        
        This stores resolution information in a learnable format for ML/analytics.
        
        Args:
            market_id: Market ID
            resolved: Whether market is resolved
            resolution: Resolution outcome (YES, NO, etc.)
            resolution_source_method: Source method ('gamma_api' or 'blockchain')
            resolved_at: When market was resolved
        """
        if self.session_factory is None:
            logger.warning("Database not available, skipping resolution save")
            return
        
        async with self.get_session() as session:
            try:
                from sqlalchemy import update
                
                # If resolved_at is missing, fall back to the market's end_date_iso
                # (prevents NULL resolved_at which bypasses the 6h temporal training guard)
                final_resolved_at = resolved_at
                if final_resolved_at is None and resolved:
                    from sqlalchemy import select as sa_select
                    row = (await session.execute(
                        sa_select(Market.end_date_iso).where(Market.id == market_id)
                    )).first()
                    if row and row[0]:
                        final_resolved_at = row[0]

                # Update existing market record with resolution data (naive UTC for DateTime columns)
                stmt = (
                    update(Market)
                    .where(Market.id == market_id)
                    .values(
                        resolved=resolved,
                        resolution=resolution,
                        resolution_source_method=resolution_source_method,
                        resolved_at=_naive_utc(final_resolved_at),
                        updated_at=_naive_utc(datetime.now(timezone.utc))
                    )
                )
                await session.execute(stmt)
                # Mark traded_markets resolved so backfill skips this market
                try:
                    from sqlalchemy import text as _sa_text2
                    await session.execute(
                        _sa_text2(
                            "UPDATE traded_markets SET resolved = TRUE, resolution = :res, resolved_at = :rat "
                            "WHERE market_id = :mid AND resolved = FALSE"
                        ),
                        {"mid": market_id, "res": resolution, "rat": _naive_utc(final_resolved_at)},
                    )
                except Exception as _tm_err:
                    logger.debug("db_traded_markets_update_skipped", error=str(_tm_err))
                await session.commit()

                logger.debug(
                    f"Saved resolution for market {market_id}",
                    resolved=resolved,
                    resolution=resolution,
                    source=resolution_source_method
                )

                # Update signal outcomes for learning (even if signals suck)
                if resolved and resolution and resolution.upper() in ("YES", "NO"):
                    await self._update_signal_outcomes(session, market_id, resolution)
            except Exception as e:
                logger.error(f"Failed to save market resolution: {str(e)}", exc_info=True)
                await session.rollback()
                # Don't raise - resolution save failure shouldn't break ingestion

    async def _update_signal_outcomes(
        self,
        session,
        market_id: str,
        resolution: str
    ) -> None:
        """Update outcome_correct for all signals for this market. Enables learning."""
        try:
            from sqlalchemy import update, case
            res_upper = resolution.upper()
            outcome_expr = case((Signal.direction == res_upper, True), else_=False)
            stmt = (
                update(Signal)
                .where(
                    Signal.market_id == market_id,
                    Signal.direction.in_(["YES", "NO"]),
                    Signal.outcome_correct.is_(None)
                )
                .values(
                    outcome_correct=outcome_expr,
                    resolution_at=_naive_utc(datetime.now(timezone.utc)),
                    market_resolution=res_upper
                )
            )
            result = await session.execute(stmt)
            if result.rowcount and result.rowcount > 0:
                logger.debug(f"Updated {result.rowcount} signal outcomes for market {market_id}")
        except Exception as e:
            logger.debug(f"Signal outcome update failed for {market_id}: {e}")

    async def update_market_token_ids(
        self,
        market_id: str,
        yes_token_id: Optional[str] = None,
        no_token_id: Optional[str] = None
    ) -> bool:
        """
        Update token IDs for an existing market (for full price history on next run).
        Used when we get token IDs from API path during price ingestion.
        """
        if self.session_factory is None or (not yes_token_id and not no_token_id):
            return False
        async with self.get_session() as session:
            try:
                from sqlalchemy import update
                values = {"updated_at": _naive_utc(datetime.now(timezone.utc))}
                if yes_token_id:
                    values["yes_token_id"] = str(yes_token_id)
                if no_token_id:
                    values["no_token_id"] = str(no_token_id)
                stmt = update(Market).where(Market.id == market_id).values(**values)
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.debug(f"update_market_token_ids failed for {market_id}: {e}")
                await session.rollback()
                return False
    
    async def save_market_price(
        self,
        market_id: str,
        token_id: str,
        price: float,
        timestamp: datetime,
        side: Optional[str] = None
    ):
        """
        Save a single market price to the database.
        
        Args:
            market_id: Market ID
            token_id: Token ID
            price: Price value
            timestamp: Timestamp for the price
            side: Order side (BUY/SELL) - optional but recommended for learnable data
        """
        timestamp = _naive_utc(timestamp)
        if self.session_factory is None:
            raise DatabaseError(
                "Database not available. Cannot save price.",
                operation="save_market_price",
                table="market_prices",
                market_id=market_id,
                token_id=token_id
            )
        
        async with self.get_session() as session:
            try:
                from base_engine.data.database_partitioning import get_partition_key
                
                price_obj = MarketPrice(
                    market_id=str(market_id),
                    token_id=str(token_id),
                    price=float(price),
                    timestamp=timestamp,
                    side=side,  # Store side for learnable data
                    partition_month=get_partition_key(timestamp)  # Set partition for performance
                )
                # Ensure naive UTC before merge (asyncpg / TIMESTAMP WITHOUT TIME ZONE)
                if price_obj.timestamp is not None and getattr(price_obj.timestamp, "tzinfo", None) is not None:
                    price_obj.timestamp = _naive_utc(price_obj.timestamp)
                await session.merge(price_obj)
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to save market price: {str(e)}", exc_info=True)
                await session.rollback()
                raise
    
    async def get_softest_markets(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Get markets with highest liquidity and volume."""
        if self.session_factory is None:
            raise DatabaseError(
                "Database not available. Cannot retrieve markets.",
                operation="get_softest_markets",
                table="markets"
            )
        async with self.get_session() as session:
            from sqlalchemy import select, text

            query = text("""
                SELECT * FROM markets
                WHERE active = TRUE
                ORDER BY liquidity DESC, volume DESC
                LIMIT :limit
            """)
            result = await session.execute(query, {"limit": limit})
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]

    async def get_resolved_market_ids(self, limit: int = 50000) -> set:
        """Return set of market ids that are already resolved (YES/NO). Used to skip re-fetch (M3)."""
        if self.session_factory is None:
            return set()
        try:
            async with self.get_session() as session:
                r = await session.execute(text("""
                    SELECT id FROM markets
                    WHERE resolved = TRUE AND resolution IN ('YES', 'NO')
                    LIMIT :lim
                """), {"lim": limit})
                rows = r.fetchall()
                return {str(row[0]).strip() for row in rows if row[0]}
        except Exception as e:
            logger.debug("get_resolved_market_ids failed: %s", e)
            return set()

    async def get_markets_for_price_ingestion(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get markets for price ingestion. When PRICE_INGESTION_STALE_FIRST: order by oldest/no price first (rotation).
        Otherwise order by volume (highest first). P4: excludes markets with price_fetch_attempts >= PRICE_FETCH_EMPTY_MAX_ATTEMPTS."""
        if self.session_factory is None:
            return []
        from config.settings import settings
        stale_first = getattr(settings, "PRICE_INGESTION_STALE_FIRST", True)
        max_empty = getattr(settings, "PRICE_FETCH_EMPTY_MAX_ATTEMPTS", 5)
        async with self.get_session() as session:
            from sqlalchemy import text
            # P4: skip markets that repeatedly return empty price history (requires migration 011)
            p4_filter = "AND (m.price_fetch_attempts IS NULL OR m.price_fetch_attempts < :max_empty)" if max_empty > 0 else ""
            if stale_first:
                # market_prices.market_id may be m.id (historical ingestion) or m.condition_id (WS streaming)
                query = text(f"""
                    SELECT m.* FROM markets m
                    LEFT JOIN (
                        SELECT market_id, MAX(timestamp) AS last_ts
                        FROM market_prices GROUP BY market_id
                    ) lp ON (lp.market_id = m.id OR lp.market_id = m.condition_id)
                    WHERE m.active = TRUE
                    AND (
                        (m.yes_token_id IS NOT NULL AND m.yes_token_id != '')
                        OR (m.no_token_id IS NOT NULL AND m.no_token_id != '')
                    )
                    {p4_filter}
                    ORDER BY lp.last_ts ASC NULLS FIRST, COALESCE(m.volume, 0) DESC NULLS LAST
                    LIMIT :limit
                """)
            else:
                p4_filter_alt = "AND (price_fetch_attempts IS NULL OR price_fetch_attempts < :max_empty)" if max_empty > 0 else ""
                query = text(f"""
                    SELECT * FROM markets
                    WHERE active = TRUE
                    AND (
                        (yes_token_id IS NOT NULL AND yes_token_id != '')
                        OR (no_token_id IS NOT NULL AND no_token_id != '')
                    )
                    {p4_filter_alt}
                    ORDER BY COALESCE(volume, 0) DESC NULLS LAST, COALESCE(liquidity, 0) DESC NULLS LAST
                    LIMIT :limit
                """)
            params = {"limit": limit, "max_empty": max_empty}
            result = await session.execute(query, params)
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]

    async def record_empty_price_fetch(self, market_id: str) -> None:
        """P4: Record that we got an empty price history for this market. Run migration 011 first."""
        if not self.session_factory or not market_id:
            return
        try:
            async with self.get_session() as session:
                await session.execute(
                    text("""
                        UPDATE markets
                        SET price_fetch_attempts = COALESCE(price_fetch_attempts, 0) + 1,
                            last_price_fetch_empty = NOW() AT TIME ZONE 'UTC'
                        WHERE id = :id
                    """),
                    {"id": str(market_id).strip()},
                )
                await session.commit()
        except Exception as e:
            if "price_fetch_attempts" in str(e) or "column" in str(e).lower():
                logger.debug("P4 columns missing? Run schema/migrations/011_price_fetch_empty_tracking.sql: %s", e)
            else:
                logger.warning("record_empty_price_fetch failed: %s", e)

    async def reset_price_fetch_attempts(self, market_id: str) -> None:
        """P4: Reset empty-fetch counter after a successful price fetch. Run migration 011 first."""
        if not self.session_factory or not market_id:
            return
        try:
            async with self.get_session() as session:
                await session.execute(
                    text("""
                        UPDATE markets
                        SET price_fetch_attempts = 0, last_price_fetch_empty = NULL
                        WHERE id = :id
                    """),
                    {"id": str(market_id).strip()},
                )
                await session.commit()
        except Exception as e:
            if "price_fetch_attempts" in str(e) or "column" in str(e).lower():
                logger.debug("P4 columns missing? Run schema/migrations/011_price_fetch_empty_tracking.sql: %s", e)
            else:
                logger.warning("reset_price_fetch_attempts failed: %s", e)

    async def get_markets_needing_price_update(
        self, limit: int = 1000, skip_recent_hours: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Get markets for price ingestion, optionally excluding those with recent price data.
        When skip_recent_hours is set, excludes markets where MAX(market_prices.timestamp) >= NOW - skip_recent_hours."""
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            from sqlalchemy import text

            if skip_recent_hours is None or skip_recent_hours <= 0:
                return await self.get_markets_for_price_ingestion(limit)

            skip_seconds = int(skip_recent_hours * 3600)
            # market_prices.market_id may be m.id (historical ingestion) or m.condition_id (WS streaming)
            query = text("""
                SELECT m.* FROM markets m
                LEFT JOIN (
                    SELECT market_id, MAX(timestamp) AS last_price_ts
                    FROM market_prices
                    GROUP BY market_id
                ) lp ON (lp.market_id = m.id OR lp.market_id = m.condition_id)
                WHERE m.active = TRUE
                AND (
                    (m.yes_token_id IS NOT NULL AND m.yes_token_id != '')
                    OR (m.no_token_id IS NOT NULL AND m.no_token_id != '')
                )
                AND (lp.last_price_ts IS NULL OR lp.last_price_ts < NOW() - make_interval(secs => :skip_seconds))
                ORDER BY COALESCE(m.volume, 0) DESC NULLS LAST, COALESCE(m.liquidity, 0) DESC NULLS LAST
                LIMIT :limit
            """)
            result = await session.execute(
                query,
                {"limit": limit, "skip_seconds": skip_seconds},
            )
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]

    async def get_max_price_timestamps_for_markets(
        self, market_ids: List[str]
    ) -> Dict[Tuple[str, str], int]:
        """
        Get max timestamp (unix) per (market_id, token_id) for range-aware fetch.
        Returns dict keyed by (market_id, token_id) -> unix timestamp.
        """
        if not self.session_factory or not market_ids:
            return {}
        ids = [str(m) for m in market_ids[:5000] if m]
        if not ids:
            return {}
        async with self.get_session() as session:
            from sqlalchemy import text, bindparam
            query = text(
                "SELECT market_id, token_id, EXTRACT(EPOCH FROM MAX(timestamp))::bigint AS max_ts "
                "FROM market_prices WHERE market_id IN :ids GROUP BY market_id, token_id"
            ).bindparams(bindparam("ids", expanding=True))
            result = await session.execute(query, {"ids": ids})
            rows = result.fetchall()
            return {(str(r[0]), str(r[1])): int(r[2]) for r in rows if r[0] and r[1] and r[2] is not None}

    async def get_recent_market_ids(self, limit: int = 50) -> List[str]:
        """Return market IDs from DB ordered by updated_at desc, without filtering by active."""
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            from sqlalchemy import text
            query = text("SELECT id FROM markets ORDER BY updated_at DESC, id LIMIT :limit")
            result = await session.execute(query, {"limit": limit})
            rows = result.fetchall()
            return [str(row[0]) for row in rows if row[0] is not None]

    async def get_active_markets_for_activity(self, limit: int = 2000) -> List[Dict[str, Any]]:
        """Return active markets with id, volume, end_date_iso for activity scoring (SmartDataFetcher)."""
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Market.id, Market.volume, Market.end_date_iso)
                .where(Market.active == True)
                .limit(limit)
            )
            rows = result.fetchall()
            return [
                {"id": str(r[0]), "volume": float(r[1]) if r[1] is not None else 0.0, "end_date_iso": r[2]}
                for r in rows if r[0] is not None
            ]

    async def get_market_basic(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Return one market's id, end_date_iso, volume for FeatureStore (days_until_close)."""
        if self.session_factory is None or not market_id:
            return None
        async with self.get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Market.id, Market.end_date_iso, Market.volume).where(Market.id == market_id)
            )
            row = result.first()
            if row is None:
                return None
            return {
                "id": str(row[0]),
                "end_date_iso": row[1],
                "volume": float(row[2]) if row[2] is not None else 0.0,
            }

    async def get_trade_counts_since(self, since: datetime) -> Dict[str, int]:
        """Return per-market trade counts since given time (for activity scoring)."""
        if self.session_factory is None:
            return {}
        since_utc = _naive_utc(since) if getattr(since, "tzinfo", None) else since.replace(tzinfo=timezone.utc)
        since_utc = _naive_utc(since_utc)
        async with self.get_session() as session:
            from sqlalchemy import select, func
            result = await session.execute(
                select(Trade.market_id, func.count(Trade.id))
                .where(Trade.timestamp >= since_utc)
                .where(Trade.market_id.isnot(None))
                .group_by(Trade.market_id)
            )
            rows = result.fetchall()
            return {str(r[0]): int(r[1]) for r in rows if r[0] is not None}

    async def get_trade_volume_by_market_since(
        self, since: datetime
    ) -> Dict[str, Dict[str, Any]]:
        """Return per-market volume (sum size*price) and count since given time (for FeatureStore)."""
        if self.session_factory is None:
            return {}
        since_utc = _naive_utc(since) if getattr(since, "tzinfo", None) else since.replace(tzinfo=timezone.utc)
        since_utc = _naive_utc(since_utc)
        async with self.get_session() as session:
            from sqlalchemy import select, func
            result = await session.execute(
                select(
                    Trade.market_id,
                    func.coalesce(func.sum(Trade.size * Trade.price), 0).label("volume"),
                    func.count(Trade.id).label("count"),
                )
                .where(Trade.timestamp >= since_utc)
                .where(Trade.market_id.isnot(None))
                .group_by(Trade.market_id)
            )
            rows = result.fetchall()
            return {
                str(r[0]): {"volume_usd": float(r[1]), "count": int(r[2])}
                for r in rows if r[0] is not None
            }

    async def get_markets_with_token_ids(self, market_ids: List[str]) -> List[Dict[str, Any]]:
        """Return markets with id, yes_token_id, no_token_id for given IDs.
        Only rows with at least one non-empty token ID (required for CLOB price fetch)."""
        if self.session_factory is None or not market_ids:
            return []
        async with self.get_session() as session:
            from sqlalchemy import select, or_, and_
            # Filter: at least one of yes_token_id or no_token_id is non-null and non-empty
            result = await session.execute(
                select(Market.id, Market.yes_token_id, Market.no_token_id).where(
                    Market.id.in_(market_ids),
                    or_(
                        and_(Market.yes_token_id.isnot(None), Market.yes_token_id != ""),
                        and_(Market.no_token_id.isnot(None), Market.no_token_id != ""),
                    )
                )
            )
            rows = result.fetchall()
            return [
                {"id": str(r[0]), "yes_token_id": r[1], "no_token_id": r[2]}
                for r in rows if r[0] is not None
            ]
    
    async def get_active_markets_for_trading(self, limit: int = 200, min_liquidity: float = 0) -> List[Dict[str, Any]]:
        """DB-first market list: active markets with token IDs and recent price data. For prediction bots."""
        if self.session_factory is None:
            return []
        try:
            async with self.get_session() as session:
                query = text("""
                    SELECT m.* FROM markets m
                    WHERE m.active = TRUE
                    AND (
                        (m.yes_token_id IS NOT NULL AND m.yes_token_id != '')
                        OR (m.no_token_id IS NOT NULL AND m.no_token_id != '')
                    )
                    AND COALESCE(m.liquidity, 0) >= :min_liq
                    AND m.resolved = FALSE
                    ORDER BY COALESCE(m.volume, 0) DESC NULLS LAST
                    LIMIT :limit
                """)
                result = await session.execute(query, {"limit": limit, "min_liq": min_liquidity})
                rows = result.fetchall()
                return [dict(row._mapping) for row in rows]
        except Exception as e:
            logger.debug("get_active_markets_for_trading failed: %s", e)
            return []

    async def get_elite_traders(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Get elite traders sorted by profit and win rate."""
        if self.session_factory is None:
            raise DatabaseError(
                "Database not available. Cannot retrieve elite traders.",
                operation="get_elite_traders",
                table="users"
            )
        async with self.get_session() as session:
            from sqlalchemy import select
            # Select only needed columns; avoids is_likely_market_maker which may not exist (migration 008)
            result = await session.execute(
                select(User.address, User.total_profit, User.win_rate, User.total_trades, User.total_volume)
                .where(User.is_elite == True)
                .order_by(User.total_profit.desc(), User.win_rate.desc())
                .limit(limit)
            )
            rows = result.fetchall()
            return [{"address": r[0], "total_profit": r[1], "win_rate": r[2], "total_trades": r[3] or 0, "total_volume": r[4] or 0} for r in rows]

    async def get_user_resolution_counts(
        self, lookback_days: int = 365, regime_start: str = None
    ) -> List[Dict[str, Any]]:
        """
        Per-user counts of correct/incorrect by side from resolved markets (for Bayesian elite reliability).
        Returns list of { user_address, yes_correct, yes_total, no_correct, no_total }.
        Side is inferred from token_id or t.side; outcome from m.resolution.

        S150: regime_start filters out data from before a regime change (e.g. pre-S146).
        """
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            # S150: Use regime_start if provided, otherwise fall back to lookback_days
            _time_filter = "AND t.timestamp >= :regime_start" if regime_start else "AND t.timestamp >= NOW() - INTERVAL '1 day' * :days"
            q = text(f"""
                SELECT
                    t.user_address,
                    SUM(CASE WHEN (t.side IN ('YES','BUY') OR t.token_id = m.yes_token_id)
                             AND m.resolution = 'YES' THEN 1 ELSE 0 END) as yes_correct,
                    SUM(CASE WHEN (t.side IN ('YES','BUY') OR t.token_id = m.yes_token_id)
                             AND m.resolution IN ('YES','NO') THEN 1 ELSE 0 END) as yes_total,
                    SUM(CASE WHEN (t.side IN ('NO','SELL') OR t.token_id = m.no_token_id)
                             AND m.resolution = 'NO' THEN 1 ELSE 0 END) as no_correct,
                    SUM(CASE WHEN (t.side IN ('NO','SELL') OR t.token_id = m.no_token_id)
                             AND m.resolution IN ('YES','NO') THEN 1 ELSE 0 END) as no_total
                FROM trades t
                JOIN markets m ON t.market_id = m.id
                WHERE t.user_address IS NOT NULL
                AND t.market_id IS NOT NULL
                AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
                {_time_filter}
                GROUP BY t.user_address
            """)
            _params = {"regime_start": regime_start} if regime_start else {"days": lookback_days}
            result = await session.execute(q, _params)
            rows = result.mappings().all()
            return [
                {
                    "user_address": r["user_address"],
                    "yes_correct": r["yes_correct"] or 0,
                    "yes_total": r["yes_total"] or 0,
                    "no_correct": r["no_correct"] or 0,
                    "no_total": r["no_total"] or 0,
                }
                for r in rows
            ]

    async def get_user_resolution_counts_by_category(
        self, lookback_days: int = 365, regime_start: str = None
    ) -> List[Dict[str, Any]]:
        """Per-user per-category resolution counts for category-aware elite reliability.

        Returns list of { user_address, category, yes_correct, yes_total, no_correct, no_total }.

        S113: LEFT JOIN market_categories for markets outside top-1000 ingestion.
        Uses COALESCE(m.category, mc.category, 'unknown') to provide category data
        for whale trades on markets not in the markets table.
        S150: regime_start filters out data from before a regime change (e.g. pre-S146).
        """
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            # S150: Use regime_start if provided, otherwise fall back to lookback_days
            _time_filter = "AND t.timestamp >= :regime_start" if regime_start else "AND t.timestamp >= NOW() - INTERVAL '1 day' * :days"
            q = text(f"""
                SELECT
                    t.user_address,
                    LOWER(COALESCE(m.category, mc.category, 'unknown')) AS category,
                    SUM(CASE WHEN (t.side IN ('YES','BUY')
                                   OR t.token_id = m.yes_token_id
                                   OR t.token_id = mc.yes_token_id)
                             AND COALESCE(m.resolution, mc.resolution) = 'YES'
                             THEN 1 ELSE 0 END) as yes_correct,
                    SUM(CASE WHEN (t.side IN ('YES','BUY')
                                   OR t.token_id = m.yes_token_id
                                   OR t.token_id = mc.yes_token_id)
                             AND COALESCE(m.resolution, mc.resolution) IN ('YES','NO')
                             THEN 1 ELSE 0 END) as yes_total,
                    SUM(CASE WHEN (t.side IN ('NO','SELL')
                                   OR t.token_id = m.no_token_id
                                   OR t.token_id = mc.no_token_id)
                             AND COALESCE(m.resolution, mc.resolution) = 'NO'
                             THEN 1 ELSE 0 END) as no_correct,
                    SUM(CASE WHEN (t.side IN ('NO','SELL')
                                   OR t.token_id = m.no_token_id
                                   OR t.token_id = mc.no_token_id)
                             AND COALESCE(m.resolution, mc.resolution) IN ('YES','NO')
                             THEN 1 ELSE 0 END) as no_total
                FROM trades t
                LEFT JOIN markets m ON t.market_id = m.id
                LEFT JOIN market_categories mc ON t.market_id = mc.condition_id
                WHERE t.user_address IS NOT NULL
                AND t.market_id IS NOT NULL
                AND (m.resolved = TRUE OR mc.resolved = TRUE)
                AND COALESCE(m.resolution, mc.resolution) IN ('YES', 'NO')
                {_time_filter}
                GROUP BY t.user_address, LOWER(COALESCE(m.category, mc.category, 'unknown'))
            """)
            _params = {"regime_start": regime_start} if regime_start else {"days": lookback_days}
            result = await session.execute(q, _params)
            rows = result.mappings().all()
            return [
                {
                    "user_address": r["user_address"],
                    "category": r["category"],
                    "yes_correct": r["yes_correct"] or 0,
                    "yes_total": r["yes_total"] or 0,
                    "no_correct": r["no_correct"] or 0,
                    "no_total": r["no_total"] or 0,
                }
                for r in rows
            ]

    async def upsert_market_category(
        self,
        condition_id: str,
        category: str,
        question: str = "",
        yes_token_id: str = "",
        no_token_id: str = "",
        resolved: bool = False,
        resolution: Optional[str] = None,
    ) -> None:
        """S113: Upsert market category for markets outside top-1000 ingestion.

        Populates market_categories table used by elite_reliability tracker
        to provide per-whale per-category win rates (Factor 1).
        """
        if self.session_factory is None or not condition_id:
            return
        try:
            async with self.get_session() as session:
                await session.execute(
                    text(
                        "INSERT INTO market_categories "
                        "(condition_id, category, question, yes_token_id, no_token_id, resolved, resolution) "
                        "VALUES (:cid, :cat, :q, :yes_tid, :no_tid, :resolved, :resolution) "
                        "ON CONFLICT (condition_id) DO UPDATE SET "
                        "category = EXCLUDED.category, "
                        "question = COALESCE(NULLIF(EXCLUDED.question, ''), market_categories.question), "
                        "yes_token_id = COALESCE(NULLIF(EXCLUDED.yes_token_id, ''), market_categories.yes_token_id), "
                        "no_token_id = COALESCE(NULLIF(EXCLUDED.no_token_id, ''), market_categories.no_token_id), "
                        "resolved = EXCLUDED.resolved OR market_categories.resolved, "
                        "resolution = COALESCE(EXCLUDED.resolution, market_categories.resolution)"
                    ),
                    {
                        "cid": condition_id,
                        "cat": category or "unknown",
                        "q": question or "",
                        "yes_tid": yes_token_id or "",
                        "no_tid": no_token_id or "",
                        "resolved": resolved,
                        "resolution": resolution,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.debug("upsert_market_category failed for %s: %s", condition_id[:16], e)

    async def get_user_trade_counts(
        self, addresses: List[str], lookback_days: int = 90
    ) -> List[Dict[str, Any]]:
        """S113: Get trade counts per user for F3 conviction signal.

        Returns list of { user_address, num_trades } for the given addresses.
        Used to supplement watchlist data when Data API doesn't return totalTrades.
        """
        if self.session_factory is None or not addresses:
            return []
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(
                        "SELECT user_address, COUNT(*) as num_trades "
                        "FROM trades "
                        "WHERE user_address = ANY(:addrs) "
                        "AND timestamp >= NOW() - INTERVAL '1 day' * :days "
                        "GROUP BY user_address"
                    ),
                    {"addrs": addresses, "days": lookback_days},
                )
                return [
                    {
                        "user_address": r["user_address"],
                        "num_trades": r["num_trades"] or 0,
                    }
                    for r in result.mappings().all()
                ]
        except Exception as e:
            logger.debug("get_user_trade_counts failed: %s", e)
            return []

    async def upsert_users(self, users: List[Dict[str, Any]]) -> int:
        """
        Upsert users (insert or update on address conflict).
        Handles duplicate key gracefully via INSERT ... ON CONFLICT DO UPDATE.
        """
        if self.session_factory is None or not users:
            return 0
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        now = _naive_utc(datetime.now(timezone.utc))
        rows = []
        for u in users:
            addr = u.get("address")
            if not addr or not isinstance(addr, str) or not addr.startswith("0x"):
                continue
            rows.append({
                "address": addr,
                "total_profit": float(u.get("total_profit", 0) or 0),
                "total_volume": float(u.get("total_volume", 0) or 0),
                "win_rate": float(u.get("win_rate", 0) or 0),
                "total_trades": int(u.get("total_trades", 0) or 0),
                "wins": int(u.get("wins", 0) or 0),
                "losses": int(u.get("losses", 0) or 0),
                "roi": float(u.get("roi", 0) or 0),
                "is_elite": bool(u.get("is_elite", True)),
                "last_updated": now,
            })
        if not rows:
            return 0
        async with self.get_session() as session:
            stmt = pg_insert(User).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["address"],
                set_={
                    "total_profit": stmt.excluded.total_profit,
                    "total_volume": stmt.excluded.total_volume,
                    "win_rate": stmt.excluded.win_rate,
                    "total_trades": stmt.excluded.total_trades,
                    "wins": stmt.excluded.wins,
                    "losses": stmt.excluded.losses,
                    "roi": stmt.excluded.roi,
                    "is_elite": stmt.excluded.is_elite,
                    "last_updated": stmt.excluded.last_updated,
                },
            )
            await session.execute(stmt)
            await session.commit()
        return len(rows)
    
    async def get_trades_since(self, since: datetime) -> List[Dict[str, Any]]:
        """
        Get trades since a given time for learning.
        Returns list of dicts with market_id, entry_price, pnl, entry_time (shape expected by learn_from_trades).
        Only includes trades from RESOLVED markets; derives pnl from side + resolution (win=1.0, loss=-1.0).
        Skips open/unresolved trades to avoid incorrect labels.
        """
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            from sqlalchemy import select, func
            since_utc = _naive_utc(since) if (since.tzinfo is not None) else _naive_utc(since.replace(tzinfo=timezone.utc))
            # L5 FIX: Join on both m.id and m.condition_id to catch 37% of trades
            # stored with condition_id as market_id (from Data API ingestion)
            from sqlalchemy import or_, cast, String
            result = await session.execute(
                select(Trade, Market)
                .join(Market, or_(
                    Trade.market_id == cast(Market.id, String),
                    Trade.market_id == Market.condition_id,
                ))
                .where(
                    Trade.market_id.isnot(None),  # Exclude NULL market_id
                    func.coalesce(Trade.entry_time, Trade.timestamp) >= since_utc,
                    Market.resolved == True,
                    Market.resolution.in_(["YES", "NO"]),
                )
            )
            rows = result.all()
            out = []
            for t, m in rows:
                if not t.market_id:
                    continue
                # Infer bet side: YES if token matches yes_token_id, else NO
                side_yes = (
                    (t.token_id and m.yes_token_id and str(t.token_id) == str(m.yes_token_id))
                    or (t.side and str(t.side).upper() == "YES")
                )
                side_no = (
                    (t.token_id and m.no_token_id and str(t.token_id) == str(m.no_token_id))
                    or (t.side and str(t.side).upper() == "NO")
                )
                if not side_yes and not side_no:
                    continue  # Cannot determine position side, skip
                res = (m.resolution or "").upper()
                if res not in ("YES", "NO"):
                    continue
                # Use stored pnl if present; else derive from resolution
                if t.pnl is not None:
                    pnl = float(t.pnl)
                else:
                    win = (side_yes and res == "YES") or (side_no and res == "NO")
                    pnl = 1.0 if win else -1.0
                out.append({
                    "market_id": t.market_id,
                    "entry_price": float(t.price) if t.price is not None else 0.5,
                    "pnl": pnl,
                    "entry_time": t.entry_time or t.timestamp,
                })
            return out

    async def get_bot_metrics(self, bot_name: str) -> Dict[str, Any]:
        """
        Get trades_executed, trades_won and total_pnl from closed positions for a bot.
        Phase 2: per-bot metrics for dashboard (resolution backfill already wired).
        """
        if self.session_factory is None:
            return {"trades_executed": 0, "trades_won": 0, "total_pnl": 0.0}
        try:
            from sqlalchemy import select, or_, func
            async with self.get_session() as session:
                result = await session.execute(
                    select(
                        func.count().label("n"),
                        func.sum(Position.unrealized_pnl).label("total"),
                    )
                    .select_from(Position)
                    .where(
                        or_(
                            Position.bot_id == bot_name,
                            Position.source_bot == bot_name,
                        ),
                        Position.status == "closed",
                        Position.unrealized_pnl.isnot(None),
                        or_(Position.is_paper.is_(None), Position.is_paper == False),  # noqa: E712 - exclude paper positions
                    )
                )
                row = result.one_or_none()
                if not row:
                    return {"trades_executed": 0, "trades_won": 0, "total_pnl": 0.0}
                trades_executed = int(row.n or 0)
                total_pnl = float(row.total or 0.0)
                win_result = await session.execute(
                    select(func.count())
                    .select_from(Position)
                    .where(
                        or_(
                            Position.bot_id == bot_name,
                            Position.source_bot == bot_name,
                        ),
                        Position.status == "closed",
                        Position.unrealized_pnl > 0,
                        or_(Position.is_paper.is_(None), Position.is_paper == False),  # noqa: E712
                    )
                )
                trades_won = (win_result.scalar() or 0) or 0
                return {"trades_executed": trades_executed, "trades_won": trades_won, "total_pnl": total_pnl}
        except Exception as e:
            logger.debug("get_bot_metrics failed for %s: %s", bot_name, e)
            return {"trades_executed": 0, "trades_won": 0, "total_pnl": 0.0}

    async def get_all_bots_metrics(self) -> List[Dict[str, Any]]:
        """Phase 2: per-bot metrics for all bots that have closed positions."""
        if self.session_factory is None:
            return []
        try:
            from sqlalchemy import select, or_, func
            async with self.get_session() as session:
                bot_ids = await session.execute(
                    select(Position.source_bot).where(Position.source_bot.isnot(None)).distinct()
                )
                bots = [r[0] for r in bot_ids.fetchall() if r[0]]
                bot_ids2 = await session.execute(
                    select(Position.bot_id).where(Position.bot_id.isnot(None)).distinct()
                )
                for r in bot_ids2.fetchall():
                    if r[0] and r[0] not in bots:
                        bots.append(r[0])
                out = []
                for bot_name in bots:
                    m = await self.get_bot_metrics(bot_name)
                    m["bot_id"] = bot_name
                    out.append(m)
                return out
        except Exception as e:
            logger.debug("get_all_bots_metrics failed: %s", e)
            return []

    async def get_clv_diagnostic(self) -> Dict[str, Any]:
        """
        Closing Line Value diagnostic: avg(realized PnL per share) over resolved/closed positions.
        Positive avg_clv = consistently buying below where the market settled (edge).
        Negative = systematically buying too late or too high. Uses closed positions only.
        """
        if self.session_factory is None:
            return {"global": {"avg_clv": None, "n_positions": 0}, "per_bot": []}
        try:
            from sqlalchemy import select, or_, func, text
            async with self.get_session() as session:
                # Global: SUM(unrealized_pnl)/NULLIF(SUM(size),0) for status='closed', size>0
                r = await session.execute(
                    text("""
                        SELECT COALESCE(SUM(unrealized_pnl), 0) AS total_pnl,
                               COALESCE(SUM(CASE WHEN size > 0 THEN size ELSE 0 END), 0) AS total_size,
                               COUNT(*)::int AS n
                        FROM positions
                        WHERE status = 'closed' AND size > 0 AND unrealized_pnl IS NOT NULL
                          AND (is_paper IS NULL OR is_paper = FALSE)
                    """)
                )
                row = r.one_or_none()
                if not row or (row[2] or 0) == 0:
                    global_avg_clv = None
                    global_n = 0
                else:
                    total_pnl = float(row[0] or 0)
                    total_size = float(row[1] or 0)
                    global_n = int(row[2] or 0)
                    global_avg_clv = (total_pnl / total_size) if total_size else None
                # Per-bot: same metric keyed by bot_id/source_bot
                bot_list = await session.execute(
                    select(Position.bot_id).where(
                        Position.status == "closed",
                        Position.bot_id.isnot(None),
                    ).distinct()
                )
                bots = [r[0] for r in bot_list.fetchall() if r[0]]
                per_bot = []
                for bot_id in bots:
                    r2 = await session.execute(
                        text("""
                            SELECT COALESCE(SUM(unrealized_pnl), 0) AS total_pnl,
                                   COALESCE(SUM(CASE WHEN size > 0 THEN size ELSE 0 END), 0) AS total_size,
                                   COUNT(*)::int AS n
                            FROM positions
                            WHERE status = 'closed' AND size > 0 AND unrealized_pnl IS NOT NULL
                              AND (bot_id = :bid OR source_bot = :bid)
                              AND (is_paper IS NULL OR is_paper = FALSE)
                        """),
                        {"bid": bot_id},
                    )
                    row2 = r2.one_or_none()
                    if row2 and (row2[2] or 0) > 0 and (float(row2[1] or 0)) > 0:
                        per_bot.append({
                            "bot_id": bot_id,
                            "avg_clv": float(row2[0] or 0) / float(row2[1] or 1),
                            "n_positions": int(row2[2] or 0),
                        })
                return {
                    "global": {"avg_clv": global_avg_clv, "n_positions": global_n},
                    "per_bot": per_bot,
                }
        except Exception as e:
            logger.debug("get_clv_diagnostic failed: %s", e)
            return {"global": {"avg_clv": None, "n_positions": 0}, "per_bot": []}

    # ── Fill Analysis CRUD (P2A-10) ──────────────────────────────

    async def load_recent_fills(self, limit: int = 500, bot_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Load recent fill_analysis rows for restoring AdverseSelectionTracker state on restart.
        Returns list of dicts matching the in-memory fill format.
        """
        if self.session_factory is None:
            return []
        try:
            from sqlalchemy import text
            params: Dict[str, Any] = {"lim": limit}
            bot_filter = ""
            if bot_name:
                bot_filter = "AND source_bot = :bot"
                params["bot"] = bot_name
            async with self.get_session() as session:
                r = await session.execute(text(f"""
                    SELECT market_id, source_bot, fill_price, fill_side, fill_time,
                           price_30s, adverse_move_30s
                    FROM fill_analysis
                    WHERE fill_time IS NOT NULL {bot_filter}
                    ORDER BY fill_time DESC
                    LIMIT :lim
                """), params)
                rows = r.fetchall()
                return [
                    {
                        "market_id": row[0],
                        "source_bot": row[1] or "",
                        "fill_price": float(row[2]),
                        "side": row[3],
                        "fill_time": row[4],
                        "post_fill_price": float(row[5]) if row[5] is not None else None,
                        "adverse_move_30s": float(row[6]) if row[6] is not None else None,
                        "_persisted": True,
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.debug("load_recent_fills failed: %s", e)
            return []

    async def get_fill_analysis_summary(self, bot_name: Optional[str] = None, lookback_days: int = 7) -> Dict[str, Any]:
        """
        Aggregate fill analysis stats from DB: avg adverse move, % adverse, total fills.
        Used by dashboard and risk monitoring.
        """
        if self.session_factory is None:
            return {"n_fills": 0, "avg_adverse_move": None, "pct_adverse": None}
        try:
            from sqlalchemy import text
            params: Dict[str, Any] = {"days": lookback_days}
            bot_filter = ""
            if bot_name:
                bot_filter = "AND source_bot = :bot"
                params["bot"] = bot_name
            async with self.get_session() as session:
                r = await session.execute(text(f"""
                    SELECT COUNT(*)::int AS n_fills,
                           AVG(adverse_move_30s) AS avg_adverse,
                           SUM(CASE WHEN adverse_move_30s > 0 THEN 1 ELSE 0 END)::float
                               / NULLIF(COUNT(*), 0) AS pct_adverse
                    FROM fill_analysis
                    WHERE fill_time >= NOW() - make_interval(days => :days)
                          AND adverse_move_30s IS NOT NULL
                          {bot_filter}
                """), params)
                row = r.one_or_none()
                if not row or (row[0] or 0) == 0:
                    return {"n_fills": 0, "avg_adverse_move": None, "pct_adverse": None}
                return {
                    "n_fills": int(row[0] or 0),
                    "avg_adverse_move": float(row[1]) if row[1] is not None else None,
                    "pct_adverse": float(row[2]) if row[2] is not None else None,
                }
        except Exception as e:
            logger.debug("get_fill_analysis_summary failed: %s", e)
            return {"n_fills": 0, "avg_adverse_move": None, "pct_adverse": None}

    async def get_adverse_move_stats(self, market_id: str, lookback_days: int = 7) -> Optional[Dict[str, float]]:
        """
        L4: Get adverse move statistics for a specific market from fill_analysis.

        Returns avg adverse_move_300s (5-min window) for the market over the lookback period.
        Markets with high adverse selection should get smaller positions.

        Returns:
            Dict with 'avg_adverse_300s', 'n_fills', or None if insufficient data.
        """
        if self.session_factory is None:
            return None
        try:
            from sqlalchemy import text
            async with self.get_session() as session:
                r = await session.execute(text("""
                    SELECT
                        AVG(COALESCE(adverse_move_300s, adverse_move_30s)),
                        COUNT(*)
                    FROM fill_analysis
                    WHERE market_id = :mid
                      AND fill_time >= NOW() - make_interval(days => :days)
                      AND (adverse_move_300s IS NOT NULL OR adverse_move_30s IS NOT NULL)
                """), {"mid": market_id, "days": lookback_days})
                row = r.one_or_none()
                if not row or (row[1] or 0) == 0:
                    return None
                return {
                    "avg_adverse_300s": float(row[0]) if row[0] is not None else 0.0,
                    "n_fills": int(row[1] or 0),
                }
        except Exception as e:
            logger.debug("get_adverse_move_stats(%s) failed: %s", market_id, e)
            return None

    async def get_recent_performance_from_prediction_log(self, n: int = 20) -> Optional[Dict[str, Any]]:
        """Phase 2: last N resolved predictions for recent performance factor. Returns accuracy and count."""
        if self.session_factory is None or n <= 0:
            return None
        try:
            async with self.get_session() as session:
                r = await session.execute(text("""
                    SELECT COUNT(*)::int as cnt,
                           SUM(CASE WHEN sub.was_correct = true THEN 1 ELSE 0 END)::int as correct
                    FROM (
                        SELECT id, was_correct FROM prediction_log
                        WHERE resolution IN ('YES', 'NO') AND was_correct IS NOT NULL
                        ORDER BY resolved_at DESC NULLS LAST, id DESC
                        LIMIT :n
                    ) sub
                """), {"n": n})
                row = r.one_or_none()
                if not row or (row[0] or 0) == 0:
                    return {"count": 0, "correct": 0, "accuracy": 0.0}
                cnt, correct = int(row[0] or 0), int(row[1] or 0)
                return {"count": cnt, "correct": correct, "accuracy": correct / cnt}
        except Exception as e:
            logger.debug("get_recent_performance_from_prediction_log failed: %s", e)
            return None

    async def get_recent_brier_from_prediction_log(self, n: int = 50) -> Optional[Dict[str, Any]]:
        """Recent Brier score and accuracy from last n resolved predictions. Brier = mean((predicted_prob - outcome)^2)."""
        if self.session_factory is None or n <= 0:
            return None
        try:
            async with self.get_session() as session:
                r = await session.execute(text("""
                    SELECT COUNT(*)::int AS cnt,
                           AVG((pl.predicted_prob - CASE WHEN pl.resolution = 'YES' THEN 1.0 ELSE 0.0 END)^2)::float AS brier,
                           SUM(CASE WHEN pl.was_correct = true THEN 1 ELSE 0 END)::int AS correct
                    FROM (
                        SELECT id, predicted_prob, resolution, was_correct FROM prediction_log
                        WHERE resolution IN ('YES', 'NO') AND was_correct IS NOT NULL
                        ORDER BY resolved_at DESC NULLS LAST, id DESC
                        LIMIT :n
                    ) pl
                """), {"n": n})
                row = r.one_or_none()
                if not row or (row[0] or 0) == 0:
                    return {"count": 0, "brier": 0.25, "accuracy": 0.0}
                cnt, brier, correct = int(row[0] or 0), float(row[1] or 0.25), int(row[2] or 0)
                return {"count": cnt, "brier": brier, "accuracy": correct / cnt if cnt else 0.0}
        except Exception as e:
            logger.debug("get_recent_brier_from_prediction_log failed: %s", e)
            return None

    async def get_recent_resolved_predictions(self, since: datetime) -> List[Dict[str, Any]]:
        """Return prediction_log rows resolved after since (for feeding IncrementalLearner)."""
        if self.session_factory is None:
            return []
        try:
            since_naive = _naive_utc(since)
            async with self.get_session() as session:
                r = await session.execute(text("""
                    SELECT market_id, predicted_prob, resolution, resolved_at
                    FROM prediction_log
                    WHERE resolution IN ('YES', 'NO') AND was_correct IS NOT NULL AND resolved_at > :since
                    ORDER BY resolved_at ASC
                """), {"since": since_naive})
                rows = r.fetchall()
                return [{"market_id": str(row[0]), "predicted_prob": float(row[1]), "resolution": str(row[2]), "resolved_at": row[3]} for row in rows if row[0]]
        except Exception as e:
            logger.debug("get_recent_resolved_predictions failed: %s", e)
            return []

    async def get_model_live_performance(self, lookback_days: int = 30) -> Optional[Dict[str, Any]]:
        """Phase 2 (GAP 3): model live performance from prediction_log + resolved markets."""
        if self.session_factory is None or lookback_days <= 0:
            return None
        try:
            async with self.get_session() as session:
                r = await session.execute(text("""
                    SELECT COUNT(*) as cnt,
                           SUM(CASE WHEN was_correct = true THEN 1 ELSE 0 END) as correct,
                           AVG(realized_edge) as avg_edge
                    FROM prediction_log pl
                    WHERE pl.resolution IN ('YES', 'NO') AND pl.was_correct IS NOT NULL
                    AND pl.resolved_at >= :since
                """), {"since": _naive_utc(datetime.now(timezone.utc) - timedelta(days=lookback_days))})
                row = r.one_or_none()
                if not row or (row[0] or 0) == 0:
                    return {"count": 0, "accuracy": 0.0, "avg_edge": 0.0}
                cnt, correct, avg_edge = int(row[0] or 0), int(row[1] or 0), float(row[2] or 0.0)
                return {"count": cnt, "accuracy": correct / cnt, "avg_edge": avg_edge}
        except Exception as e:
            logger.debug("get_model_live_performance failed: %s", e)
            return None

    async def get_latest_trade_timestamp(self) -> Optional[datetime]:
        """Get the timestamp of the most recent trade (for health checks)."""
        if self.session_factory is None:
            return None
        async with self.get_session() as session:
            from sqlalchemy import select, func
            result = await session.execute(
                select(func.max(func.coalesce(Trade.entry_time, Trade.timestamp))).select_from(Trade)
            )
            row = result.scalar_one_or_none()
            return _naive_utc(row) if row is not None else None

    async def get_latest_price_timestamp(self) -> Optional[datetime]:
        """Get the timestamp of the most recent market price (for freshness fallback when no trades)."""
        if self.session_factory is None:
            return None
        async with self.get_session() as session:
            from sqlalchemy import select, func
            from base_engine.data.database import MarketPrice
            result = await session.execute(select(func.max(MarketPrice.timestamp)).select_from(MarketPrice))
            row = result.scalar_one_or_none()
            return _naive_utc(row) if row is not None else None

    async def get_latest_sync_completed_at(self) -> Optional[datetime]:
        """Get completed_at of most recent successful sync (for freshness fallback)."""
        if self.session_factory is None:
            return None
        async with self.get_session() as session:
            from sqlalchemy import select, desc
            from base_engine.data.database import SyncLog
            result = await session.execute(
                select(SyncLog.completed_at)
                .where(SyncLog.status == "success")
                .order_by(desc(SyncLog.completed_at))
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return _naive_utc(row) if row is not None else None

    async def get_recent_trades_for_market(
        self, market_id: str, hours: int = 24, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Get recent trades for a market (for game-theory detectors: persuasion, cascade).
        Returns list of dicts with id, market_id, user_address, side, size, price, timestamp.
        """
        if self.session_factory is None:
            return []
        since = _naive_utc(datetime.now(timezone.utc)) - timedelta(hours=hours)
        async with self.get_session() as session:
            from sqlalchemy import select, desc
            result = await session.execute(
                select(Trade)
                .where(Trade.market_id == market_id, Trade.timestamp >= since)
                .order_by(desc(Trade.timestamp))
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "market_id": r.market_id,
                    "user_address": r.user_address or "",
                    "side": (r.side or "YES").upper() if r.side else "YES",
                    "size": float(r.size) if r.size is not None else 0.0,
                    "price": float(r.price) if r.price is not None else 0.5,
                    "timestamp": r.timestamp,
                }
                for r in rows
            ]

    async def get_price_at(
        self, market_id: str, at_time: datetime, token_id: Optional[str] = None
    ) -> Optional[float]:
        """
        Get the closest price at or before at_time for a market (for adverse selection / reversion).
        Returns single price (YES side preferred if side present) or None.
        """
        if self.session_factory is None:
            return None
        at_naive = _naive_utc(at_time) if getattr(at_time, "tzinfo", None) else at_time
        async with self.get_session() as session:
            from sqlalchemy import select, desc
            stmt = (
                select(MarketPrice)
                .where(MarketPrice.market_id == market_id, MarketPrice.timestamp <= at_naive)
                .order_by(desc(MarketPrice.timestamp))
                .limit(1)
            )
            if token_id:
                stmt = stmt.where(MarketPrice.token_id == token_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row and row.price is not None:
                return float(row.price)
            return None

    async def insert_sync_log(
        self,
        sync_type: str,
        component: str,
        status: str,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        records_processed: Optional[int] = None,
        records_inserted: Optional[int] = None,
        records_failed: Optional[int] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an ingestion run for monitoring. Does not raise on failure."""
        if self.session_factory is None:
            return
        started_at = started_at or datetime.now(timezone.utc)
        started_at = _naive_utc(started_at) if getattr(started_at, "tzinfo", None) else started_at
        completed_at = _naive_utc(completed_at) if completed_at and getattr(completed_at, "tzinfo", None) else completed_at
        try:
            async with self.get_session() as session:
                log = SyncLog(
                    sync_type=sync_type,
                    component=component,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=status,
                    records_processed=records_processed,
                    records_inserted=records_inserted,
                    records_failed=records_failed,
                    error_message=error_message,
                    extra=metadata,
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.warning("Failed to write sync_log: %s", e)

    async def insert_prediction_log(
        self,
        market_id: str,
        predicted_prob: float,
        market_price: float,
        model_name: str = "ensemble",
        token_id: Optional[str] = None,
        fallback_level: Optional[int] = None,
        confidence: Optional[float] = None,
        ensemble_pred: Optional[float] = None,
        learning_conf: Optional[float] = None,
        feature_snapshot: Optional[Dict] = None,
        correlation_id: Optional[str] = None,
        bot_name: Optional[str] = None,
    ) -> None:
        """Log a prediction for drift detection and live performance tracking. No-op if no db or table missing."""
        if self.session_factory is None:
            return
        edge = predicted_prob - market_price
        ts = _naive_utc(datetime.now(timezone.utc))
        try:
            async with self.get_session() as session:
                from base_engine.data.database import PredictionLog
                log = PredictionLog(
                    market_id=market_id,
                    token_id=token_id,
                    model_name=model_name,
                    predicted_prob=predicted_prob,
                    market_price=market_price,
                    edge=edge,
                    prediction_time=ts,
                    fallback_level=fallback_level,
                    confidence=confidence,
                    ensemble_pred=ensemble_pred,
                    learning_conf=learning_conf,
                    feature_snapshot=feature_snapshot,
                    correlation_id=correlation_id,
                    bot_name=bot_name,
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.debug("Failed to write prediction_log (table may not exist): %s", e)

    async def mark_prediction_traded(
        self,
        market_id: str,
        token_id: str,
        trade_side: str,
        trade_size: float,
        trade_price: float,
    ) -> None:
        """Mark the most recent prediction_log entry for this market/token as traded."""
        if self.session_factory is None:
            return
        try:
            async with self.get_session() as session:
                await session.execute(
                    text(
                        "UPDATE prediction_log SET trade_executed = true, "
                        "trade_side = :side, trade_size = :size, trade_price = :price "
                        "WHERE id = ("
                        "  SELECT id FROM prediction_log "
                        "  WHERE market_id = :mid AND token_id = :tid "
                        "  ORDER BY created_at DESC LIMIT 1"
                        ")"
                    ),
                    {"mid": market_id, "tid": token_id, "side": trade_side, "size": trade_size, "price": trade_price},
                )
                await session.commit()
        except Exception as e:
            logger.debug("mark_prediction_traded failed (non-fatal): %s", e)

    async def get_recent_resolved_for_blend(self, n: int = 100) -> List[Dict[str, Any]]:
        """Return recent resolved prediction_log rows with ensemble_pred and learning_conf for blend grid search."""
        if self.session_factory is None or n <= 0:
            return []
        try:
            async with self.get_session() as session:
                r = await session.execute(text("""
                    SELECT predicted_prob, resolution, ensemble_pred, learning_conf
                    FROM prediction_log
                    WHERE resolution IN ('YES', 'NO') AND was_correct IS NOT NULL
                    ORDER BY resolved_at DESC NULLS LAST, id DESC
                    LIMIT :n
                """), {"n": n})
                rows = r.fetchall()
            outcome = lambda res: 1.0 if res == "YES" else 0.0
            return [
                {
                    "predicted_prob": float(row[0]),
                    "resolution": str(row[1]),
                    "ensemble_pred": float(row[2]) if row[2] is not None else None,
                    "learning_conf": float(row[3]) if row[3] is not None else None,
                    "outcome": outcome(row[1]),
                }
                for row in rows
            ]
        except Exception as e:
            logger.debug("get_recent_resolved_for_blend failed: %s", e)
            return []

    async def backfill_prediction_log_resolution(self) -> int:
        """
        Update prediction_log rows with resolution, resolved_at, was_correct from resolved markets.
        Run after resolution backfill. Returns count of rows updated.

        Temporal ordering assertion: only labels predictions where the market resolved AFTER the
        prediction was made (resolved_at > prediction_time).  This prevents retroactive labeling of
        predictions with outcomes that were already known at prediction time — the cardinal sin of
        ML pipeline data leakage.
        """
        if self.session_factory is None:
            return 0
        try:
            async with self.get_session() as session:
                # Temporal integrity guard: detect any predictions that would be labeled
                # with a resolution timestamp BEFORE their prediction_time.
                # These indicate clock skew, backfill ordering bugs, or data corruption.
                r_check = await session.execute(text("""
                    SELECT COUNT(*) FROM prediction_log pl
                    JOIN markets m ON pl.market_id = m.id
                    WHERE m.resolution IN ('YES', 'NO')
                    AND m.resolved_at IS NOT NULL
                    AND pl.prediction_time IS NOT NULL
                    AND m.resolved_at < pl.prediction_time
                """))
                temporal_violations = r_check.scalar_one_or_none() or 0
                if temporal_violations > 0:
                    # S142: rate-limit to once per 5 min — was firing every ~0.5s
                    import time as _t
                    _now = _t.monotonic()
                    if not hasattr(self, "_last_temporal_warn") or (_now - self._last_temporal_warn) > 300:
                        self._last_temporal_warn = _now
                        logger.warning(
                            "Temporal ordering violation: %d prediction_log rows have "
                            "resolved_at < prediction_time — excluded from labeling. "
                            "Run: python scripts/cleanup_temporal_violations.py --dry-run",
                            temporal_violations,
                        )

                r = await session.execute(text("""
                    UPDATE prediction_log pl
                    SET
                        resolution = m.resolution,
                        resolved_at = m.resolved_at,
                        was_correct = CASE
                            WHEN ABS(pl.predicted_prob - 0.5) < 0.01 THEN NULL
                            ELSE ((pl.predicted_prob >= 0.5) = (m.resolution = 'YES'))
                        END,
                        realized_edge = CASE
                            WHEN m.resolution = 'YES' THEN 1.0 - pl.market_price
                            WHEN m.resolution = 'NO' THEN pl.market_price
                            ELSE NULL
                        END
                    FROM markets m
                    WHERE (pl.market_id = CAST(m.id AS TEXT) OR pl.market_id = m.condition_id)
                    AND m.resolution IN ('YES', 'NO')
                    AND (pl.resolution IS NULL OR pl.resolution NOT IN ('YES', 'NO'))
                    -- Temporal ordering: only label if market resolved AFTER prediction was made.
                    -- Prevents poisoning the model with outcomes known at prediction time.
                    AND (
                        m.resolved_at IS NULL
                        OR pl.prediction_time IS NULL
                        OR m.resolved_at >= pl.prediction_time
                    )
                """))
                count = getattr(r, "rowcount", 0) or 0
                await session.commit()
                return count
        except Exception as e:
            logger.debug("prediction_log resolution backfill failed (table may not exist): %s", e)
            return 0

    async def insert_paper_trade(
        self,
        order_id: str,
        market_id: str,
        token_id: Optional[str],
        bot_name: str,
        side: str,
        size: float,
        price: float,
        confidence: Optional[float] = None,
        correlation_id: Optional[str] = None,
        realized_pnl: Optional[float] = None,
        latency_ms: Optional[float] = None,
        status: str = "filled",
        submitted_at: Optional[datetime] = None,
        filled_at: Optional[datetime] = None,
    ) -> None:
        """Persist one paper trade for SIMULATION_MODE. No-op if no db.

        Uses UPSERT (ON CONFLICT DO UPDATE) on the UNIQUE(bot_name, market_id, side)
        constraint so re-entries after a position close overwrite the old row
        instead of failing with a duplicate key error.
        """
        if self.session_factory is None:
            return
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                await session.execute(
                    _sa_text(
                        "INSERT INTO paper_trades "
                        "(order_id, market_id, token_id, bot_name, side, size, price, "
                        " confidence, correlation_id, realized_pnl, latency_ms, status, "
                        " submitted_at, filled_at, created_at) "
                        "VALUES (:order_id, :market_id, :token_id, :bot_name, :side, :size, :price, "
                        " :confidence, :correlation_id, :realized_pnl, :latency_ms, :status, "
                        " :submitted_at, :filled_at, NOW()) "
                        "ON CONFLICT (bot_name, market_id, side) DO UPDATE SET "
                        " order_id = EXCLUDED.order_id, "
                        " token_id = COALESCE(paper_trades.token_id, EXCLUDED.token_id), "
                        " confidence = EXCLUDED.confidence, "
                        " correlation_id = EXCLUDED.correlation_id, "
                        " realized_pnl = COALESCE(paper_trades.realized_pnl, EXCLUDED.realized_pnl), "
                        " latency_ms = EXCLUDED.latency_ms, "
                        " status = EXCLUDED.status, "
                        " submitted_at = EXCLUDED.submitted_at, "
                        " filled_at = EXCLUDED.filled_at"
                    ),
                    {
                        "order_id": order_id,
                        "market_id": market_id,
                        "token_id": token_id,
                        "bot_name": bot_name,
                        "side": side,
                        "size": size,
                        "price": price,
                        "confidence": confidence,
                        "correlation_id": correlation_id,
                        "realized_pnl": realized_pnl,
                        "latency_ms": latency_ms,
                        "status": status,
                        "submitted_at": submitted_at,
                        "filled_at": filled_at,
                    },
                )
                # S109: Upsert traded_markets with condition_id enrichment from markets table.
                # Previously inserted without condition_id → 275/276 resolved markets had NULL
                # condition_id → resolution backfill couldn't emit RESOLUTION events.
                try:
                    await session.execute(
                        _sa_text(
                            "INSERT INTO traded_markets (market_id, condition_id, bot_names, first_trade_at) "
                            "SELECT :market_id, m.condition_id, :bot_name, NOW() "
                            "FROM (SELECT 1) dummy "
                            "LEFT JOIN markets m ON m.condition_id = :market_id OR CAST(m.id AS TEXT) = :market_id "
                            "ON CONFLICT (market_id) DO UPDATE SET "
                            "  bot_names = CASE "
                            "    WHEN traded_markets.bot_names NOT LIKE '%%' || :bot_name || '%%' "
                            "    THEN traded_markets.bot_names || ',' || :bot_name "
                            "    ELSE traded_markets.bot_names END, "
                            "  condition_id = COALESCE(traded_markets.condition_id, EXCLUDED.condition_id)"
                        ),
                        {"market_id": market_id, "bot_name": bot_name},
                    )
                except Exception as _tm_err:
                    logger.debug("db_traded_markets_upsert_skipped", error=str(_tm_err))
                await session.commit()
            # trade_event emission moved to paper_trading.py execution layer
            # to avoid duplicate writes (both paths generated same idempotency_key)
        except Exception as e:
            logger.warning("Failed to write paper_trades: %s", e)

    async def get_paper_trade_by_correlation_id(self, correlation_id: str, market_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """H1: Idempotency check — returns existing paper_trade dict if correlation_id already used.

        Called before executing a trade to prevent double-fills on timeout + retry.
        Returns None if no matching record found (safe to proceed with trade).

        When market_id is provided, matches on (correlation_id, market_id) composite key.
        This prevents false dedup when multiple orders share a per-scan correlation_id.
        """
        if self.session_factory is None or not correlation_id:
            return None
        try:
            async with self.get_session() as session:
                from sqlalchemy import text as _sa_text
                if market_id:
                    result = await session.execute(
                        _sa_text(
                            "SELECT order_id, price, size, side, status "
                            "FROM paper_trades WHERE correlation_id = :cid AND market_id = :mid LIMIT 1"
                        ),
                        {"cid": str(correlation_id), "mid": str(market_id)},
                    )
                else:
                    result = await session.execute(
                        _sa_text(
                            "SELECT order_id, price, size, side, status "
                            "FROM paper_trades WHERE correlation_id = :cid LIMIT 1"
                        ),
                        {"cid": str(correlation_id)},
                    )
                row = result.fetchone()
                if row:
                    return {
                        "order_id": str(row[0]),
                        "price": float(row[1] or 0),
                        "size": float(row[2] or 0),
                        "side": str(row[3] or ""),
                        "status": str(row[4] or "filled"),
                    }
        except Exception as e:
            logger.debug("get_paper_trade_by_correlation_id failed (non-fatal): %s", e)
        return None

    async def backfill_prediction_log_from_closed_trades(self) -> int:
        """
        Pseudo-label fallback: set prediction_log.was_correct from closed paper trades when
        real market resolution is unavailable (delayed label problem).

        Logic: for each prediction_log row with was_correct=NULL, find SELL trades for the same
        market and set was_correct = (avg_realized_pnl > 0). This is an imperfect but actionable
        proxy — a profitable exit means the directional prediction was approximately correct.

        Only sets was_correct on rows that have no real resolution yet. Idempotent.
        Returns count of rows updated.

        DISABLED by default (PSEUDO_LABEL_ENABLED=false): when paper trades are consistently
        losing, avg_pnl < 0 for almost all markets → was_correct=FALSE for everything → corrupts
        model training. Real market-resolution labels from backfill_prediction_log_resolution()
        are more reliable. Enable only if paper trade P&L is a trustworthy accuracy proxy.
        """
        if not getattr(settings, "PSEUDO_LABEL_ENABLED", False):
            return 0
        if self.session_factory is None:
            return 0
        try:
            async with self.get_session() as session:
                r = await session.execute(text("""
                    UPDATE prediction_log pl
                    SET was_correct = (agg.avg_pnl > 0),
                        realized_edge = agg.avg_pnl / NULLIF(agg.avg_price, 0)
                    FROM (
                        SELECT
                            pt.market_id,
                            AVG(pt.realized_pnl) AS avg_pnl,
                            AVG(pt.price)        AS avg_price
                        FROM paper_trades pt
                        WHERE pt.side = 'SELL'
                          AND pt.realized_pnl IS NOT NULL
                        GROUP BY pt.market_id
                    ) agg
                    WHERE pl.market_id = agg.market_id
                      AND pl.was_correct IS NULL
                      AND (pl.resolution IS NULL OR pl.resolution NOT IN ('YES', 'NO'))
                """))
                count = getattr(r, "rowcount", 0) or 0
                await session.commit()
                return count
        except Exception as e:
            logger.debug("prediction_log closed-trade pseudo-label backfill failed: %s", e)
            return 0

    async def backfill_paper_trades_resolution(self) -> int:
        """
        Update paper_trades with resolution, resolved_at, and realized_pnl from resolved markets.
        Includes taker fee deduction (TAKER_FEE_BPS) for realistic P&L.
        Run after resolution backfill. Returns count of rows updated.
        """
        if self.session_factory is None:
            return 0
        _fee_rate = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0  # 1.5% default
        try:
            async with self.get_session() as session:
                r = await session.execute(text("""
                    UPDATE paper_trades pt
                    SET
                        resolution = m.resolution,
                        resolved_at = m.resolved_at,
                        status = 'resolved',
                        realized_pnl = (
                            CASE
                                WHEN m.resolution = 'YES' AND LOWER(pt.side) = 'yes' THEN pt.size * (1.0 - pt.price) - (pt.size * 1.0 * :fee_rate)
                                WHEN m.resolution = 'YES' AND LOWER(pt.side) = 'no' THEN pt.size * (0.0 - pt.price) - (pt.size * 0.0 * :fee_rate)
                                WHEN m.resolution = 'NO' AND LOWER(pt.side) = 'yes' THEN pt.size * (0.0 - pt.price) - (pt.size * 0.0 * :fee_rate)
                                WHEN m.resolution = 'NO' AND LOWER(pt.side) = 'no' THEN pt.size * (1.0 - pt.price) - (pt.size * 1.0 * :fee_rate)
                                ELSE NULL
                            END
                        )
                    FROM markets m
                    WHERE (pt.market_id = CAST(m.id AS TEXT) OR pt.market_id = m.condition_id)
                    AND m.resolution IN ('YES', 'NO')
                    AND (pt.resolution IS NULL OR pt.resolution NOT IN ('YES', 'NO'))
                    AND LOWER(pt.side) != 'sell'
                """), {"fee_rate": _fee_rate})
                count = getattr(r, "rowcount", 0) or 0
                await session.commit()

                # RESOLUTION event emission removed — handled by resolution_backfill.py
                # Phase 4b with NOT EXISTS dedup.  This path had no dedup and created
                # duplicates on every call (event_time=now() bypassed ON CONFLICT).

                return count
        except Exception as e:
            logger.debug("paper_trades resolution backfill failed (table may not exist): %s", e)
            return 0

    async def get_paper_trade_equity_curve(self, days: int = 90) -> List[Dict[str, Any]]:
        """Return daily cumulative P&L from resolved paper trades for equity curve display.
        Each row: {date, daily_pnl, cumulative_pnl, trade_count, win_count}."""
        if self.session_factory is None:
            return []
        try:
            async with self.get_session() as session:
                result = await session.execute(text("""
                    SELECT
                        DATE(pt.created_at) AS day,
                        SUM(COALESCE(pt.realized_pnl, 0)) AS daily_pnl,
                        COUNT(*) AS trade_count,
                        COUNT(*) FILTER (WHERE pt.realized_pnl > 0) AS win_count
                    FROM paper_trades pt
                    WHERE pt.resolution IS NOT NULL
                      AND pt.resolution IN ('YES', 'NO')
                      AND pt.side IN ('YES', 'NO')
                      AND pt.created_at >= NOW() - make_interval(days => :days)
                    GROUP BY DATE(pt.created_at)
                    ORDER BY day
                """), {"days": int(days)})
                rows = result.fetchall()
                curve = []
                cumulative = 0.0
                for row in rows:
                    cumulative += float(row[1] or 0)
                    curve.append({
                        "date": str(row[0]),
                        "daily_pnl": float(row[1] or 0),
                        "cumulative_pnl": cumulative,
                        "trade_count": int(row[2] or 0),
                        "win_count": int(row[3] or 0),
                    })
                return curve
        except Exception as e:
            logger.debug("paper trade equity curve failed (table may not exist): %s", e)
            return []

    async def get_paper_trade_summary(self) -> Dict[str, Any]:
        """Return aggregate paper trading performance: total P&L, win rate, per-bot stats."""
        if self.session_factory is None:
            return {}
        try:
            async with self.get_session() as session:
                # Overall stats
                overall = await session.execute(text("""
                    SELECT
                        COUNT(*) AS total_trades,
                        COUNT(*) FILTER (WHERE resolution IS NOT NULL) AS resolved_trades,
                        COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
                        COUNT(*) FILTER (WHERE realized_pnl <= 0 AND realized_pnl IS NOT NULL) AS losses,
                        COALESCE(SUM(realized_pnl), 0) AS total_pnl,
                        AVG(realized_pnl) FILTER (WHERE realized_pnl IS NOT NULL) AS avg_pnl,
                        MAX(realized_pnl) AS best_trade,
                        MIN(realized_pnl) AS worst_trade
                    FROM paper_trades
                    WHERE side IN ('YES', 'NO')
                """))
                o = overall.first()
                if not o:
                    return {}

                # Per-bot stats
                per_bot = await session.execute(text("""
                    SELECT
                        bot_name,
                        COUNT(*) AS trades,
                        COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
                        COALESCE(SUM(realized_pnl), 0) AS pnl
                    FROM paper_trades
                    WHERE resolution IS NOT NULL
                      AND side IN ('YES', 'NO')
                    GROUP BY bot_name
                    ORDER BY pnl DESC
                """))
                bot_rows = per_bot.fetchall()

                resolved = int(o[1] or 0)
                wins = int(o[2] or 0)
                return {
                    "total_trades": int(o[0] or 0),
                    "resolved_trades": resolved,
                    "wins": wins,
                    "losses": int(o[3] or 0),
                    "win_rate": wins / max(resolved, 1),
                    "total_pnl": float(o[4] or 0),
                    "avg_pnl": float(o[5] or 0) if o[5] else 0.0,
                    "best_trade": float(o[6] or 0) if o[6] else 0.0,
                    "worst_trade": float(o[7] or 0) if o[7] else 0.0,
                    "per_bot": [
                        {"bot_name": r[0], "trades": int(r[1]), "wins": int(r[2]), "pnl": float(r[3])}
                        for r in bot_rows
                    ],
                }
        except Exception as e:
            logger.debug("paper trade summary failed (table may not exist): %s", e)
            return {}

    async def get_per_bot_strategy_analytics(self, days: int = 30) -> Dict[str, Any]:
        """Per-bot strategy analytics: Sharpe ratio, max drawdown, win rate, avg trade P&L.
        Computed from paper_trades (resolved) grouped by bot_name.
        Returns {bot_name: {sharpe, max_drawdown, win_rate, total_pnl, trade_count, avg_pnl}}."""
        if self.session_factory is None:
            return {}
        try:
            async with self.get_session() as session:
                result = await session.execute(text("""
                    SELECT
                        bot_name,
                        realized_pnl,
                        created_at
                    FROM paper_trades
                    WHERE resolution IS NOT NULL
                      AND resolution IN ('YES', 'NO')
                      AND side IN ('YES', 'NO')
                      AND realized_pnl IS NOT NULL
                      AND created_at >= NOW() - make_interval(days => :days)
                    ORDER BY bot_name, created_at ASC
                """), {"days": int(days)})
                rows = result.fetchall()
                if not rows:
                    return {}

                from collections import defaultdict
                import math
                bot_trades: Dict[str, List[float]] = defaultdict(list)
                for r in rows:
                    bot_trades[r[0]].append(float(r[1] or 0))

                analytics = {}
                for bot_name, pnls in bot_trades.items():
                    n = len(pnls)
                    wins = sum(1 for p in pnls if p > 0)
                    total = sum(pnls)
                    avg = total / n if n else 0.0

                    # Sharpe ratio (annualized, assume ~1 trade/day cadence)
                    sharpe = 0.0
                    if n >= 2:
                        mean_pnl = total / n
                        variance = sum((p - mean_pnl) ** 2 for p in pnls) / n
                        std = math.sqrt(variance) if variance > 0 else 0.0
                        if std > 1e-10:
                            sharpe = round((mean_pnl / std) * math.sqrt(252), 2)

                    # Max drawdown from cumulative equity
                    max_dd = 0.0
                    cumulative = 0.0
                    peak = 0.0
                    for p in pnls:
                        cumulative += p
                        if cumulative > peak:
                            peak = cumulative
                        if peak > 0:
                            dd = (peak - cumulative) / peak * 100.0
                            if dd > max_dd:
                                max_dd = dd

                    analytics[bot_name] = {
                        "trade_count": n,
                        "wins": wins,
                        "losses": n - wins,
                        "win_rate": round(wins / n, 4) if n else 0.0,
                        "total_pnl": round(total, 4),
                        "avg_pnl": round(avg, 4),
                        "sharpe_ratio": sharpe,
                        "max_drawdown_pct": round(max_dd, 2),
                    }
                return analytics
        except Exception as e:
            logger.debug("per-bot strategy analytics failed (table may not exist): %s", e)
            return {}

    async def backfill_positions_resolution(self) -> int:
        """
        CRITICAL FIX: Update Position.unrealized_pnl for closed/open positions where the market
        has resolved, but unrealized_pnl is 0.0 or NULL (set by position_manager on exit, but NOT
        on market resolution). This is THE most impactful metric fix — it corrects CLV, win rate,
        and Total P&L for all resolution-based exits.

        Payout logic:
        - If position side matches resolution (YES+YES or NO+NO): payout = 1.0
        - Otherwise: payout = 0.0
        - P&L = (payout - entry_price) * size - fee
        - Fee = entry_price * size * (TAKER_FEE_BPS / 10000)

        Returns count of positions updated.
        """
        if self.session_factory is None:
            return 0
        _fee_rate = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0  # 1.5% default
        try:
            from sqlalchemy import text
            async with self.get_session() as session:
                # S109: Zero out stale unrealized_pnl on already-closed positions FIRST.
                # Must run BEFORE resolution update — otherwise we zero the fresh
                # resolution P&L we just computed (P1-19 from S128 audit).
                # S155: Skip positions on resolved markets — their P&L from stop-loss
                # or take-profit exits is correct and should not be erased. The
                # resolution update below only re-computes status='open' positions,
                # so without this guard, stop-loss P&L was permanently destroyed.
                r2 = await session.execute(text(
                    "UPDATE positions SET unrealized_pnl = 0 "
                    "WHERE status = 'closed' AND unrealized_pnl != 0 "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM markets m "
                    "  WHERE (m.id = positions.market_id OR m.condition_id = positions.market_id) "
                    "  AND m.resolution IN ('YES', 'NO')"
                    ")"
                ))
                stale_cleaned = getattr(r2, "rowcount", 0) or 0
                r = await session.execute(text("""
                    UPDATE positions p
                    SET unrealized_pnl = (
                        CASE
                            WHEN UPPER(p.side) = m.resolution THEN (1.0 - p.entry_price) * p.size
                            ELSE (0.0 - p.entry_price) * p.size
                        END
                    ) - (p.entry_price * p.size * :fee_rate),
                    status = 'closed'
                    FROM markets m
                    WHERE (p.market_id = m.id OR p.market_id = m.condition_id)
                      AND m.resolution IN ('YES', 'NO')
                      AND p.status = 'open'
                      AND p.size > 0
                      AND p.entry_price IS NOT NULL
                """), {"fee_rate": _fee_rate})
                count = getattr(r, "rowcount", 0) or 0
                await session.commit()
                if count:
                    logger.info("Backfilled unrealized_pnl for %d resolved positions", count)
                if stale_cleaned:
                    logger.info("Zeroed stale unrealized_pnl on %d closed positions", stale_cleaned)
                return count
        except Exception as e:
            logger.debug("positions resolution backfill failed: %s", e)
            return 0

    async def insert_healing_log(
        self,
        issues_detected: int,
        fixes_applied: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write one healing_log row for AutoHealer audit. No-op if no db."""
        if self.session_factory is None:
            return
        ts = _naive_utc(datetime.now(timezone.utc))
        try:
            async with self.get_session() as session:
                log = HealingLog(
                    timestamp=ts,
                    issues_detected=issues_detected,
                    fixes_applied=fixes_applied,
                    details=details,
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.warning("Failed to write healing_log: %s", e)

    async def is_sync_in_progress(
        self,
        component: str = "data_ingestion",
        sync_type: Optional[str] = None,
        stale_hours: float = 2.0,
    ) -> bool:
        """Return True if a sync with status='running' exists and is not stale (started within stale_hours)."""
        if self.session_factory is None:
            return False
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=stale_hours)
        async with self.get_session() as session:
            from sqlalchemy import select
            stmt = (
                select(SyncLog)
                .where(SyncLog.component == component)
                .where(SyncLog.status == "running")
                .where(SyncLog.started_at >= cutoff)
            )
            if sync_type:
                stmt = stmt.where(SyncLog.sync_type == sync_type)
            stmt = stmt.order_by(SyncLog.started_at.desc()).limit(1)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return row is not None

    async def mark_stale_sync_logs_failed(
        self,
        component: str = "data_ingestion",
        sync_type: Optional[str] = None,
        older_than_hours: float = 2.0,
    ) -> int:
        """Mark 'running' sync_log rows older than N hours as failed. Returns count updated."""
        if self.session_factory is None:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        cutoff = _naive_utc(cutoff)
        now = _naive_utc(datetime.now(timezone.utc))
        try:
            async with self.get_session() as session:
                stmt = (
                    update(SyncLog)
                    .where(SyncLog.component == component)
                    .where(SyncLog.status == "running")
                    .where(SyncLog.started_at < cutoff)
                    .values(
                        status="failed",
                        completed_at=now,
                        error_message="Stale run (cleared by mark_stale_sync_logs_failed)",
                    )
                )
                if sync_type:
                    stmt = stmt.where(SyncLog.sync_type == sync_type)
                result = await session.execute(stmt)
                await session.commit()
                count = result.rowcount or 0
                if count > 0:
                    logger.info("mark_stale_sync_logs_failed: marked %s stale sync_log rows as failed", count)
                return count
        except Exception as e:
            logger.warning("mark_stale_sync_logs_failed failed: %s", e)
            return 0

    async def clear_stuck_sync_running(
        self,
        component: str = "data_ingestion",
        sync_type: Optional[str] = None,
    ) -> int:
        """Mark all 'running' sync_log rows for this component/sync_type as failed. Returns count updated."""
        if self.session_factory is None:
            return 0
        now = _naive_utc(datetime.now(timezone.utc))
        try:
            async with self.get_session() as session:
                stmt = (
                    update(SyncLog)
                    .where(SyncLog.component == component)
                    .where(SyncLog.status == "running")
                    .values(
                        status="failed",
                        completed_at=now,
                        error_message="Cleared stuck run (manual or timeout)",
                    )
                )
                if sync_type:
                    stmt = stmt.where(SyncLog.sync_type == sync_type)
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount or 0
        except Exception as e:
            logger.warning("clear_stuck_sync_running failed: %s", e)
            return 0

    async def update_running_sync_log(
        self,
        component: str,
        sync_type: str,
        started_at: datetime,
        status: str,
        completed_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
        records_processed: Optional[int] = None,
        records_inserted: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Update the sync_log row with status='running' and this started_at to the given status. Returns 1 if updated."""
        if self.session_factory is None:
            return 0
        started_at = _naive_utc(started_at) if getattr(started_at, "tzinfo", None) else started_at
        completed_at = _naive_utc(completed_at) if completed_at and getattr(completed_at, "tzinfo", None) else completed_at or _naive_utc(datetime.now(timezone.utc))
        try:
            async with self.get_session() as session:
                stmt = (
                    update(SyncLog)
                    .where(SyncLog.component == component)
                    .where(SyncLog.sync_type == sync_type)
                    .where(SyncLog.status == "running")
                    .where(SyncLog.started_at == started_at)
                    .values(
                        status=status,
                        completed_at=completed_at,
                        error_message=error_message,
                        records_processed=records_processed,
                        records_inserted=records_inserted,
                        extra=metadata,
                    )
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount or 0
        except Exception as e:
            logger.warning("update_running_sync_log failed: %s", e)
            return 0

    async def get_last_sync_run(
        self, component: Optional[str] = None, status: str = "success"
    ) -> Optional[Dict[str, Any]]:
        """Get the most recent sync log entry (for health checks)."""
        if self.session_factory is None:
            return None
        async with self.get_session() as session:
            from sqlalchemy import select
            stmt = select(SyncLog).where(SyncLog.status == status).order_by(SyncLog.started_at.desc()).limit(1)
            if component:
                stmt = stmt.where(SyncLog.component == component)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                "sync_type": row.sync_type,
                "component": row.component,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
                "status": row.status,
                "records_inserted": row.records_inserted,
            }

    async def get_failed_syncs_since(
        self,
        since: datetime,
        component: str = "data_ingestion",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get failed sync_log rows since given time (for AutoHealer)."""
        if self.session_factory is None:
            return []
        since_utc = _naive_utc(since) if getattr(since, "tzinfo", None) else since.replace(tzinfo=timezone.utc)
        since_utc = _naive_utc(since_utc)
        async with self.get_session() as session:
            from sqlalchemy import select
            stmt = (
                select(SyncLog)
                .where(SyncLog.component == component)
                .where(SyncLog.status == "failed")
                .where(SyncLog.started_at >= since_utc)
                .order_by(SyncLog.started_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "sync_type": r.sync_type,
                "component": r.component,
                "started_at": r.started_at,
                "status": r.status,
                "error_message": r.error_message,
            }
            for r in rows
        ]

    async def get_recent_syncs(
        self,
        component: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get most recent sync_log rows for dashboard display."""
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            from sqlalchemy import select
            stmt = select(SyncLog).order_by(SyncLog.started_at.desc()).limit(limit)
            if component:
                stmt = stmt.where(SyncLog.component == component)
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [
            {
                "sync_type": r.sync_type,
                "component": r.component,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "status": r.status,
                "records_inserted": r.records_inserted,
                "records_processed": r.records_processed,
            }
            for r in rows
        ]

    async def get_data_pull_status(self) -> Dict[str, Any]:
        """Return markets count, prices count, and last full pull time for one-line status display."""
        out: Dict[str, Any] = {"markets_count": 0, "prices_count": 0, "last_pull_at": None, "last_pull_status": None}
        if self.session_factory is None:
            return out
        async with self.get_session() as session:
            from sqlalchemy import select, func
            result = await session.execute(select(func.count(Market.id)))
            out["markets_count"] = result.scalar() or 0
            result = await session.execute(select(func.count(MarketPrice.id)))
            out["prices_count"] = result.scalar() or 0
            # Include both "full" and "backfill" - either indicates last successful pull
            stmt = (
                select(SyncLog)
                .where(SyncLog.component == "data_ingestion")
                .where(SyncLog.sync_type.in_(["full", "backfill"]))
                .where(SyncLog.status.in_(["success", "failed"]))
                .order_by(SyncLog.started_at.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row and row.completed_at:
                out["last_pull_at"] = row.completed_at
                out["last_pull_status"] = row.status
        return out

    async def refresh_materialized_view_market_stats(self) -> bool:
        """Refresh materialized view market_stats for fast dashboards (#30)."""
        if self.session_factory is None:
            return False
        try:
            async with self.get_session() as session:
                await session.execute(text("REFRESH MATERIALIZED VIEW market_stats"))
                await session.commit()
            return True
        except Exception as e:
            logger.warning("refresh_materialized_view_market_stats failed (view may not exist yet): %s", e)
            return False

    async def insert_audit_log(
        self,
        table_name: str,
        operation: str,
        record_id: Optional[str] = None,
        old_data: Optional[Dict[str, Any]] = None,
        new_data: Optional[Dict[str, Any]] = None,
        changed_by: Optional[str] = None,
    ) -> None:
        """Append one audit log row for CDC (#29)."""
        if self.session_factory is None:
            return
        try:
            async with self.get_session() as session:
                import json
                stmt = text(
                    "INSERT INTO audit_log (table_name, operation, record_id, old_data, new_data, changed_by) "
                    "VALUES (:table_name, :operation, :record_id, CAST(:old_data AS jsonb), CAST(:new_data AS jsonb), :changed_by)"
                )
                await session.execute(
                    stmt,
                    {
                        "table_name": table_name,
                        "operation": operation,
                        "record_id": record_id,
                        "old_data": json.dumps(old_data) if old_data else None,
                        "new_data": json.dumps(new_data) if new_data else None,
                        "changed_by": changed_by,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.warning("insert_audit_log failed (table may not exist): %s", e)

    async def get_webhook_configs(
        self,
        event_type: Optional[str] = None,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get webhook configs for dispatching (#39)."""
        if self.session_factory is None:
            return []
        try:
            async with self.get_session() as session:
                q = "SELECT id, event_type, url, secret, active FROM webhook_config WHERE 1=1"
                params: Dict[str, Any] = {}
                if event_type:
                    q += " AND event_type = :event_type"
                    params["event_type"] = event_type
                if active_only:
                    q += " AND active = TRUE"
                result = await session.execute(text(q), params)
                rows = result.mappings().all()
            return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.debug("get_webhook_configs failed (table may not exist): %s", e)
            return []

    async def get_prices_since(self, since: datetime, limit: int = 10000) -> List[Dict[str, Any]]:
        """
        Get price records since a given time for learning.
        Returns list of dicts with market_id, token_id, price, timestamp, side.
        """
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            from sqlalchemy import select, desc
            since_utc = _naive_utc(since) if (since.tzinfo is not None) else since
            result = await session.execute(
                select(MarketPrice)
                .where(MarketPrice.timestamp >= since_utc)
                .order_by(MarketPrice.timestamp.asc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "market_id": r.market_id,
                    "token_id": r.token_id,
                    "price": float(r.price) if r.price is not None else 0.5,
                    "timestamp": r.timestamp,
                    "side": r.side,
                }
                for r in rows if r.market_id
            ]

    async def get_prices_for_market_since(
        self, market_id: str, since: datetime, limit: int = 5000
    ) -> List[Dict[str, Any]]:
        """Get price records for a market since given time (for FeatureStore)."""
        if self.session_factory is None:
            return []
        since_utc = _naive_utc(since) if getattr(since, "tzinfo", None) else since.replace(tzinfo=timezone.utc)
        since_utc = _naive_utc(since_utc)
        async with self.get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(MarketPrice)
                .where(MarketPrice.market_id == market_id)
                .where(MarketPrice.timestamp >= since_utc)
                .order_by(MarketPrice.timestamp.asc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "market_id": r.market_id,
                    "token_id": r.token_id,
                    "price": float(r.price) if r.price is not None else 0.5,
                    "timestamp": r.timestamp,
                }
                for r in rows if r.market_id
            ]

    async def get_recent_prices_for_market(
        self, market_id: str, token_ids: Optional[List[str]] = None, limit: int = 50,
        condition_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get recent price records for a market (for bots to check movement).
        Returns list of dicts with market_id, token_id, price, timestamp, ordered by timestamp DESC.
        market_prices.market_id may be m.id (historical ingestion) or m.condition_id (WS streaming).
        """
        if self.session_factory is None:
            return []
        async with self.get_session() as session:
            from sqlalchemy import select, desc, or_
            # Match on both m.id and m.condition_id since prices may be stored under either
            id_filters = [MarketPrice.market_id == market_id]
            if condition_id and condition_id != market_id:
                id_filters.append(MarketPrice.market_id == condition_id)
            stmt = (
                select(MarketPrice)
                .where(or_(*id_filters))
                .order_by(desc(MarketPrice.timestamp))
                .limit(limit)
            )
            if token_ids:
                stmt = stmt.where(MarketPrice.token_id.in_(token_ids))
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "market_id": r.market_id,
                    "token_id": r.token_id,
                    "price": float(r.price) if r.price is not None else 0.5,
                    "timestamp": r.timestamp,
                    "side": r.side,
                }
                for r in rows if r.market_id
            ]

    async def get_recent_prices_bulk(
        self, market_keys: List[tuple], limit_per_market: int = 50,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        B17 FIX: Bulk fetch recent prices for many markets in one query.
        market_keys: list of (market_id, condition_id) tuples.
        Returns dict keyed by market_id → list of price dicts.
        Uses ROW_NUMBER() window function to limit per market.
        """
        if self.session_factory is None or not market_keys:
            return {}
        async with self.get_session() as session:
            # Collect all possible IDs (market_id + condition_id)
            all_ids = set()
            for mid, cid in market_keys:
                all_ids.add(str(mid))
                if cid and str(cid) != str(mid):
                    all_ids.add(str(cid))
            all_ids_list = list(all_ids)

            # Single query with ROW_NUMBER() partitioned by market_id
            from sqlalchemy import text
            result = await session.execute(
                text("""
                    SELECT market_id, token_id, price, timestamp, side
                    FROM (
                        SELECT market_id, token_id, price, timestamp, side,
                            ROW_NUMBER() OVER (
                                PARTITION BY market_id ORDER BY timestamp DESC
                            ) as rn
                        FROM market_prices
                        WHERE market_id = ANY(:ids)
                    ) sub
                    WHERE rn <= :lim
                    ORDER BY market_id, timestamp DESC
                """),
                {"ids": all_ids_list, "lim": limit_per_market}
            )
            rows = result.fetchall()

            # Group by original market_id (map condition_id back)
            cid_to_mid = {}
            for mid, cid in market_keys:
                cid_to_mid[str(mid)] = str(mid)
                if cid and str(cid) != str(mid):
                    cid_to_mid[str(cid)] = str(mid)

            prices_by_market: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                row_mid = str(r[0])
                canonical = cid_to_mid.get(row_mid, row_mid)
                if canonical not in prices_by_market:
                    prices_by_market[canonical] = []
                prices_by_market[canonical].append({
                    "market_id": r[0],
                    "token_id": r[1],
                    "price": float(r[2]) if r[2] is not None else 0.5,
                    "timestamp": r[3],
                    "side": r[4],
                })
            return prices_by_market

    async def get_open_positions_for_bot(self, bot_name: str) -> List[Dict[str, Any]]:
        """
        Get open positions for a bot (for exit logic, e.g. stop-loss/take-profit).
        Returns list of dicts with market_id, token_id, side, size, entry_price, current_price, unrealized_pnl.
        """
        if self.session_factory is None:
            return []
        try:
            from sqlalchemy import select, or_
            async with self.get_session() as session:
                result = await session.execute(
                    select(Position).where(
                        or_(
                            Position.bot_id == bot_name,
                            Position.source_bot == bot_name,
                        ),
                        Position.status == "open",
                    )
                )
                rows = result.scalars().all()
                return [
                    {
                        "market_id": r.market_id,
                        "token_id": r.token_id,
                        "side": r.side,
                        "size": float(r.size or 0),
                        "entry_price": float(r.entry_price or 0),
                        "current_price": float(r.current_price or r.entry_price or 0),
                        "unrealized_pnl": float(r.unrealized_pnl or 0),
                        "opened_at": r.opened_at,  # NaiveUTC datetime for grace period support
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.debug("get_open_positions_for_bot failed for %s: %s", bot_name, e)
            return []

    async def get_open_positions_with_price_history(self, limit_per_position: int = 200) -> List[Dict[str, Any]]:
        """
        Session 44: Get all open positions with their price history since entry.

        Returns a list of position dicts, each with a 'price_history' key containing
        time-series price data since the position was opened. Enables viewing price
        line movement on open trades.

        Returns:
            List of dicts:
                - market_id, token_id, side, size, entry_price, current_price, unrealized_pnl
                - opened_at (datetime)
                - bot_name (str)
                - price_history: [{timestamp, price}] ordered by timestamp ASC
                - price_change_pct: float (current vs entry, %)
        """
        if self.session_factory is None:
            return []
        try:
            from sqlalchemy import select, text as sa_text
            async with self.get_session() as session:
                # 1) Get all open positions
                result = await session.execute(
                    select(Position).where(Position.status == "open")
                )
                positions = result.scalars().all()
                if not positions:
                    return []

                # 2) Build token_id → opened_at mapping for efficient price fetching
                pos_data = []
                token_ids = set()
                for p in positions:
                    entry = float(p.entry_price) if p.entry_price else 0.5
                    current = float(p.current_price) if p.current_price else entry
                    size = float(p.size) if p.size else 0.0
                    side = (p.side or "YES").upper()
                    if side in ("YES", "BUY"):
                        upnl = (current - entry) * size
                        pct = ((current - entry) / entry * 100) if entry > 0 else 0.0
                    else:
                        upnl = (entry - current) * size
                        pct = ((entry - current) / entry * 100) if entry > 0 else 0.0
                    pos_data.append({
                        "market_id": p.market_id,
                        "token_id": p.token_id,
                        "side": p.side,
                        "size": size,
                        "entry_price": entry,
                        "current_price": current,
                        "unrealized_pnl": round(upnl, 4),
                        "opened_at": p.opened_at,
                        "bot_name": p.bot_id or "",
                        "price_change_pct": round(pct, 2),
                        "price_history": [],
                    })
                    if p.token_id:
                        token_ids.add(str(p.token_id))

                # 3) Bulk fetch price history for all open position token_ids
                if token_ids:
                    # Get the earliest opened_at to bound the query
                    earliest = min((p["opened_at"] for p in pos_data if p["opened_at"]), default=None)
                    if earliest:
                        rows = (await session.execute(
                            sa_text("""
                                SELECT token_id, price, timestamp
                                FROM market_prices
                                WHERE token_id = ANY(:tids)
                                  AND timestamp >= :since
                                ORDER BY token_id, timestamp ASC
                            """),
                            {"tids": list(token_ids), "since": earliest}
                        )).fetchall()

                        # Group by token_id
                        from collections import defaultdict
                        prices_by_token: dict = defaultdict(list)
                        for r in rows:
                            prices_by_token[str(r[0])].append({
                                "timestamp": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2]),
                                "price": float(r[1]) if r[1] is not None else 0.5,
                            })

                        # Attach price history to each position (only prices since opened_at)
                        for pd in pos_data:
                            tid = str(pd["token_id"]) if pd["token_id"] else ""
                            opened = pd["opened_at"]
                            if tid in prices_by_token and opened:
                                opened_str = opened.isoformat() if hasattr(opened, "isoformat") else str(opened)
                                pd["price_history"] = [
                                    p for p in prices_by_token[tid]
                                    if p["timestamp"] >= opened_str
                                ][:limit_per_position]

                return pos_data
        except Exception as e:
            logger.debug("get_open_positions_with_price_history failed: %s", e)
            return []

    async def get_ml_features(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Get pre-computed ML features for a market (FeatureStore). Returns None if not found."""
        if self.session_factory is None:
            return None
        async with self.get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(MLFeatures).where(MLFeatures.market_id == market_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                "market_id": row.market_id,
                "computed_at": row.computed_at,
                "features": row.features if isinstance(row.features, dict) else (row.features or {}),
            }

    async def upsert_ml_features(
        self,
        market_id: str,
        computed_at: datetime,
        features: Dict[str, Any],
    ) -> None:
        """Upsert pre-computed ML features for a market (FeatureStore)."""
        if self.session_factory is None:
            return
        computed_utc = _naive_utc(computed_at) if getattr(computed_at, "tzinfo", None) else computed_at.replace(tzinfo=timezone.utc)
        computed_utc = _naive_utc(computed_utc)
        async with self.get_session() as session:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(MLFeatures).values(
                market_id=market_id,
                computed_at=computed_utc,
                features=features,
                updated_at=_naive_utc(datetime.now(timezone.utc)),
            ).on_conflict_do_update(
                index_elements=["market_id"],
                set_={
                    "computed_at": computed_utc,
                    "features": features,
                    "updated_at": _naive_utc(datetime.now(timezone.utc)),
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_elite_net_direction(self, market_id: str, days: int = 90) -> float:
        """
        Market-level elite net direction: weighted avg of elite trade direction by win_rate.
        YES/BUY = +1, NO/SELL = -1. Returns value in [-1, 1]; 0 when no data.
        """
        if self.session_factory is None:
            return 0.0
        try:
            async with self.get_session() as session:
                # D3 FIX: Check both market_id formats (numeric id AND condition_id hash)
                r = await session.execute(text("""
                    SELECT SUM(
                        CASE WHEN t.side IN ('YES', 'BUY') THEN 1.0 ELSE -1.0 END
                        * COALESCE(u.win_rate, 0.5)
                    ) / NULLIF(SUM(COALESCE(u.win_rate, 0.5)), 0)
                    FROM trades t
                    JOIN users u ON t.user_address = u.address
                    WHERE (t.market_id = :market_id OR t.market_id IN (
                        SELECT condition_id FROM markets WHERE CAST(id AS TEXT) = :market_id
                        UNION SELECT CAST(id AS TEXT) FROM markets WHERE condition_id = :market_id
                    ))
                    AND u.is_elite = TRUE
                    AND COALESCE(u.is_likely_market_maker, false) = false
                    AND t.timestamp >= NOW() - INTERVAL '1 day' * :days
                """), {"market_id": market_id, "days": days})
                row = r.fetchone()
                if row and row[0] is not None:
                    v = float(row[0])
                    return max(-1.0, min(1.0, v))
        except Exception as e:
            logger.debug("get_elite_net_direction failed for %s: %s", market_id, e)
        return 0.0

    async def get_elite_direction_decomposed(self, market_id: str) -> Dict[str, float]:
        """Time-decomposed elite direction: 1h, 6h, 24h windows.
        Returns {"elite_direction_1h": float, "elite_direction_6h": float, "elite_direction_24h": float}."""
        result = {"elite_direction_1h": 0.0, "elite_direction_6h": 0.0, "elite_direction_24h": 0.0}
        if self.session_factory is None:
            return result
        try:
            async with self.get_session() as session:
                # D3 FIX: Check both market_id formats (numeric id AND condition_id hash)
                r = await session.execute(text("""
                    SELECT
                        COALESCE(
                            SUM(CASE WHEN t.timestamp >= NOW() - INTERVAL '1 hour' THEN
                                CASE WHEN t.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u.win_rate, 0.5) ELSE 0 END)
                            / NULLIF(SUM(CASE WHEN t.timestamp >= NOW() - INTERVAL '1 hour' THEN COALESCE(u.win_rate, 0.5) ELSE 0 END), 0), 0),
                        COALESCE(
                            SUM(CASE WHEN t.timestamp >= NOW() - INTERVAL '6 hours' THEN
                                CASE WHEN t.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u.win_rate, 0.5) ELSE 0 END)
                            / NULLIF(SUM(CASE WHEN t.timestamp >= NOW() - INTERVAL '6 hours' THEN COALESCE(u.win_rate, 0.5) ELSE 0 END), 0), 0),
                        COALESCE(
                            SUM(CASE WHEN t.timestamp >= NOW() - INTERVAL '24 hours' THEN
                                CASE WHEN t.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u.win_rate, 0.5) ELSE 0 END)
                            / NULLIF(SUM(CASE WHEN t.timestamp >= NOW() - INTERVAL '24 hours' THEN COALESCE(u.win_rate, 0.5) ELSE 0 END), 0), 0)
                    FROM trades t
                    JOIN users u ON t.user_address = u.address
                    WHERE (t.market_id = :market_id OR t.market_id IN (
                        SELECT condition_id FROM markets WHERE CAST(id AS TEXT) = :market_id
                        UNION SELECT CAST(id AS TEXT) FROM markets WHERE condition_id = :market_id
                    ))
                    AND u.is_elite = TRUE
                    AND COALESCE(u.is_likely_market_maker, false) = false
                    AND t.timestamp >= NOW() - INTERVAL '24 hours'
                """), {"market_id": market_id})
                row = r.fetchone()
                if row:
                    result["elite_direction_1h"] = max(-1.0, min(1.0, float(row[0] or 0)))
                    result["elite_direction_6h"] = max(-1.0, min(1.0, float(row[1] or 0)))
                    result["elite_direction_24h"] = max(-1.0, min(1.0, float(row[2] or 0)))
        except Exception as e:
            logger.debug("get_elite_direction_decomposed failed for %s: %s", market_id, e)
        return result

    async def get_risk_state_pnl(self) -> Dict[str, Any]:
        """Return daily_pnl, weekly_pnl from risk_state table (for Performance tab)."""
        if self.session_factory is None:
            return {"daily_pnl": 0.0, "weekly_pnl": 0.0}
        try:
            async with self.get_session() as session:
                r = await session.execute(text(
                    "SELECT daily_pnl, weekly_pnl FROM risk_state WHERE id = 1"
                ))
                row = r.fetchone()
                if row:
                    return {"daily_pnl": float(row[0] or 0), "weekly_pnl": float(row[1] or 0)}
        except Exception as e:
            logger.debug("risk state pnl fetch failed: %s", e)
        return {"daily_pnl": 0.0, "weekly_pnl": 0.0}

    async def get_bot_performance_metrics(self, bot_id: str, days: int = 30) -> Dict[str, Any]:
        """
        Return performance metrics for a bot: wins, losses, total_pnl, trade_count, win_rate.
        Uses Trade.bot_id and Trade.pnl when present.
        """
        if self.session_factory is None:
            return {"bot_id": bot_id, "total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "win_rate": 0.0}
        since = _naive_utc(datetime.now(timezone.utc) - timedelta(days=days))
        async with self.get_session() as session:
            from sqlalchemy import select, and_, func
            result = await session.execute(
                select(Trade).where(
                    and_(
                        (Trade.bot_id == bot_id) | (Trade.user_address == bot_id),
                        func.coalesce(Trade.entry_time, Trade.timestamp) >= since,
                    )
                )
            )
            rows = result.scalars().all()
        wins = sum(1 for t in rows if (t.pnl or 0) > 0)
        losses = sum(1 for t in rows if (t.pnl or 0) < 0)
        total_pnl = sum(float(t.pnl or 0) for t in rows)
        n = len(rows)
        return {
            "bot_id": bot_id,
            "period_days": days,
            "total_trades": n,
            "wins": wins,
            "losses": losses,
            "total_pnl": total_pnl,
            "win_rate": (wins / n) if n else 0.0,
        }

    async def reconcile_pnl(self) -> Dict[str, Any]:
        """
        Cross-check P&L between two sources: Positions.unrealized_pnl vs Trades.pnl.

        These tables track P&L independently:
        - Positions: opened/closed by the bot logic, P&L from resolution backfill
        - Trades: historical blockchain trades, P&L from entry/exit matching

        Returns dict with per-bot comparisons and overall discrepancy.
        """
        if self.session_factory is None:
            return {"error": "no db", "bots": []}
        try:
            from sqlalchemy import text
            async with self.get_session() as session:
                # Source 1: Positions (our primary real-money metric)
                pos_result = await session.execute(text("""
                    SELECT COALESCE(source_bot, bot_id) AS bot,
                           COALESCE(SUM(unrealized_pnl), 0) AS pnl,
                           COUNT(*) AS n
                    FROM positions
                    WHERE status = 'closed'
                      AND unrealized_pnl IS NOT NULL
                      AND (is_paper IS NULL OR is_paper = FALSE)
                    GROUP BY COALESCE(source_bot, bot_id)
                """))
                pos_rows = {r[0]: {"pnl": float(r[1]), "n": int(r[2])} for r in pos_result.fetchall()}

                # Source 2: Trades (blockchain data)
                trade_result = await session.execute(text("""
                    SELECT COALESCE(bot_id, user_address) AS bot,
                           COALESCE(SUM(pnl), 0) AS pnl,
                           COUNT(*) AS n
                    FROM trades
                    WHERE pnl IS NOT NULL
                    GROUP BY COALESCE(bot_id, user_address)
                """))
                trade_rows = {r[0]: {"pnl": float(r[1]), "n": int(r[2])} for r in trade_result.fetchall()}

            all_bots = sorted(set(pos_rows) | set(trade_rows))
            bots = []
            total_pos_pnl = 0.0
            total_trade_pnl = 0.0
            for bot in all_bots:
                p = pos_rows.get(bot, {"pnl": 0.0, "n": 0})
                t = trade_rows.get(bot, {"pnl": 0.0, "n": 0})
                diff = p["pnl"] - t["pnl"]
                total_pos_pnl += p["pnl"]
                total_trade_pnl += t["pnl"]
                bots.append({
                    "bot_id": bot,
                    "positions_pnl": round(p["pnl"], 4),
                    "positions_count": p["n"],
                    "trades_pnl": round(t["pnl"], 4),
                    "trades_count": t["n"],
                    "discrepancy": round(diff, 4),
                })
            return {
                "bots": bots,
                "total_positions_pnl": round(total_pos_pnl, 4),
                "total_trades_pnl": round(total_trade_pnl, 4),
                "total_discrepancy": round(total_pos_pnl - total_trade_pnl, 4),
            }
        except Exception as e:
            logger.debug("reconcile_pnl failed: %s", e)
            return {"error": str(e), "bots": []}

    async def check_database_status(self) -> Dict[str, Any]:
        """
        Enhanced database status check with YES/NO price breakdowns and quality issue counts.

        Returns:
            Dictionary with comprehensive status information
        """
        if self.session_factory is None:
            return {
                "error": "Database not available",
                "status": "❌ DATABASE NOT AVAILABLE"
            }
        
        async with self.get_session() as session:
            from sqlalchemy import select, func
            from base_engine.data.database import Market, MarketPrice, Trade, DataQualityIssue
            
            # Count markets
            result = await session.execute(select(func.count(Market.id)))
            market_count = result.scalar() or 0
            
            # Count markets with token IDs
            result = await session.execute(
                select(func.count(Market.id))
                .where(
                    and_(
                        Market.yes_token_id.isnot(None),
                        Market.yes_token_id != ""
                    )
                )
            )
            markets_with_tokens = result.scalar() or 0
            
            # Count price records
            result = await session.execute(select(func.count(MarketPrice.id)))
            price_count = result.scalar() or 0
            
            # Count YES vs NO price records
            result = await session.execute(
                select(func.count(MarketPrice.id))
                .where(MarketPrice.side == "YES")
            )
            yes_price_count = result.scalar() or 0
            
            result = await session.execute(
                select(func.count(MarketPrice.id))
                .where(MarketPrice.side == "NO")
            )
            no_price_count = result.scalar() or 0
            
            # Count quality issues
            result = await session.execute(select(func.count(DataQualityIssue.id)))
            quality_issues = result.scalar() or 0
            
            # Count quality issues by type
            result = await session.execute(
                select(DataQualityIssue.issue_type, func.count(DataQualityIssue.id))
                .group_by(DataQualityIssue.issue_type)
            )
            quality_by_type = {row[0]: row[1] for row in result.all()}
            
            # Get sample top market
            result = await session.execute(
                select(Market)
                .where(Market.yes_token_id.isnot(None))
                .order_by(Market.liquidity.desc())
                .limit(1)
            )
            top_market = result.scalar_one_or_none()
            
            sample_market = None
            if top_market:
                sample_market = {
                    "id": top_market.id,
                    "question": top_market.question[:50] + "..." if top_market.question and len(top_market.question) > 50 else top_market.question,
                    "yes_token_id": top_market.yes_token_id[:30] + "..." if top_market.yes_token_id and len(top_market.yes_token_id) > 30 else top_market.yes_token_id,
                    "no_token_id": top_market.no_token_id[:30] + "..." if top_market.no_token_id and len(top_market.no_token_id) > 30 else top_market.no_token_id,
                    "liquidity": float(top_market.liquidity) if top_market.liquidity else 0.0
                }
            
            # Calculate token coverage
            token_coverage = (markets_with_tokens / market_count * 100) if market_count > 0 else 0
            
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "markets": {
                    "total": market_count,
                    "with_token_ids": markets_with_tokens,
                    "token_coverage_pct": round(token_coverage, 2)
                },
                "prices": {
                    "total": price_count,
                    "yes_prices": yes_price_count,
                    "no_prices": no_price_count,
                    "yes_no_ratio": round(yes_price_count / no_price_count, 2) if no_price_count > 0 else 0
                },
                "quality_issues": {
                    "total": quality_issues,
                    "by_type": quality_by_type
                },
                "sample_market": sample_market,
                "status": "✅ HEALTHY" if quality_issues == 0 and markets_with_tokens > 0 else "⚠️ ISSUES" if quality_issues > 0 else "❌ NO DATA"
            }
    
    async def verify_market_data_quality(self) -> Dict[str, Any]:
        """
        Verify that markets stored in database are correctly formatted and usable.
        
        Returns:
            Dictionary with verification results including:
            - total_markets: Total count
            - valid_markets: Markets with all required fields
            - invalid_markets: Markets missing required fields
            - sample_valid: Sample of valid markets
            - sample_invalid: Sample of invalid markets
            - issues: List of data quality issues found
        """
        if self.session_factory is None:
            return {
                "error": "Database not available",
                "total_markets": 0,
                "valid_markets": 0,
                "invalid_markets": 0
            }
        
        async with self.get_session() as session:
            from sqlalchemy import select, func
            from base_engine.data.database import Market, MarketPrice, Trade
            
            # Get all markets
            result = await session.execute(select(Market))
            all_markets = result.scalars().all()
            
            total = len(all_markets)
            valid = 0
            invalid = []
            issues = []
            
            required_fields = ["id", "question", "condition_id", "slug"]
            
            for market in all_markets:
                market_dict = {
                    "id": market.id,
                    "question": market.question,
                    "condition_id": market.condition_id,
                    "slug": market.slug,
                    "active": market.active,
                    "liquidity": market.liquidity,
                    "volume": market.volume
                }
                
                missing_fields = [field for field in required_fields if not market_dict.get(field)]
                
                if not missing_fields:
                    valid += 1
                else:
                    invalid.append({
                        "id": market.id,
                        "missing_fields": missing_fields,
                        "has_question": bool(market.question),
                        "has_condition_id": bool(market.condition_id),
                        "has_slug": bool(market.slug)
                    })
                    issues.append(f"Market {market.id}: Missing {', '.join(missing_fields)}")
            
            # Get price data stats
            price_result = await session.execute(select(func.count(MarketPrice.id)))
            price_count = price_result.scalar() or 0
            
            # Get trade data stats
            trade_result = await session.execute(select(func.count(Trade.id)))
            trade_count = trade_result.scalar() or 0
            
            # Get sample valid markets
            sample_valid = []
            for market in all_markets[:5]:
                if market.id and market.question and market.condition_id:
                    sample_valid.append({
                        "id": market.id,
                        "question": market.question[:100] if market.question else None,
                        "condition_id": market.condition_id,
                        "liquidity": market.liquidity,
                        "active": market.active
                    })
            
            return {
                "total_markets": total,
                "valid_markets": valid,
                "invalid_markets": len(invalid),
                "validity_percentage": (valid / total * 100) if total > 0 else 0,
                "price_records": price_count,
                "trade_records": trade_count,
                "sample_valid": sample_valid,
                "sample_invalid": invalid[:5],
                "issues": issues[:10],  # Limit to first 10 issues
                "status": "✅ GOOD" if valid == total and total > 0 else "⚠️ ISSUES FOUND" if total > 0 else "❌ NO DATA"
            }

    # ──────────────────────────────────────────────────────────────────
    # Trade Event Store — immutable append-only log (migration 043)
    # ──────────────────────────────────────────────────────────────────

    async def insert_trade_event(
        self,
        event_type: str,
        bot_name: str,
        market_id: str,
        side: str,
        size: float,
        price: float,
        *,
        execution_mode: str = "paper",
        token_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        order_id: Optional[str] = None,
        fees: float = 0.0,
        realized_pnl: Optional[float] = None,
        confidence: Optional[float] = None,
        predicted_probability: Optional[float] = None,
        model_version: Optional[int] = None,
        model_name: Optional[str] = None,
        event_time: Optional[datetime] = None,
        event_data: Optional[Dict] = None,
    ) -> Optional[int]:
        """
        Append an immutable trade event. Returns sequence_num or None on failure.

        Uses synchronous_commit=off (non-financial table).
        Idempotency_key prevents duplicate events on retries.
        """
        if self.session_factory is None:
            return None
        # S141: ENTRY idempotency excludes order_id to prevent concurrent duplicate entries.
        # order_id is unique per call, so two concurrent whale signals on the same market
        # would both insert. Using token_id instead deduplicates on (market, token, side).
        if event_type == "ENTRY":
            idem_key = f"{bot_name}:{market_id}:{token_id or ''}:{side}:ENTRY"
        else:
            idem_key = f"{bot_name}:{market_id}:{side}:{order_id or correlation_id or ''}"
        evt_time = _naive_utc(event_time) if event_time else _naive_utc(datetime.now(timezone.utc))

        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                await session.execute(_sa_text("SET LOCAL synchronous_commit = off"))

                # S167: FK validation — reject trade events on markets not in DB.
                # Prevents orphan trade_events (678 WeatherBot orphans found in S166 audit).
                # Returns None (not raise) — matches existing failure mode. Callers that
                # check the return value (position_manager, backfill scripts) already abort.
                # paper_trading.py ENTRY path does NOT abort on None — the paper_trade
                # INSERT runs in parallel via asyncio.gather and proceeds independently.
                # This is acceptable: paper_trade is position authority, and the missing
                # event is caught by position_trade_events_check audit. The upstream
                # guard is that bots only trade markets discovered through ingestion.
                # PLAN DEVIATION: plan said "raise exception (fail-closed)" but raising
                # would break 6+ callers with bare except:pass around this call.
                if event_type in ("ENTRY", "EXIT"):
                    _fk_check = await session.execute(
                        _sa_text(
                            "SELECT 1 FROM markets "
                            "WHERE CAST(id AS TEXT) = :mid OR condition_id = :mid "
                            "LIMIT 1"
                        ),
                        {"mid": market_id},
                    )
                    if _fk_check.fetchone() is None:
                        logger.warning(
                            "trade_event FK rejected: market not in DB — "
                            "bot=%s market=%s event=%s",
                            bot_name, market_id, event_type,
                        )
                        return None

                # RESOLUTION events: use atomic INSERT...SELECT to prevent duplicates.
                # ON CONFLICT (idempotency_key, event_time) is broken on partitioned tables
                # because different event_time = different row = no conflict detected.
                # The WHERE NOT EXISTS guard makes RESOLUTION events truly idempotent
                # regardless of event_time differences between backfill runs.
                _params = {
                    "event_type": event_type,
                    "execution_mode": execution_mode,
                    "event_time": evt_time,
                    "bot_name": bot_name,
                    "market_id": market_id,
                    "token_id": token_id,
                    "correlation_id": correlation_id,
                    "order_id": order_id,
                    "side": side,
                    "size": size,
                    "price": price,
                    "fees": fees,
                    "realized_pnl": realized_pnl,
                    "confidence": confidence,
                    "predicted_probability": predicted_probability,
                    "model_version": model_version,
                    "model_name": model_name,
                    "idempotency_key": idem_key,
                    "event_data": json.dumps(event_data or {}, default=str),
                }
                if event_type == "RESOLUTION":
                    # Atomic INSERT...SELECT to prevent duplicates.
                    # ON CONFLICT (idempotency_key, event_time) is broken on partitioned
                    # tables because different event_time = no conflict detected.
                    # S120: Also reject RESOLUTION if position was fully exited — P&L
                    # already captured by EXIT events; emitting RESOLUTION would double-count.
                    result = await session.execute(
                        _sa_text(
                            "INSERT INTO trade_events ("
                            "  event_type, execution_mode, event_time, bot_name, market_id,"
                            "  token_id, correlation_id, order_id, side, size, price, fees,"
                            "  realized_pnl, confidence, predicted_probability,"
                            "  model_version, model_name, idempotency_key, event_data"
                            ") SELECT"
                            "  :event_type, :execution_mode, :event_time, :bot_name, :market_id,"
                            "  :token_id, :correlation_id, :order_id, :side, :size, :price, :fees,"
                            "  :realized_pnl, :confidence, :predicted_probability,"
                            "  :model_version, :model_name, :idempotency_key, CAST(:event_data AS jsonb)"
                            " WHERE NOT EXISTS ("
                            "   SELECT 1 FROM trade_events te"
                            "   WHERE te.bot_name = :bot_name"
                            "     AND te.market_id = :market_id"
                            "     AND te.event_type = 'RESOLUTION'"
                            "   -- S167: side removed from dedup — one RESOLUTION per (bot, market)"
                            " )"
                            " AND NOT EXISTS ("
                            "   SELECT 1 FROM trade_events te_exit"
                            "   WHERE te_exit.bot_name = :bot_name"
                            "     AND te_exit.market_id = :market_id"
                            "     AND te_exit.event_type = 'EXIT'"
                            "   HAVING SUM(te_exit.size) >= ("
                            "     SELECT COALESCE(SUM(te_entry.size), 0)"
                            "     FROM trade_events te_entry"
                            "     WHERE te_entry.bot_name = :bot_name"
                            "       AND te_entry.market_id = :market_id"
                            "       AND te_entry.event_type = 'ENTRY'"
                            "   )"
                            " )"
                            " RETURNING sequence_num"
                        ),
                        _params,
                    )
                else:
                    # S167: EXIT over-size guard — reject EXIT if total EXIT size
                    # would exceed total ENTRY size for same (bot_name, market_id).
                    # Side-agnostic: historical EXITs use side='SELL', ENTRYs use YES/NO.
                    if event_type == "EXIT":
                        _size_check = await session.execute(
                            _sa_text(
                                "SELECT"
                                "  COALESCE(SUM(CASE WHEN te.event_type = 'ENTRY' THEN te.size ELSE 0 END), 0) AS total_entry,"
                                "  COALESCE(SUM(CASE WHEN te.event_type = 'EXIT' THEN te.size ELSE 0 END), 0) AS total_exit"
                                " FROM trade_events te"
                                " WHERE te.bot_name = :bot_name"
                                "   AND te.market_id = :market_id"
                                "   AND te.event_type IN ('ENTRY', 'EXIT')"
                            ),
                            {"bot_name": bot_name, "market_id": market_id},
                        )
                        _sz = _size_check.fetchone()
                        if _sz:
                            _total_entry, _total_exit = float(_sz[0]), float(_sz[1])
                            if _total_exit + size > _total_entry + 1e-6:
                                logger.warning(
                                    "EXIT over-size rejected: bot=%s market=%s "
                                    "exit_size=%.6f existing_exits=%.6f total_entries=%.6f",
                                    bot_name, market_id, size, _total_exit, _total_entry,
                                )
                                return None

                    # S159: WHERE NOT EXISTS is partition-safe for ENTRY/EXIT.
                    # ON CONFLICT (idempotency_key, event_time) is per-partition — a retry
                    # with a different event_time (e.g., month boundary) bypasses dedup.
                    # Proven pattern: RESOLUTION path above uses WHERE NOT EXISTS since S87.
                    result = await session.execute(
                        _sa_text(
                            "INSERT INTO trade_events ("
                            "  event_type, execution_mode, event_time, bot_name, market_id,"
                            "  token_id, correlation_id, order_id, side, size, price, fees,"
                            "  realized_pnl, confidence, predicted_probability,"
                            "  model_version, model_name, idempotency_key, event_data"
                            ") SELECT"
                            "  :event_type, :execution_mode, :event_time, :bot_name, :market_id,"
                            "  :token_id, :correlation_id, :order_id, :side, :size, :price, :fees,"
                            "  :realized_pnl, :confidence, :predicted_probability,"
                            "  :model_version, :model_name, :idempotency_key, CAST(:event_data AS jsonb)"
                            " WHERE NOT EXISTS ("
                            "   SELECT 1 FROM trade_events te"
                            "   WHERE te.idempotency_key = :idempotency_key"
                            "     AND :idempotency_key IS NOT NULL"
                            " )"
                            " RETURNING sequence_num"
                        ),
                        _params,
                    )
                row = result.fetchone()
                await session.commit()
                return row[0] if row else None
        except Exception as e:
            logger.warning("trade_event %s persist failed for %s: %s", event_type, market_id, e)
            return None

    # insert_trade_model_linkage removed — migration 052 drops table (0 readers)
    # aggregate_model_performance removed — migration 052 drops table (0 readers)

    async def insert_shadow_fill(
        self,
        *,
        bot_name: str,
        market_id: str,
        token_id: Optional[str] = None,
        side: str = "BUY",
        order_size_shares: Optional[float] = None,
        order_size_usd: Optional[float] = None,
        signal_price: Optional[float] = None,
        confidence: Optional[float] = None,
        edge_at_signal: Optional[float] = None,
        latency_ms: Optional[float] = None,
        book_snapshot: Optional[Any] = None,
        best_ask: Optional[float] = None,
        best_bid: Optional[float] = None,
        spread: Optional[float] = None,
        depth_at_best_usd: Optional[float] = None,
        total_depth_usd: Optional[float] = None,
        vwap_fill_price: Optional[float] = None,
        book_walk_slippage: Optional[float] = None,
        fill_fraction: Optional[float] = None,
        edge_at_vwap: Optional[float] = None,
        trade_executed: bool = True,
        execution_price: Optional[float] = None,
        correlation_id: Optional[str] = None,
        model_name: Optional[str] = None,
        event_data: Optional[Dict] = None,
    ) -> Optional[int]:
        """S115: Record a shadow fill row for retroactive P&L analysis.

        Every trade signal gets a row — whether executed or not.
        Book snapshot + VWAP enable true slippage analysis after resolution.
        """
        if self.session_factory is None:
            return None
        try:
            import json as _json
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                result = await session.execute(
                    _sa_text(
                        "INSERT INTO shadow_fills ("
                        "  bot_name, market_id, token_id, side,"
                        "  order_size_shares, order_size_usd, signal_price,"
                        "  confidence, edge_at_signal, latency_ms,"
                        "  book_snapshot, best_ask, best_bid, spread,"
                        "  depth_at_best_usd, total_depth_usd,"
                        "  vwap_fill_price, book_walk_slippage, fill_fraction,"
                        "  edge_at_vwap, trade_executed, execution_price,"
                        "  correlation_id, model_name, event_data"
                        ") VALUES ("
                        "  :bot_name, :market_id, :token_id, :side,"
                        "  :order_size_shares, :order_size_usd, :signal_price,"
                        "  :confidence, :edge_at_signal, :latency_ms,"
                        "  CAST(:book_snapshot AS jsonb), :best_ask, :best_bid, :spread,"
                        "  :depth_at_best_usd, :total_depth_usd,"
                        "  :vwap_fill_price, :book_walk_slippage, :fill_fraction,"
                        "  :edge_at_vwap, :trade_executed, :execution_price,"
                        "  :correlation_id, :model_name, CAST(:event_data AS jsonb)"
                        ") RETURNING id"
                    ),
                    {
                        "bot_name": bot_name,
                        "market_id": market_id,
                        "token_id": token_id,
                        "side": side,
                        "order_size_shares": order_size_shares,
                        "order_size_usd": order_size_usd,
                        "signal_price": signal_price,
                        "confidence": confidence,
                        "edge_at_signal": edge_at_signal,
                        "latency_ms": latency_ms,
                        "book_snapshot": _json.dumps(book_snapshot) if book_snapshot else None,
                        "best_ask": best_ask,
                        "best_bid": best_bid,
                        "spread": spread,
                        "depth_at_best_usd": depth_at_best_usd,
                        "total_depth_usd": total_depth_usd,
                        "vwap_fill_price": vwap_fill_price,
                        "book_walk_slippage": book_walk_slippage,
                        "fill_fraction": fill_fraction,
                        "edge_at_vwap": edge_at_vwap,
                        "trade_executed": trade_executed,
                        "execution_price": execution_price,
                        "correlation_id": correlation_id,
                        "model_name": model_name,
                        "event_data": _json.dumps(event_data) if event_data else None,
                    },
                )
                row = result.fetchone()
                await session.commit()
                return row[0] if row else None
        except Exception as e:
            logger.debug("shadow_fill insert failed for %s: %s", market_id, e)
            return None

    async def backfill_shadow_resolution(self) -> int:
        """S115: Backfill shadow_fills with resolution data.

        For resolved markets, compute shadow_pnl from vwap_fill_price:
          YES resolution: pnl = (1.0 - vwap) * shares
          NO resolution:  pnl = -vwap * shares
        """
        if self.session_factory is None:
            return 0
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                result = await session.execute(
                    _sa_text(
                        "UPDATE shadow_fills sf SET "
                        "  resolved_at = tm.resolved_at, "
                        "  resolution_outcome = tm.resolution, "
                        "  shadow_pnl = CASE "
                        "    WHEN UPPER(tm.resolution) = 'YES' AND UPPER(sf.side) = 'YES' THEN "
                        "      (1.0 - sf.vwap_fill_price) * sf.order_size_shares "
                        "    WHEN UPPER(tm.resolution) = 'YES' AND UPPER(sf.side) = 'NO' THEN "
                        "      (0.0 - sf.vwap_fill_price) * sf.order_size_shares "
                        "    WHEN UPPER(tm.resolution) = 'NO' AND UPPER(sf.side) = 'YES' THEN "
                        "      (0.0 - sf.vwap_fill_price) * sf.order_size_shares "
                        "    WHEN UPPER(tm.resolution) = 'NO' AND UPPER(sf.side) = 'NO' THEN "
                        "      (1.0 - sf.vwap_fill_price) * sf.order_size_shares "
                        "    ELSE NULL "
                        "  END "
                        "FROM traded_markets tm "
                        "WHERE sf.market_id = tm.market_id "
                        "  AND tm.resolution IN ('YES', 'NO') "
                        "  AND tm.resolved_at IS NOT NULL "
                        "  AND sf.resolved_at IS NULL "
                        "  AND sf.vwap_fill_price IS NOT NULL"
                    )
                )
                updated = result.rowcount
                await session.commit()
                if updated > 0:
                    logger.info("shadow_fills_resolution_backfill", updated=updated)
                return updated
        except Exception as e:
            logger.debug("shadow_fills resolution backfill failed: %s", e)
            return 0

    async def mark_market_resolved(
        self,
        market_id: str,
        resolution: str,
        pnl: Optional[float] = None,
    ) -> None:
        """Mark a traded market as resolved and record the outcome."""
        if self.session_factory is None:
            return
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                await session.execute(
                    _sa_text(
                        "UPDATE traded_markets SET "
                        "  status = 'resolved', resolved = TRUE,"
                        "  resolution = :resolution,"
                        "  resolved_at = NOW(),"
                        "  resolution_pnl = :pnl "
                        "WHERE market_id = :market_id AND status = 'open'"
                    ),
                    {"market_id": market_id, "resolution": resolution, "pnl": pnl},
                )
                await session.commit()
        except Exception as e:
            logger.warning("mark_market_resolved failed for %s: %s", market_id, e)

    # ──────────────────────────────────────────────────────────────────
    # Position & Equity Snapshots — daily state capture (migration 045)
    # ──────────────────────────────────────────────────────────────────

    # take_position_snapshot removed — migration 052 drops table (0 readers)

    async def take_equity_snapshot(self, snapshot_date: Optional[date] = None) -> int:
        """Snapshot per-bot equity with peak/drawdown/Sharpe. Returns bot count."""
        if self.session_factory is None:
            return 0
        _date = snapshot_date or date.today()
        bot_count = 0
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                # Per-bot aggregates from open positions
                bots_result = await session.execute(
                    _sa_text(
                        "SELECT COALESCE(source_bot, bot_id) AS bot_name,"
                        "  COUNT(*) AS open_positions,"
                        "  COALESCE(SUM(size * entry_price), 0) AS deployed_capital,"
                        "  COALESCE(SUM(size * (COALESCE(current_price, entry_price) - entry_price)), 0) AS unrealized_pnl "
                        "FROM positions WHERE status = 'open' "
                        "GROUP BY COALESCE(source_bot, bot_id)"
                    )
                )
                bots = bots_result.fetchall()

                for bot_row in bots:
                    bot_name = bot_row[0]
                    open_positions = bot_row[1]
                    deployed_capital = float(bot_row[2])
                    unrealized_pnl = float(bot_row[3])

                    # Realized PnL — from trade_events (authoritative, includes EXIT + RESOLUTION)
                    rpnl_result = await session.execute(
                        _sa_text(
                            "SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0) "
                            "FROM trade_events "
                            "WHERE bot_name = :bot AND realized_pnl IS NOT NULL"
                        ),
                        {"bot": bot_name},
                    )
                    realized = float(rpnl_result.scalar() or 0)

                    # Daily trades + win/loss from trade_events
                    daily_result = await session.execute(
                        _sa_text(
                            "SELECT COUNT(*) AS daily_trades,"
                            "  COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) > 0) AS wins,"
                            "  COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) < 0) AS losses "
                            "FROM trade_events "
                            "WHERE bot_name = :bot AND event_type IN ('EXIT', 'RESOLUTION') "
                            "  AND event_time >= CAST(:snap_date AS date)"
                        ),
                        {"bot": bot_name, "snap_date": _date},
                    )
                    daily_row = daily_result.fetchone()
                    daily_trades = daily_row[0] if daily_row else 0
                    win_count = daily_row[1] if daily_row else 0
                    loss_count = daily_row[2] if daily_row else 0

                    # Per-bot capital from config
                    from config.settings import settings as _settings
                    _bot_capitals = {
                        "WeatherBot": float(getattr(_settings, "WEATHER_TOTAL_CAPITAL", 5000)),
                        "MirrorBot": float(getattr(_settings, "MIRROR_TOTAL_CAPITAL", 20000)),
                        "EsportsBot": float(getattr(_settings, "ESPORTS_TOTAL_CAPITAL", 5000)),
                        "EsportsLiveBot": float(getattr(_settings, "ESPORTS_TOTAL_CAPITAL", 5000)),
                    }
                    total_capital = _bot_capitals.get(bot_name, 1000.0)
                    current_equity = total_capital + realized + unrealized_pnl

                    # Peak equity from history
                    peak_result = await session.execute(
                        _sa_text(
                            "SELECT MAX(peak_equity) FROM equity_snapshots WHERE bot_name = :bot"
                        ),
                        {"bot": bot_name},
                    )
                    prev_peak = float(peak_result.scalar() or current_equity)
                    peak = max(prev_peak, current_equity)
                    drawdown = (peak - current_equity) / peak if peak > 0 else 0

                    # Rolling 30-day Sharpe
                    sharpe_result = await session.execute(
                        _sa_text(
                            "WITH daily_returns AS ("
                            "  SELECT total_equity - LAG(total_equity) OVER (ORDER BY snapshot_date) AS daily_return "
                            "  FROM equity_snapshots WHERE bot_name = :bot "
                            "  ORDER BY snapshot_date DESC LIMIT 30"
                            ") "
                            "SELECT CASE WHEN STDDEV(daily_return) > 0 "
                            "  THEN AVG(daily_return) / STDDEV(daily_return) * SQRT(252) "
                            "  ELSE NULL END "
                            "FROM daily_returns WHERE daily_return IS NOT NULL"
                        ),
                        {"bot": bot_name},
                    )
                    sharpe = sharpe_result.scalar()

                    await session.execute(
                        _sa_text(
                            "INSERT INTO equity_snapshots ("
                            "  snapshot_date, bot_name, total_capital, deployed_capital,"
                            "  realized_pnl, unrealized_pnl, total_equity, open_positions,"
                            "  daily_trades, win_count, loss_count,"
                            "  peak_equity, drawdown_pct, rolling_sharpe, execution_mode"
                            ") VALUES (:snap_date, :bot, :capital, :deployed,"
                            "  :realized, :unrealized, :total_equity, :positions,"
                            "  :daily_trades, :wins, :losses,"
                            "  :peak, :drawdown, :sharpe, 'paper')"
                            " ON CONFLICT (snapshot_date, bot_name) DO UPDATE SET"
                            "  total_capital = EXCLUDED.total_capital,"
                            "  deployed_capital = EXCLUDED.deployed_capital,"
                            "  realized_pnl = EXCLUDED.realized_pnl,"
                            "  unrealized_pnl = EXCLUDED.unrealized_pnl,"
                            "  total_equity = EXCLUDED.total_equity,"
                            "  open_positions = EXCLUDED.open_positions,"
                            "  daily_trades = EXCLUDED.daily_trades,"
                            "  win_count = EXCLUDED.win_count,"
                            "  loss_count = EXCLUDED.loss_count,"
                            "  peak_equity = EXCLUDED.peak_equity,"
                            "  drawdown_pct = EXCLUDED.drawdown_pct,"
                            "  rolling_sharpe = EXCLUDED.rolling_sharpe"
                        ),
                        {
                            "snap_date": _date,
                            "bot": bot_name,
                            "capital": total_capital,
                            "deployed": deployed_capital,
                            "realized": realized,
                            "unrealized": unrealized_pnl,
                            "total_equity": current_equity,
                            "positions": open_positions,
                            "daily_trades": daily_trades,
                            "wins": win_count,
                            "losses": loss_count,
                            "peak": peak,
                            "drawdown": drawdown,
                            "sharpe": sharpe,
                        },
                    )
                    bot_count += 1

                await session.commit()
                logger.info("equity_snapshot: %d bots captured for %s", bot_count, _date)
                return bot_count
        except Exception as e:
            logger.warning("take_equity_snapshot failed: %s", e)
            return 0

    # ──────────────────────────────────────────────────────────────────
    # Position Rebuild — crash recovery from event replay (migration 043)
    # ──────────────────────────────────────────────────────────────────

    async def rebuild_positions_from_events(self, bot_name: str) -> Dict[str, Dict]:
        """
        Reconstruct position state from trade_events for a specific bot.
        Returns dict of {market_id:side: {market_id, side, net_quantity, avg_price}}.
        Called during bot startup if positions table is empty/stale.
        """
        if self.session_factory is None:
            return {}
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                result = await session.execute(
                    _sa_text(
                        "SELECT market_id, side, size, price, event_type "
                        "FROM trade_events "
                        "WHERE bot_name = :bot "
                        "  AND event_type IN ('ENTRY', 'EXIT', 'RESOLUTION') "
                        "ORDER BY sequence_num ASC"
                    ),
                    {"bot": bot_name},
                )
                rows = result.fetchall()

            positions: Dict[str, Dict[str, Dict[str, float]]] = {}
            for row in rows:
                mid = row[0]
                side_val = row[1]
                size_val = float(row[2] or 0)
                price_val = float(row[3] or 0)
                evt_type = row[4]

                if mid not in positions:
                    positions[mid] = {
                        "YES": {"qty": 0.0, "cost": 0.0},
                        "NO": {"qty": 0.0, "cost": 0.0},
                    }

                if evt_type == "ENTRY" and side_val in ("YES", "NO"):
                    positions[mid][side_val]["qty"] += size_val
                    positions[mid][side_val]["cost"] += size_val * price_val
                elif evt_type in ("EXIT", "RESOLUTION"):
                    for s in ("YES", "NO"):
                        positions[mid][s] = {"qty": 0.0, "cost": 0.0}

            # Filter to open positions only
            result_dict: Dict[str, Dict] = {}
            for mid, sides in positions.items():
                for side_val, data in sides.items():
                    if data["qty"] > 0:
                        result_dict[f"{mid}:{side_val}"] = {
                            "market_id": mid,
                            "side": side_val,
                            "net_quantity": data["qty"],
                            "avg_price": data["cost"] / data["qty"],
                        }

            logger.info("rebuild_positions: %d open positions rebuilt for %s", len(result_dict), bot_name)
            return result_dict
        except Exception as e:
            logger.warning("rebuild_positions_from_events failed for %s: %s", bot_name, e)
            return {}

    # ──────────────────────────────────────────────────────────────────
    # Reconciliation — 6h integrity check (migration 046)
    # ──────────────────────────────────────────────────────────────────

    async def repair_orphaned_positions(self) -> int:
        """
        Auto-repair: create paper_trades rows for open positions that lack them.
        Root cause: insert_paper_trade() can fail after 3 retries while
        confirm_position() already wrote to positions table.
        Returns number of repaired rows (-1 on error).
        """
        if self.session_factory is None:
            return -1
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                result = await session.execute(
                    _sa_text(
                        "INSERT INTO paper_trades "
                        "  (order_id, market_id, token_id, bot_name, side, size, price, "
                        "   created_at, status, submitted_at, filled_at) "
                        "SELECT "
                        "  'repair-' || p.id::text, p.market_id, p.token_id, "
                        "  COALESCE(p.source_bot, p.bot_id), COALESCE(p.side, 'YES'), "
                        "  p.size, COALESCE(p.entry_price, p.current_price, 0.50), "
                        "  COALESCE(p.opened_at, NOW()), 'filled', "
                        "  COALESCE(p.opened_at, NOW()), COALESCE(p.opened_at, NOW()) "
                        "FROM positions p "
                        "LEFT JOIN paper_trades pt "
                        "  ON pt.market_id = p.market_id "
                        "  AND pt.bot_name = COALESCE(p.source_bot, p.bot_id) "
                        "  AND LOWER(pt.side) != 'sell' "
                        "WHERE p.status = 'open' AND pt.id IS NULL"
                    )
                )
                repaired = result.rowcount
                # Also backfill trade_events ENTRY records for orphans.
                # trade_events is the P&L authority — without an ENTRY event,
                # resolution backfill can't compute realized P&L.
                # S143: Join paper_trades to recover confidence (was lost in repair path).
                te_result = await session.execute(
                    _sa_text(
                        "INSERT INTO trade_events "
                        "  (event_type, execution_mode, event_time, bot_name, market_id, "
                        "   token_id, side, size, price, confidence, idempotency_key) "
                        "SELECT "
                        "  'ENTRY', 'paper', COALESCE(p.opened_at, NOW()), "
                        "  COALESCE(p.source_bot, p.bot_id), p.market_id, p.token_id, "
                        "  COALESCE(p.side, 'YES'), p.size, "
                        "  COALESCE(p.entry_price, p.current_price, 0.50), "
                        "  pt_match.confidence, "
                        "  'repair-entry-' || p.id::text "
                        "FROM positions p "
                        "LEFT JOIN LATERAL ("
                        "  SELECT confidence FROM paper_trades pt2 "
                        "  WHERE pt2.market_id = p.market_id "
                        "    AND pt2.bot_name = COALESCE(p.source_bot, p.bot_id) "
                        "    AND LOWER(pt2.side) != 'sell' "
                        "  ORDER BY pt2.created_at DESC LIMIT 1"
                        ") pt_match ON true "
                        "WHERE p.status = 'open' "
                        "  AND p.opened_at < NOW() - INTERVAL '60 seconds' "
                        "  AND NOT EXISTS ("
                        "    SELECT 1 FROM trade_events te "
                        "    WHERE te.bot_name = COALESCE(p.source_bot, p.bot_id) "
                        "      AND te.market_id = p.market_id "
                        "      AND te.event_type = 'ENTRY'"
                        "  )"
                    )
                )
                te_repaired = te_result.rowcount
                await session.commit()
                if repaired > 0 or te_repaired > 0:
                    logger.warning(
                        "repair_orphaned_positions: backfilled %d paper_trades, %d trade_events",
                        repaired, te_repaired,
                    )
                return repaired + te_repaired
        except Exception as e:
            logger.warning("repair_orphaned_positions failed: %s", e)
            return -1

    async def run_reconciliation(self) -> int:
        """
        Cross-validate positions vs trade_events vs traded_markets.
        Returns number of NEW breaks found (-1 on error).
        Filters: active bots only, $0.50 tolerance, dedup per (date, type, bot, market).
        """
        if self.session_factory is None:
            return -1
        breaks_found = 0
        today = date.today()
        # Only recon active bots — skip disabled bots and archived EnsembleBot
        import os as _os
        _active_bots = set()
        for _bname in ("WeatherBot", "MirrorBot", "EsportsBot", "EsportsLiveBot"):
            _env_key = f"BOT_ENABLED_{_bname.upper()}"
            if _os.getenv(_env_key, "true").lower() != "false":
                _active_bots.add(_bname)
        if not _active_bots:
            _active_bots = {"WeatherBot", "MirrorBot", "EsportsBot", "EsportsLiveBot"}

        # Auto-repair orphaned positions before checking for mismatches
        try:
            _repaired = await self.repair_orphaned_positions()
            if _repaired and _repaired > 0:
                logger.info("reconciliation: auto-repaired %d orphaned positions", _repaired)
        except Exception as _rep_err:
            logger.warning("db_recon_repair_failed", error=str(_rep_err))

        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                # Check 1: positions vs trade_events net size mismatch
                mismatches = await session.execute(
                    _sa_text(
                        "WITH position_state AS ("
                        "  SELECT COALESCE(source_bot, bot_id) AS bot_name, market_id, side, size FROM positions WHERE status = 'open'"
                        "), trade_state AS ("
                        "  SELECT bot_name, market_id, side,"
                        "    SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)"
                        "    - SUM(CASE WHEN event_type = 'EXIT' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS total_size"
                        "  FROM trade_events"
                        "  WHERE event_type IN ('ENTRY', 'EXIT')"
                        "  GROUP BY bot_name, market_id, side"
                        "  HAVING SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)"
                        "    - SUM(CASE WHEN event_type = 'EXIT' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) > 0.01"
                        ") "
                        "SELECT "
                        "  COALESCE(p.bot_name, t.bot_name) AS bot_name,"
                        "  COALESCE(p.market_id, t.market_id) AS market_id,"
                        "  p.size AS position_size,"
                        "  t.total_size AS trade_size,"
                        "  ABS(COALESCE(p.size, 0) - COALESCE(t.total_size, 0)) AS delta "
                        "FROM position_state p "
                        "FULL OUTER JOIN trade_state t "
                        "  ON p.bot_name = t.bot_name AND p.market_id = t.market_id AND p.side = t.side "
                        "WHERE ABS(COALESCE(p.size, 0) - COALESCE(t.total_size, 0)) > 0.50"
                    )
                )
                mismatch_rows = mismatches.fetchall()

                for m in mismatch_rows:
                    _bot = m[0]
                    if _bot not in _active_bots:
                        continue
                    # Dedup: skip if same break already recorded today
                    _dup = await session.execute(
                        _sa_text(
                            "SELECT 1 FROM reconciliation_breaks "
                            "WHERE recon_date = :today AND recon_type = 'POSITION' "
                            "AND bot_name = :bot AND market_id = :market LIMIT 1"
                        ),
                        {"today": today, "bot": _bot, "market": m[1]},
                    )
                    if _dup.fetchone():
                        continue
                    await session.execute(
                        _sa_text(
                            "INSERT INTO reconciliation_breaks "
                            "  (recon_date, recon_type, bot_name, market_id,"
                            "   internal_value, external_value, difference, severity, details) "
                            "VALUES (:today, 'POSITION', :bot, :market,"
                            "  :pos_size, :trade_size, :delta, 'WARNING',"
                            "  CAST('{\"source\": \"positions_vs_trade_events\"}' AS jsonb))"
                        ),
                        {
                            "today": today,
                            "bot": _bot,
                            "market": m[1],
                            "pos_size": m[2],
                            "trade_size": m[3],
                            "delta": m[4],
                        },
                    )
                    breaks_found += 1

                # Check 2: traded_markets status vs actual resolution
                stale = await session.execute(
                    _sa_text(
                        "SELECT tm.market_id, tm.bot_names "
                        "FROM traded_markets tm "
                        "WHERE tm.status = 'open' "
                        "  AND EXISTS ("
                        "    SELECT 1 FROM paper_trades pt "
                        "    WHERE pt.market_id = tm.market_id "
                        "      AND pt.realized_pnl IS NOT NULL"
                        "      AND pt.side IN ('YES', 'NO')"
                        "  )"
                    )
                )
                stale_rows = stale.fetchall()

                for s in stale_rows:
                    _sbot = str(s[1]).split(",")[0] if s[1] else "unknown"
                    if _sbot not in _active_bots:
                        continue
                    _dup = await session.execute(
                        _sa_text(
                            "SELECT 1 FROM reconciliation_breaks "
                            "WHERE recon_date = :today AND recon_type = 'STALE_POSITION' "
                            "AND market_id = :market LIMIT 1"
                        ),
                        {"today": today, "market": s[0]},
                    )
                    if _dup.fetchone():
                        continue
                    await session.execute(
                        _sa_text(
                            "INSERT INTO reconciliation_breaks "
                            "  (recon_date, recon_type, bot_name, market_id,"
                            "   severity, details) "
                            "VALUES (:today, 'STALE_POSITION', :bot, :market,"
                            "  'CRITICAL',"
                            "  CAST('{\"source\": \"traded_markets_status_mismatch\"}' AS jsonb))"
                        ),
                        {
                            "today": today,
                            "bot": _sbot,
                            "market": s[0],
                        },
                    )
                    breaks_found += 1

                await session.commit()

            if breaks_found:
                logger.warning("reconciliation: %d breaks found", breaks_found)
            else:
                logger.info("reconciliation: clean — 0 breaks")
            return breaks_found
        except Exception as e:
            logger.warning("run_reconciliation failed: %s", e)
            return -1

    # ──────────────────────────────────────────────────────────────────
    # COPY Bulk Insert — 10x faster for batch trade_events (migration 043)
    # ──────────────────────────────────────────────────────────────────

    async def copy_insert_trade_events(self, events: List[Dict]) -> int:
        """
        Bulk insert trade events using raw asyncpg COPY (10x faster than INSERT).
        Used for backfill and replay operations. Returns count of rows inserted.
        Falls back to individual inserts on failure.
        """
        if not events or self.session_factory is None:
            return 0

        columns = [
            "event_type", "execution_mode", "event_time", "bot_name",
            "market_id", "token_id", "correlation_id", "order_id",
            "side", "size", "price", "fees", "realized_pnl",
            "confidence", "predicted_probability", "model_version",
            "model_name", "idempotency_key", "event_data",
        ]

        try:
            # Acquire semaphore to stay within pool budget (mirrors get_session pattern)
            if self._db_semaphore:
                await asyncio.wait_for(self._db_semaphore.acquire(), timeout=15.0)
            try:
                # Get raw asyncpg connection from SQLAlchemy engine
                async with self.engine.connect() as conn:
                    raw_conn = await conn.get_raw_connection()
                    asyncpg_conn = raw_conn.dbapi_connection

                    records = []
                    for e in events:
                        records.append(tuple(
                            e.get(col) for col in columns
                        ))

                    result = await asyncpg_conn.copy_records_to_table(
                        "trade_events",
                        records=records,
                        columns=columns,
                    )
                    count = int(result.split()[-1]) if result else len(records)
                    logger.info("copy_insert_trade_events: %d events inserted", count)
                    return count
            finally:
                if self._db_semaphore:
                    self._db_semaphore.release()
        except Exception as e:
            logger.warning("copy_insert_trade_events failed, falling back to individual inserts: %s", e)
            # Fallback to individual inserts
            inserted = 0
            for evt in events:
                seq = await self.insert_trade_event(
                    event_type=evt.get("event_type", "ENTRY"),
                    bot_name=evt.get("bot_name", ""),
                    market_id=evt.get("market_id", ""),
                    side=evt.get("side", "YES"),
                    size=float(evt.get("size", 0)),
                    price=float(evt.get("price", 0)),
                    execution_mode=evt.get("execution_mode", "paper"),
                    token_id=evt.get("token_id"),
                    correlation_id=evt.get("correlation_id"),
                    order_id=evt.get("order_id"),
                    realized_pnl=evt.get("realized_pnl"),
                    confidence=evt.get("confidence"),
                    event_time=evt.get("event_time"),
                    event_data=evt.get("event_data"),
                )
                if seq is not None:
                    inserted += 1
            return inserted

    # register_model, promote_model, update_model_performance, register_feature_set
    # removed — migration 052 drops model_registry, model_performance_daily, feature_sets tables

    async def ensure_future_partitions(self) -> int:
        """Create monthly partitions 3 months ahead for trade_events.
        Returns number of partitions created."""
        if self.session_factory is None:
            return 0
        created = 0
        try:
            from sqlalchemy import text as _sa_text
            async with self.get_session() as session:
                # Find latest existing partition month for trade_events
                result = await session.execute(
                    _sa_text(
                        "SELECT relname FROM pg_class "
                        "WHERE relname ~ '^trade_events_\\d{4}_\\d{2}$' AND relkind = 'r' "
                        "ORDER BY relname DESC LIMIT 1"
                    )
                )
                row = result.fetchone()
                if not row:
                    return 0

                latest = row[0]  # e.g. trade_events_2026_12
                parts = latest.split("_")
                latest_year = int(parts[2])
                latest_month = int(parts[3])

                # Check if we're within 2 months of the last partition
                from datetime import date as _d
                today = _d.today()
                months_left = (latest_year - today.year) * 12 + (latest_month - today.month)
                if months_left > 2:
                    return 0

                # Create next 12 monthly partitions for trade_events
                import calendar
                cur_year = latest_year
                cur_month = latest_month
                for _ in range(12):
                    cur_month += 1
                    if cur_month > 12:
                        cur_month = 1
                        cur_year += 1
                    next_month = cur_month + 1
                    next_year = cur_year
                    if next_month > 12:
                        next_month = 1
                        next_year += 1

                    start = f"{cur_year}-{cur_month:02d}-01"
                    end = f"{next_year}-{next_month:02d}-01"
                    te_name = f"trade_events_{cur_year}_{cur_month:02d}"

                    try:
                        await session.execute(
                            _sa_text(
                                f"CREATE TABLE IF NOT EXISTS {te_name} "
                                f"PARTITION OF trade_events "
                                f"FOR VALUES FROM ('{start}') TO ('{end}')"
                            )
                        )
                        created += 1
                    except Exception as _part_err:
                        logger.debug("db_partition_create_skipped", month=te_name, error=str(_part_err))

                await session.commit()
                if created > 0:
                    logger.info("ensure_future_partitions: created %d partitions", created)
                return created
        except Exception as e:
            logger.warning("ensure_future_partitions failed: %s", e)
            return 0
