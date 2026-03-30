from pydantic import ConfigDict, field_validator, model_validator
from pydantic_settings import BaseSettings
import warnings
from typing import Optional
import os

_DEFAULT_GAMMA = "https://gamma-api.polymarket.com"
_DEFAULT_CLOB = "https://clob.polymarket.com"
_DEFAULT_DATA = "https://data-api.polymarket.com"
_DEFAULT_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _reject_empty_url(v: Optional[str], default: str) -> str:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return default
    return str(v).strip()


class Settings(BaseSettings):
    # Database Configuration - PostgreSQL
    # Empty when not set - avoids trying localhost (which fails with "password auth failed for user")
    DATABASE_URL: str = os.getenv("DATABASE_URL") or ""
    # Connection pool configuration
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "8"))  # S145: 12→8 (PgBouncer transaction mode; 3×12=36 < pgb 40)
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "4"))  # S145: 2→4 (burst headroom)
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "15"))  # S145: 30→15 (fail fast)
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "300"))  # S145: 600→300 (5min recycle)
    # Ingestion tuning — caps how many DB connections Phase 1 can hold simultaneously
    INGEST_NUM_PROCESSORS: int = int(os.getenv("INGEST_NUM_PROCESSORS", "8"))
    INGEST_DB_BUDGET: int = int(os.getenv("INGEST_DB_BUDGET", "4"))
    # Optional: after bulk insert, run a quick COUNT/exists check and log if data not visible (debug/staging)
    VERIFY_SAVE_AFTER_INSERT: bool = os.getenv("VERIFY_SAVE_AFTER_INSERT", "false").lower() in ("true", "1", "yes")
    # Pre-insert validation: skip invalid market/price/trade rows instead of failing (optional)
    PRE_INSERT_VALIDATION: bool = os.getenv("PRE_INSERT_VALIDATION", "true").lower() in ("true", "1", "yes")

    # Bot identity (required for multi-bot; coordination uses this)
    BOT_ID: str = os.getenv("BOT_ID", "default")

    # Redis (optional - caching only; app runs without Redis when REDIS_ENABLED=false)
    REDIS_ENABLED: bool = os.getenv("REDIS_ENABLED", "true").lower() in ("true", "1", "yes")
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL") or None  # Overrides host/port/db when set (e.g. redis://:password@host:6379/0)
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD") or None  # Used only when REDIS_URL not set
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    
    # External API Keys (Optional - for signal ingestion)
    NEWSAPI_KEY: Optional[str] = os.getenv("NEWSAPI_KEY", None)
    TWITTER_BEARER_TOKEN: Optional[str] = os.getenv("TWITTER_BEARER_TOKEN", None)
    REDDIT_CLIENT_ID: Optional[str] = os.getenv("REDDIT_CLIENT_ID", None)
    REDDIT_CLIENT_SECRET: Optional[str] = os.getenv("REDDIT_CLIENT_SECRET", None)
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY", None)
    SENTRY_DSN: Optional[str] = os.getenv("SENTRY_DSN") or None

    # Polygon RPC (QuickNode primary; alert when rate limit approached)
    POLYGON_RPC: Optional[str] = os.getenv("POLYGON_RPC") or os.getenv("QUICKNODE_HTTP")
    QUICKNODE_HTTP: Optional[str] = os.getenv("QUICKNODE_HTTP")
    ALCHEMY_HTTP: Optional[str] = os.getenv("ALCHEMY_HTTP")
    BLASTAPI_HTTP: Optional[str] = os.getenv("BLASTAPI_HTTP")

    # UMA Optimistic Oracle V3 (Polygon) - ProposePrice monitoring for ~2h resolution lead
    UMA_PROPOSAL_MONITOR_ENABLED: bool = os.getenv("UMA_PROPOSAL_MONITOR_ENABLED", "false").lower() in ("true", "1", "yes")
    UMA_OO_V3_POLYGON: Optional[str] = os.getenv("UMA_OO_V3_POLYGON") or None

    # Polymarket API (empty env overrides are replaced with these defaults)
    POLYMARKET_GAMMA_API: str = _DEFAULT_GAMMA
    POLYMARKET_CLOB_API: str = _DEFAULT_CLOB
    POLYMARKET_DATA_API: str = _DEFAULT_DATA
    POLYMARKET_WS: str = _DEFAULT_WS

    @field_validator("POLYMARKET_GAMMA_API", "POLYMARKET_CLOB_API", "POLYMARKET_DATA_API", "POLYMARKET_WS", mode="before")
    @classmethod
    def reject_empty_polymarket_urls(cls, v: Optional[str], info) -> str:
        defaults = {"POLYMARKET_GAMMA_API": _DEFAULT_GAMMA, "POLYMARKET_CLOB_API": _DEFAULT_CLOB, "POLYMARKET_DATA_API": _DEFAULT_DATA, "POLYMARKET_WS": _DEFAULT_WS}
        return _reject_empty_url(v, defaults[info.field_name])
    
    # SECURITY FIX: Validation for sensitive settings
    
    @field_validator("PRIVATE_KEY", mode="before")
    @classmethod
    def validate_private_key(cls, v: Optional[str]) -> Optional[str]:
        """Validate private key format and security"""
        if v is None or not v.strip():
            return None
        
        key = v.strip()
        # Validate private key format
        if not key.startswith("0x"):
            key = "0x" + key
        
        if len(key) != 66:  # 0x + 64 hex characters
            raise ValueError("Private key must be 64 hex characters (with or without 0x prefix)")
        
        # Validate hex format
        try:
            int(key, 16)
        except ValueError:
            raise ValueError("Private key must contain only hexadecimal characters")
        
        return key
    
    @field_validator("MAX_POSITION_SIZE_PCT", "MAX_DAILY_EXPOSURE", "RISK_PER_TRADE_PCT", mode="after")
    @classmethod
    def validate_percentage_ranges(cls, v: float) -> float:
        """Validate percentage values are in safe ranges"""
        if not 0.0 < v <= 1.0:
            raise ValueError("Percentage values must be between 0.0 and 1.0")
        return v
    
    @field_validator("TOTAL_CAPITAL", "MAX_DAILY_LOSS", "MAX_WEEKLY_LOSS", "MAX_MONTHLY_LOSS", mode="after")
    @classmethod
    def validate_positive_amounts(cls, v: float) -> float:
        """Validate financial amounts are positive"""
        if v <= 0:
            raise ValueError("Financial amounts must be positive")
        return v
    
    @field_validator("RATE_LIMIT_REQUESTS_PER_SECOND", "RATE_LIMIT_BURST", mode="after")
    @classmethod
    def validate_rate_limits(cls, v: int) -> int:
        """Validate rate limiting parameters"""
        if v <= 0:
            raise ValueError("Rate limit values must be positive integers")
        if v > 1000:  # Safety limit
            raise ValueError("Rate limit values seem too high (>1000) - check configuration")
        return v

    # Rate Limiting (Maximum Settings)
    RATE_LIMIT_REQUESTS_PER_SECOND: int = 100
    RATE_LIMIT_BURST: int = 200
    RATE_LIMIT_WINDOW_SECONDS: int = 1
    
    # Caching (Maximum Performance)
    CACHE_TTL_MARKETS: int = 60
    CACHE_TTL_PREDICTIONS: int = int(os.getenv("CACHE_TTL_PREDICTIONS", "60"))
    CACHE_TTL_LEARNING: int = 1800
    # Feature vector cache invalidation on large price move (Phase 1.4)
    FV_CACHE_INVALIDATE_PRICE_MOVE: float = float(os.getenv("FV_CACHE_INVALIDATE_PRICE_MOVE", "0.03"))
    # Extremization factor: push ensemble predictions away from 0.5 via log-odds scaling.
    # 1.4 is the AIA/Pythia consensus value. 0.0 = disabled (old default), 1.4 = active.
    EXTREMIZATION_FACTOR: float = float(os.getenv("EXTREMIZATION_FACTOR", "1.8"))
    # Platt scaling (§9.3): shrink predictions toward 0.5 when rolling Brier > 0.15
    PLATT_SCALING_ENABLED: bool = os.getenv("PLATT_SCALING_ENABLED", "false").lower() in ("true", "1", "yes")
    PLATT_MIN_RESOLVED: int = int(os.getenv("PLATT_MIN_RESOLVED", "200"))
    # Pseudo-label from paper trade P&L: was_correct = (avg_pnl > 0).
    # Disabled by default — this label is misleading when all paper trades lose money
    # (e.g. avg_pnl < 0 → was_correct=FALSE even when the directional prediction was correct).
    # Real market-resolution labels (Location 1 in database.py) are used instead.
    PSEUDO_LABEL_ENABLED: bool = os.getenv("PSEUDO_LABEL_ENABLED", "false").lower() in ("true", "1", "yes")

    # Trading Settings — percentage-based guardrails (used by risk_manager + dashboard)
    MAX_POSITIONS_PER_BOT: int = 50
    MAX_DAILY_EXPOSURE: float = 1.0
    MIN_CONFIDENCE_THRESHOLD: float = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.45"))  # Session 47: was 0.55
    MAX_POSITION_SIZE_PCT: float = 0.10
    TOTAL_CAPITAL: float = float(os.getenv("TOTAL_CAPITAL", "20000.0"))
    RISK_PER_TRADE_PCT: float = 1.0
    MAX_DAILY_LOSS: float = float(os.getenv("MAX_DAILY_LOSS", "10000.0"))
    MAX_WEEKLY_LOSS: float = float(os.getenv("MAX_WEEKLY_LOSS", "25000.0"))
    MAX_MONTHLY_LOSS: float = float(os.getenv("MAX_MONTHLY_LOSS", "50000.0"))

    # Risk guardrails — USD hard limits (Issue #19, used by risk_manager loss-limit checks)
    # These are COMPLEMENTARY to the percentage settings above:
    #   Percentage settings = proportional guardrails (scale with capital)
    #   USD settings = absolute hard caps (prevent catastrophic loss regardless of capital)
    RISK_MAX_POSITION_SIZE_USD: float = float(os.getenv("RISK_MAX_POSITION_SIZE_USD", "1000"))
    RISK_MAX_TOTAL_EXPOSURE_USD: float = float(os.getenv("RISK_MAX_TOTAL_EXPOSURE_USD", "20000"))
    RISK_MAX_POSITIONS_COUNT: int = int(os.getenv("RISK_MAX_POSITIONS_COUNT", "50"))
    RISK_MAX_DAILY_LOSS_USD: float = float(os.getenv("RISK_MAX_DAILY_LOSS_USD", "10000"))  # S105: aligned to $10K (matching per-bot max_daily_usd)
    RISK_MAX_WEEKLY_LOSS_USD: float = float(os.getenv("RISK_MAX_WEEKLY_LOSS_USD", "25000"))  # S105: 2.5x daily
    RISK_MAX_DRAWDOWN_PCT: float = float(os.getenv("RISK_MAX_DRAWDOWN_PCT", "20"))
    RISK_MAX_PORTFOLIO_CVAR_USD: float = float(os.getenv("RISK_MAX_PORTFOLIO_CVAR_USD", "10000"))  # CVaR tail-risk cap (S120: 5000→10000)
    RISK_MIN_EDGE_PCT: float = float(os.getenv("RISK_MIN_EDGE_PCT", "2"))
    RISK_MAX_PRICE: float = float(os.getenv("RISK_MAX_PRICE", "0.95"))
    RISK_MIN_PRICE: float = float(os.getenv("RISK_MIN_PRICE", "0.05"))
    # Ensemble edge filter: model_prob - market_price must exceed this minimum.
    # Raised from 5% to 10% — Polymarket 625bps taker fee eats ~3% round-trip at mid-price.
    ENSEMBLE_MIN_EDGE: float = float(os.getenv("ENSEMBLE_MIN_EDGE", "0.02"))  # Session 47: 2% net edge = ~5% raw after 3% round-trip costs
    # Category-specific min edge overrides per guardrail_settings.csv.
    # Higher for crypto/geopolitical (fees + corr + uncertainty), lower for weather (NOAA edge).
    ENSEMBLE_CATEGORY_MIN_EDGES: str = os.getenv(
        "ENSEMBLE_CATEGORY_MIN_EDGES",
        '{"weather":0.03,"crypto":0.05,"sports":0.04,"politics":0.04,"science":0.04,'
        '"finance":0.04,"geopolitical":0.05,"entertainment":0.04}'
    )  # Session 47: aligned with VPS tuned values (was 0.08-0.12)
    # Min 24h market volume to trade (zombie/thin market filter in ensemble decision gate)
    ENSEMBLE_MIN_MARKET_VOLUME_USD: float = float(os.getenv("ENSEMBLE_MIN_MARKET_VOLUME_USD", "5000.0"))
    # Side bias detection threshold — warn if >X% of last 50 trades are same side
    ENSEMBLE_SIDE_BIAS_THRESHOLD: float = float(os.getenv("ENSEMBLE_SIDE_BIAS_THRESHOLD", "0.75"))
    # Max bid-ask spread to trade into; if spread > this, reject (CLOB gate)
    ENSEMBLE_MAX_SPREAD_PCT: float = float(os.getenv("ENSEMBLE_MAX_SPREAD_PCT", "0.10"))
    # Min resolved predictions before periodic (non-forced) retrain runs
    MIN_RESOLVED_FOR_RETRAIN: int = int(os.getenv("MIN_RESOLVED_FOR_RETRAIN", "20"))
    # Min combined volume+liquidity for a market to be saved during ingestion
    MIN_MARKET_VOLUME: float = float(os.getenv("MIN_MARKET_VOLUME", "100.0"))
    # DEPRECATED: Per-bot sizing now handled by BotBankrollManager (Session 47).
    # Kept for backward compatibility with bots not yet migrated to BotBankrollManager.
    KELLY_ACTIVE_BOTS: int = int(os.getenv("KELLY_ACTIVE_BOTS", "3"))  # Session 47: was 10 — only 3 bots enabled
    # Per-bot bankroll config (Session 47). JSON dict keyed by bot_name.
    # Each bot gets independent capital, Kelly fraction, and daily/per-trade caps.
    # Empty {} = use built-in defaults from BotBankrollManager._load_bot_config()
    BOT_BANKROLL_CONFIG: str = os.getenv("BOT_BANKROLL_CONFIG", "{}")
    # Max consecutive losing closed trades before pausing a bot. 0 = disabled.
    # Paper phase: 0 (disabled); Learning phase: 3; Graduated: 4; Production: 5
    MAX_CONSECUTIVE_LOSSES: int = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "0"))
    # PortfolioDrawdownBreaker daily loss circuit threshold (fraction of day-open equity).
    # Paper phase: 0.05 (5%); Learning: 0.02 (2%); Graduated: 0.025; Production: 0.03
    DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.05"))
    # Health check interval (minutes). HealthRunner fires this often inside IngestionScheduler.
    HEALTH_CHECK_INTERVAL_MINUTES: int = int(os.getenv("HEALTH_CHECK_INTERVAL_MINUTES", "60"))
    HEALTH_PORT: int = int(os.getenv("HEALTH_PORT", "8765"))  # per-service port; override in .env.{bot}
    # Mini backfill interval (minutes). prediction_log + pseudo-label labeling between daily runs.
    MINI_BACKFILL_INTERVAL_MINUTES: int = int(os.getenv("MINI_BACKFILL_INTERVAL_MINUTES", "15"))  # S92: 30→15 for faster resolution clearing
    # Transaction cost model (for dynamic edge threshold)
    TAKER_FEE_BPS: int = int(os.getenv("TAKER_FEE_BPS", "150"))  # 1.5% — used by exit strategy, position manager edge calcs
    MAKER_FEE_BPS: int = int(os.getenv("MAKER_FEE_BPS", "0"))
    PAPER_TAKER_FEE_BPS: int = int(os.getenv("PAPER_TAKER_FEE_BPS", "150"))  # S120: Default 150 bps (1.5%) for realistic paper P&L. Override per market: 0 for weather, 156 for 15-min crypto.
    GAS_COST_USD: float = float(os.getenv("GAS_COST_USD", "0.01"))
    FIXED_SLIPPAGE_BPS: int = int(os.getenv("FIXED_SLIPPAGE_BPS", "0"))  # 0=use tiered model; >0=flat override
    # S91: Realistic paper fill modeling — fill probability, partial fills, latency drift
    PAPER_REALISTIC_FILLS: bool = os.getenv("PAPER_REALISTIC_FILLS", "true").lower() in ("true", "1", "yes")
    PAPER_DEFAULT_SPREAD: float = float(os.getenv("PAPER_DEFAULT_SPREAD", "0.04"))  # 4% when bid/ask unavailable
    PAPER_LATENCY_DRIFT_BPS_PER_SEC: int = int(os.getenv("PAPER_LATENCY_DRIFT_BPS_PER_SEC", "0"))  # S121: disabled by default. Models adverse price movement during execution delay. Set to 10 to enable (~0.1%/sec).
    # S121: Live order retry settings (only active when SIMULATION_MODE=false)
    LIVE_ORDER_MAX_RETRIES: int = int(os.getenv("LIVE_ORDER_MAX_RETRIES", "3"))
    LIVE_ORDER_RETRY_BASE_S: float = float(os.getenv("LIVE_ORDER_RETRY_BASE_S", "1.0"))
    # S95: Paper trading realism elevations
    PAPER_KYLE_LAMBDA_ENABLED: bool = os.getenv("PAPER_KYLE_LAMBDA_ENABLED", "true").lower() in ("true", "1", "yes")
    PAPER_CROSS_SCAN_IMPACT_ENABLED: bool = os.getenv("PAPER_CROSS_SCAN_IMPACT_ENABLED", "true").lower() in ("true", "1", "yes")
    PAPER_ALPHA_DECAY_HALF_LIFE_S: float = float(os.getenv("PAPER_ALPHA_DECAY_HALF_LIFE_S", "300"))  # 5 min half-life
    PAPER_RESOLUTION_PROXIMITY_ENABLED: bool = os.getenv("PAPER_RESOLUTION_PROXIMITY_ENABLED", "true").lower() in ("true", "1", "yes")
    # S100: L2 book walk — use real order book depth for fill simulation (replaces heuristic slippage)
    # S106: Enabled by default. Already ON in production via .env override; codifying as default.
    PAPER_BOOK_WALK_ENABLED: bool = os.getenv("PAPER_BOOK_WALK_ENABLED", "true").lower() in ("true", "1", "yes")
    # S105: Taker-side filter — soft 0.5x fill penalty when taker side matches order side.
    # S106: Enabled by default. Shadow trading guide: "the single most important implementation detail".
    PAPER_TAKER_SIDE_FILTER: bool = os.getenv("PAPER_TAKER_SIDE_FILTER", "true").lower() in ("true", "1", "yes")
    # S105b: Flat taker-side discount when no taker_side data is available in event_data.
    # S107: Raised from 0.55→0.85. 0.55 modeled resting limit orders (45% same-side taker
    # chance). All bots are taker-style in paper trading — 0.85 reflects only queue/timing risk.
    PAPER_TAKER_SIDE_FACTOR: float = float(os.getenv("PAPER_TAKER_SIDE_FACTOR", "0.85"))
    # S106: Slippage-eats-edge rejection — reject trade when estimated slippage exceeds the edge.
    # Ported from WeatherBot's liquidity_guardian pattern. Applies to ALL bots.
    PAPER_SLIPPAGE_EDGE_CHECK: bool = os.getenv("PAPER_SLIPPAGE_EDGE_CHECK", "true").lower() in ("true", "1", "yes")
    # S106: Fill-failure cooldown — back off a market after consecutive fill rejections.
    PAPER_FILL_FAILURE_COOLDOWN_S: int = int(os.getenv("PAPER_FILL_FAILURE_COOLDOWN_S", "300"))  # 5 min

    # Learning Settings
    # Per-bot model training (Session 47): when enabled, each bot trains on its own prediction_log entries.
    # Default OFF — all bots share the global ensemble model. Enable after 200+ per-bot predictions.
    USE_PER_BOT_MODELS: bool = os.getenv("USE_PER_BOT_MODELS", "false").lower() in ("true", "1", "yes")
    LEARNING_PERSISTENCE: bool = os.getenv("LEARNING_PERSISTENCE", "true").lower() in ("true", "1", "yes")
    RETRAIN_INTERVAL_HOURS: int = int(os.getenv("RETRAIN_INTERVAL_HOURS", "6"))
    LEARNING_UPDATE_INTERVAL_SECONDS: int = 300
    USE_RESOLUTION_LABEL: bool = os.getenv("USE_RESOLUTION_LABEL", "true").lower() in ("true", "1", "yes")
    USE_PATH_SUMMARY: bool = os.getenv("USE_PATH_SUMMARY", "true").lower() in ("true", "1", "yes")
    USE_REGIME_FEATURES: bool = os.getenv("USE_REGIME_FEATURES", "true").lower() in ("true", "1", "yes")
    PATH_SUMMARY_MAX_ROWS: int = int(os.getenv("PATH_SUMMARY_MAX_ROWS", "50000"))
    SIMULATION_ITERATIONS: int = 100000
    BACKTEST_LOOKBACK_DAYS: int = 365
    USE_PRICE_HISTORY_TRAINING_FALLBACK: bool = os.getenv("USE_PRICE_HISTORY_TRAINING_FALLBACK", "true").lower() in ("true", "1", "yes")
    # Refuse to train/deploy models with fewer than N samples (staleness check is different — protects against junk models)
    MODEL_MIN_TRAINING_SAMPLES: int = int(os.getenv("MODEL_MIN_TRAINING_SAMPLES", "50"))
    # Autonomous learning: retrain when recent performance degrades (AUTO_RETRAIN_ON_DEGRADATION)
    # L4 FIX: Default true — system should self-heal on degradation (cooldown prevents runaway retraining)
    AUTO_RETRAIN_ON_DEGRADATION: bool = os.getenv("AUTO_RETRAIN_ON_DEGRADATION", "true").lower() in ("true", "1", "yes")
    AUTO_RETRAIN_BRIER_MAX: float = float(os.getenv("AUTO_RETRAIN_BRIER_MAX", "0.30"))
    AUTO_RETRAIN_ACC_MIN: float = float(os.getenv("AUTO_RETRAIN_ACC_MIN", "0.45"))
    AUTO_RETRAIN_COOLDOWN_HOURS: float = float(os.getenv("AUTO_RETRAIN_COOLDOWN_HOURS", "1.0"))
    AUTO_RETRAIN_RECENT_N: int = int(os.getenv("AUTO_RETRAIN_RECENT_N", "50"))
    AUTO_RETRAIN_MIN_SAMPLES: int = int(os.getenv("AUTO_RETRAIN_MIN_SAMPLES", "20"))
    # Incremental learner: batch size before triggering full retrain (C)
    INCREMENTAL_LEARNER_BATCH_SIZE: int = int(os.getenv("INCREMENTAL_LEARNER_BATCH_SIZE", "100"))
    # Wrap models in CalibratedClassifierCV (isotonic) for better probability calibration
    # Phase 0: Default true — wraps all non-CatBoost models with isotonic calibration.
    # CatBoost intentionally excluded (incompatible __sklearn_tags__).
    USE_CALIBRATED_MODELS: bool = os.getenv("USE_CALIBRATED_MODELS", "true").lower() in ("true", "1", "yes")
    # Individual model toggles — disable without redeploying (all 8 ensemble models)
    MODEL_ENABLE_RANDOM_FOREST: bool = os.getenv("MODEL_ENABLE_RANDOM_FOREST", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_XGBOOST: bool = os.getenv("MODEL_ENABLE_XGBOOST", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_GRADIENT_BOOSTING: bool = os.getenv("MODEL_ENABLE_GRADIENT_BOOSTING", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_LOGISTIC_REGRESSION: bool = os.getenv("MODEL_ENABLE_LOGISTIC_REGRESSION", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_EXTRA_TREES: bool = os.getenv("MODEL_ENABLE_EXTRA_TREES", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_HIST_GRADIENT_BOOSTING: bool = os.getenv("MODEL_ENABLE_HIST_GRADIENT_BOOSTING", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_LIGHTGBM: bool = os.getenv("MODEL_ENABLE_LIGHTGBM", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_CATBOOST: bool = os.getenv("MODEL_ENABLE_CATBOOST", "true").lower() in ("true", "1", "yes")
    # GAP-4: Non-tree diversity models (RidgeClassifier = linear, KNN = instance-based)
    MODEL_ENABLE_RIDGE: bool = os.getenv("MODEL_ENABLE_RIDGE", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_KNN: bool = os.getenv("MODEL_ENABLE_KNN", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_MLP: bool = os.getenv("MODEL_ENABLE_MLP", "true").lower() in ("true", "1", "yes")
    MODEL_ENABLE_TABPFN: bool = os.getenv("MODEL_ENABLE_TABPFN", "true").lower() in ("true", "1", "yes")
    # RL Trade Timing Agent (learns WHEN to trade from paper trade outcomes)
    RL_TRADE_TIMING_ENABLED: bool = os.getenv("RL_TRADE_TIMING_ENABLED", "false").lower() in ("true", "1", "yes")
    RL_LEARNING_RATE: float = float(os.getenv("RL_LEARNING_RATE", "0.1"))
    RL_DISCOUNT_FACTOR: float = float(os.getenv("RL_DISCOUNT_FACTOR", "0.95"))
    RL_EPSILON_START: float = float(os.getenv("RL_EPSILON_START", "0.3"))
    RL_EPSILON_MIN: float = float(os.getenv("RL_EPSILON_MIN", "0.05"))
    RL_EPSILON_DECAY_TRADES: int = int(os.getenv("RL_EPSILON_DECAY_TRADES", "500"))
    RL_REPLAY_BUFFER_SIZE: int = int(os.getenv("RL_REPLAY_BUFFER_SIZE", "2000"))
    RL_REPLAY_BATCH_SIZE: int = int(os.getenv("RL_REPLAY_BATCH_SIZE", "32"))
    # Max unique (market, token) pairs to fetch price history for during training.
    # Session 50: was hardcoded 500, now configurable. Higher = more rows get real
    # path/regime/FE features instead of zeros. Cost: more DB reads at retrain.
    TRAINING_MAX_PRICE_KEYS: int = int(os.getenv("TRAINING_MAX_PRICE_KEYS", "2000"))
    # Exclude low-volume markets from training (reduces thin-market noise bias)
    TRAINING_MIN_VOLUME: float = float(os.getenv("TRAINING_MIN_VOLUME", "500"))
    TRAINING_PURGE_DAYS: float = float(os.getenv("TRAINING_PURGE_DAYS", "0"))
    TRAINING_EMBARGO_DAYS: float = float(os.getenv("TRAINING_EMBARGO_DAYS", "0"))
    # Training feedback loop: learn from paper trades and prediction outcomes
    TRAIN_ON_PAPER_TRADES: bool = os.getenv("TRAIN_ON_PAPER_TRADES", "true").lower() in ("true", "1", "yes")
    PAPER_TRADE_TRAINING_WEIGHT: float = float(os.getenv("PAPER_TRADE_TRAINING_WEIGHT", "0.5"))
    PAPER_TRADE_TRAINING_MAX_ROWS: int = int(os.getenv("PAPER_TRADE_TRAINING_MAX_ROWS", "5000"))
    TRAIN_ON_PREDICTION_LOG: bool = os.getenv("TRAIN_ON_PREDICTION_LOG", "true").lower() in ("true", "1", "yes")
    PREDICTION_LOG_TRAINING_WEIGHT: float = float(os.getenv("PREDICTION_LOG_TRAINING_WEIGHT", "0.3"))
    PREDICTION_LOG_TRAINING_MAX_ROWS: int = int(os.getenv("PREDICTION_LOG_TRAINING_MAX_ROWS", "10000"))
    RETRAIN_ON_NEW_FEEDBACK: bool = os.getenv("RETRAIN_ON_NEW_FEEDBACK", "true").lower() in ("true", "1", "yes")
    # Ensemble blend: weight of ML ensemble vs learning_conf in final prediction.
    # Session 50: was 0.6 (40% learning_conf ≈ 0.5 diluted all predictions toward coin-flip).
    # Set to 1.0 to use pure ensemble until learning_conf has real signal from resolved outcomes.
    ENSEMBLE_BLEND: float = float(os.getenv("ENSEMBLE_BLEND", "1.0"))
    # Model rollback: max Brier score degradation allowed before rejecting new models (lower = stricter)
    MODEL_ROLLBACK_BRIER_TOLERANCE: float = float(os.getenv("MODEL_ROLLBACK_BRIER_TOLERANCE", "0.02"))
    # Alpha decay: exponential decay rate for stale predictions (higher = faster decay)
    # At lambda=0.5: 1h old = 60% confidence, 2h = 37%, 4h = 13%
    ALPHA_DECAY_LAMBDA: float = float(os.getenv("ALPHA_DECAY_LAMBDA", "0.5"))
    BACKTEST_PREFER_PRICE_HISTORY: bool = os.getenv("BACKTEST_PREFER_PRICE_HISTORY", "false").lower() in ("true", "1", "yes")
    
    # Elite Trader Settings - Top 200 traders
    TOP_TRADER_COUNT: int = 300  # S142: 500→300 (top 0.04% capture 70% of profits; reduce noise)
    # Elite thresholds (relaxed: 5 bets in last year, 55% win; high vol+return weighted higher)
    ELITE_MIN_TRADES: int = int(os.getenv("ELITE_MIN_TRADES", "100"))  # 5→100: minimum trades to qualify as elite
    ELITE_MIN_VOLUME_USD: float = float(os.getenv("ELITE_MIN_VOLUME_USD", "10000"))  # OR $10k volume — either proves activity
    ELITE_MIN_WIN_RATE: float = float(os.getenv("ELITE_MIN_WIN_RATE", "0.55"))
    ELITE_MIN_PROFIT_USD: float = float(os.getenv("ELITE_MIN_PROFIT_USD", "0"))
    ELITE_LOOKBACK_DAYS: int = int(os.getenv("ELITE_LOOKBACK_DAYS", "365"))
    # Learning weights: elite base multiplier, extra for high-volume+high-return
    ELITE_LEARNING_WEIGHT: float = float(os.getenv("ELITE_LEARNING_WEIGHT", "1.35"))
    ELITE_HIGH_VOL_RETURN_WEIGHT: float = float(os.getenv("ELITE_HIGH_VOL_RETURN_WEIGHT", "1.55"))
    # Cap per-market elite signal at N addresses (Sybil mitigation)
    ELITE_MAX_ADDRESSES_PER_MARKET_SIGNAL: int = int(os.getenv("ELITE_MAX_ADDRESSES_PER_MARKET_SIGNAL", "5"))
    # Near-elite: expand pool when elite data is sparse (lower thresholds for promising users)
    NEAR_ELITE_ENABLED: bool = os.getenv("NEAR_ELITE_ENABLED", "true").lower() in ("true", "1", "yes")
    NEAR_ELITE_MIN_TRADES: int = int(os.getenv("NEAR_ELITE_MIN_TRADES", "30"))
    NEAR_ELITE_MIN_WIN_RATE: float = float(os.getenv("NEAR_ELITE_MIN_WIN_RATE", "0.45"))
    # Market-maker heuristic: users trading both sides on >60% of markets
    ELITE_MARKET_MAKER_BOTH_SIDES_RATIO: float = float(os.getenv("ELITE_MARKET_MAKER_BOTH_SIDES_RATIO", "0.6"))
    SOFTEST_MARKETS_COUNT: int = 25
    # MirrorBot — RTDS real-time copy trading (S96+)
    MIRROR_MIN_CONFIDENCE: float = float(os.getenv("MIRROR_MIN_CONFIDENCE", "0.55"))  # S142: 0.50→0.55 (0.50 is breakeven after fees)
    MIRROR_MAX_PER_MARKET: float = float(os.getenv("MIRROR_MAX_PER_MARKET", "500"))
    MIRROR_MAX_PER_MARKET_PCT: float = float(os.getenv("MIRROR_MAX_PER_MARKET_PCT", "0.10"))
    MIRROR_MAX_CATEGORY_EXPOSURE_USD: float = float(os.getenv("MIRROR_MAX_CATEGORY_EXPOSURE_USD", "40000"))
    MIRROR_MAX_TRACKED_TRADES: int = int(os.getenv("MIRROR_MAX_TRACKED_TRADES", "10000"))
    MIRROR_EXIT_ENABLED: bool = os.getenv("MIRROR_EXIT_ENABLED", "true").lower() in ("true", "1", "yes")
    MIRROR_MAX_CONCURRENT_POSITIONS: int = int(os.getenv("MIRROR_MAX_CONCURRENT_POSITIONS", "600"))
    MIRROR_MAX_DAILY_EXPOSURE_PCT: float = float(os.getenv("MIRROR_MAX_DAILY_EXPOSURE_PCT", "0.15"))  # deprecated fallback, tests use
    MIRROR_MIN_RELIABILITY: float = float(os.getenv("MIRROR_MIN_RELIABILITY", "0.52"))
    MIRROR_MIN_ELITE_TRADES: int = int(os.getenv("MIRROR_MIN_ELITE_TRADES", "100"))
    # Watchlist: real-time WebSocket copy trading (monthly leaderboard top 1k)
    WATCHLIST_ENABLED: bool = os.getenv("WATCHLIST_ENABLED", "false").lower() in ("true", "1", "yes")
    WATCHLIST_SIZE: int = int(os.getenv("WATCHLIST_SIZE", "300"))  # S142: 500→300 (aligned with TOP_TRADER_COUNT)
    WHALE_TRADE_LOG_ENABLED: bool = os.getenv("WHALE_TRADE_LOG_ENABLED", "true").lower() in ("true", "1", "yes")
    # RTDS: global trade feed
    RTDS_WS_URL: str = os.getenv("RTDS_WS_URL", "wss://ws-live-data.polymarket.com")
    RTDS_PING_INTERVAL: int = int(os.getenv("RTDS_PING_INTERVAL", "5"))
    RTDS_RECV_TIMEOUT: int = int(os.getenv("RTDS_RECV_TIMEOUT", "25"))  # S137 C15: was 120s

    # Calibration + safety feature flags (all off by default)
    MIRROR_USE_CALIBRATION: bool = os.getenv("MIRROR_USE_CALIBRATION", "false").lower() in ("true", "1", "yes")
    MIRROR_ADAPTIVE_SAFETY: bool = os.getenv("MIRROR_ADAPTIVE_SAFETY", "true").lower() in ("true", "1", "yes")  # S137 C4: enabled (BUG-14 denominator fixed)
    MIRROR_SKIP_LIQUIDITY_RTDS: bool = os.getenv("MIRROR_SKIP_LIQUIDITY_RTDS", "true").lower() in ("true", "1", "yes")
    MIRROR_SKIP_COORDINATOR_BUY: bool = os.getenv("MIRROR_SKIP_COORDINATOR_BUY", "true").lower() in ("true", "1", "yes")
    MIRROR_RTDS_FAST_PATH: bool = os.getenv("MIRROR_RTDS_FAST_PATH", "true").lower() in ("true", "1", "yes")
    # S124: ML trade selector (three-way shadow race: XGBoost / Q-learning / combo)
    MIRROR_USE_ML_SELECTOR: bool = os.getenv("MIRROR_USE_ML_SELECTOR", "false").lower() in ("true", "1", "yes")
    MIRROR_ML_STRATEGY: str = os.getenv("MIRROR_ML_STRATEGY", "xgb")  # xgb | ql | combo
    MIRROR_ML_MIN_SCORE: float = float(os.getenv("MIRROR_ML_MIN_SCORE", "0.45"))
    MIRROR_ML_MODEL_PATH: str = os.getenv("MIRROR_ML_MODEL_PATH", "models/mirror_ml_selector.pkl")
    MIRROR_ML_QTABLE_PATH: str = os.getenv("MIRROR_ML_QTABLE_PATH", "models/mirror_ml_qtable.pkl")
    MIRROR_ML_MAX_AGE_DAYS: int = int(os.getenv("MIRROR_ML_MAX_AGE_DAYS", "14"))

    # Exit logic
    MIRROR_STOP_LOSS_PCT: float = float(os.getenv("MIRROR_STOP_LOSS_PCT", "0.15"))
    # S137 C10: Reversed stop-loss graduation — tight early (kill losers fast), loose late (near-res noise)
    # Was: -15%→-10%→-5% (backwards — tightest at end). Now: -10%→-12%→-15% (tightest at start).
    MIRROR_STOP_LOSS_TIGHTEN_24H: float = float(os.getenv("MIRROR_STOP_LOSS_TIGHTEN_24H", "-0.06"))  # S146: 0-24h tightest
    MIRROR_STOP_LOSS_TIGHTEN_48H: float = float(os.getenv("MIRROR_STOP_LOSS_TIGHTEN_48H", "-0.12"))
    MIRROR_STOP_LOSS_TIGHTEN_72H: float = float(os.getenv("MIRROR_STOP_LOSS_TIGHTEN_72H", "-0.15"))
    # S137 C10: Near-resolution tightener — if < 24h to resolution, tighten to -5%
    MIRROR_STOP_LOSS_NEAR_RES_HOURS: float = float(os.getenv("MIRROR_STOP_LOSS_NEAR_RES_HOURS", "24.0"))
    MIRROR_STOP_LOSS_NEAR_RES_PCT: float = float(os.getenv("MIRROR_STOP_LOSS_NEAR_RES_PCT", "-0.05"))
    # S137 C11: Resolution-relative max-hold — exit after >80% of total market duration
    MIRROR_MAX_HOLD_FRACTION: float = float(os.getenv("MIRROR_MAX_HOLD_FRACTION", "0.80"))
    MIRROR_MAX_POSITIONS: int = int(os.getenv("MIRROR_MAX_POSITIONS", "1000"))
    MIRROR_TOTAL_CAPITAL: float = float(os.getenv("MIRROR_TOTAL_CAPITAL", "20000"))
    # Tier 0 pre-trade filters
    MIRROR_CATEGORY_BLOCKLIST: str = os.getenv("MIRROR_CATEGORY_BLOCKLIST", "crypto,15-minute,speed")
    MIRROR_MARKET_COOLDOWN_SECONDS: int = int(os.getenv("MIRROR_MARKET_COOLDOWN_SECONDS", "1800"))
    MIRROR_MIN_TRADE_USD: float = float(os.getenv("MIRROR_MIN_TRADE_USD", "50.0"))
    MIRROR_MAX_SLIPPAGE_PCT: float = float(os.getenv("MIRROR_MAX_SLIPPAGE_PCT", "0.05"))  # S142: 0.08→0.05 (8% slippage consumed edge on 3-5¢ signal)
    # S132: Minimum whale trade USD — sub-$50 trades are noise (39.9% WR, -$153K)
    MIRROR_MIN_WHALE_TRADE_USD: float = float(os.getenv("MIRROR_MIN_WHALE_TRADE_USD", "50.0"))
    # S132: NO-side dampener — NO loses 7x more than YES. 0.5 = half size on NO.
    MIRROR_NO_SIDE_DAMPENER: float = float(os.getenv("MIRROR_NO_SIDE_DAMPENER", "0.3"))  # S137 C5: 0.5→0.3 (NO = -$139K, 87% of losses)
    MIRROR_NO_PRICE_BLOCK: float = float(os.getenv("MIRROR_NO_PRICE_BLOCK", "0.75"))    # S137 C5: hard block NO when token price >75%
    MIRROR_NO_BLOCK_FLOOR: float = float(os.getenv("MIRROR_NO_BLOCK_FLOOR", "0.20"))   # S146: hard block NO when token price < floor (was 0.10)
    MIRROR_NO_MIN_EDGE: float = float(os.getenv("MIRROR_NO_MIN_EDGE", "0.05"))         # S146: NO must show >=5% edge (confidence - price)
    # S137 C8: Market volume gate — thin markets have poor execution quality
    MIRROR_MIN_MARKET_VOLUME_24H: float = float(os.getenv("MIRROR_MIN_MARKET_VOLUME_24H", "5000.0"))
    # S137 C9: Category expertise filter — reject traders with poor category-specific WR
    MIRROR_CAT_MIN_TRADES: int = int(os.getenv("MIRROR_CAT_MIN_TRADES", "10"))
    MIRROR_CAT_MIN_WIN_RATE: float = float(os.getenv("MIRROR_CAT_MIN_WIN_RATE", "0.45"))
    # S133/S142: Spread gate — 20c+ spread = entering 20% underwater. Tightened to 8c.
    MIRROR_MAX_SPREAD: float = float(os.getenv("MIRROR_MAX_SPREAD", "0.08"))
    # S133: Per-trader P&L blacklist — auto-block traders with poor WR after enough data.
    # 76% of copied traders are unprofitable; top 3 worst = -$68K (43% of all losses).
    MIRROR_TRADER_MIN_WIN_RATE: float = float(os.getenv("MIRROR_TRADER_MIN_WIN_RATE", "0.35"))
    MIRROR_TRADER_MIN_RESOLVED: int = int(os.getenv("MIRROR_TRADER_MIN_RESOLVED", "20"))
    # S146: Copy-P&L tiered sizing — multiplier per trader based on OUR copy-profitability.
    # Tier 1 (copy-profitable, n>=threshold): 1.0x. Tier 2 (thin data): mult. Tier 3 (copy-unprofitable): mult.
    MIRROR_COPY_TIER2_MULT: float = float(os.getenv("MIRROR_COPY_TIER2_MULT", "0.50"))
    MIRROR_COPY_TIER3_MULT: float = float(os.getenv("MIRROR_COPY_TIER3_MULT", "0.25"))
    MIRROR_COPY_MIN_TRADES_FOR_TIER: int = int(os.getenv("MIRROR_COPY_MIN_TRADES_FOR_TIER", "20"))
    # Dampeners (S119: set to 1.0 = no-op for data collection phase)
    MIRROR_FAVORITE_PRICE_THRESHOLD: float = float(os.getenv("MIRROR_FAVORITE_PRICE_THRESHOLD", "0.70"))
    MIRROR_FAVORITE_DAMPENER: float = float(os.getenv("MIRROR_FAVORITE_DAMPENER", "1.0"))  # S119: 0.40→1.0 for data collection

    # Bot Settings
    BOT_SCAN_INTERVAL_SECONDS: int = int(os.getenv("BOT_SCAN_INTERVAL_SECONDS", "60"))
    DEFAULT_SCAN_INTERVAL: int = int(os.getenv("DEFAULT_SCAN_INTERVAL", "60"))
    # CASCADE_CHECK_ENABLED — defined in 2026 Alpha section below (default: true)
    # LLM_AB_TEST_PROMPTS — defined in 2026 Alpha section below (default: false)
    BOT_MAX_CONSECUTIVE_ERRORS: int = int(os.getenv("BOT_MAX_CONSECUTIVE_ERRORS", "10"))
    USE_SCAN_JITTER: bool = os.getenv("USE_SCAN_JITTER", "false").lower() in ("true", "1", "yes")
    SCAN_JITTER_PCT: float = float(os.getenv("SCAN_JITTER_PCT", "0.2"))
    # Scan intervals — authoritative definitions in 2026 Alpha section below
    ARB_MIN_NET_EDGE: float = float(os.getenv("ARB_MIN_NET_EDGE", "0.005"))
    ARB_MAX_PRICE_AGE_SECONDS: int = int(os.getenv("ARB_MAX_PRICE_AGE_SECONDS", "5"))
    # How long (seconds) to block EnsembleBot re-entry after any position exit.
    # Prevents churning the same market after model_reversal exits.
    ENSEMBLE_EXIT_COOLDOWN_SECONDS: int = int(os.getenv("ENSEMBLE_EXIT_COOLDOWN_SECONDS", "300"))  # Session 47: was 1800 (30 min) — 5 min base, doubles per exit, 1h cap

    # Position manager adaptive exit thresholds (applies to ALL bots except excluded)
    PM_EXCLUDE_BOTS: list = [b.strip() for b in os.getenv("PM_EXCLUDE_BOTS", "EsportsBot,MirrorBot,WeatherBot").split(",") if b.strip()]
    PM_STOP_LOSS_PCT: float = float(os.getenv("PM_STOP_LOSS_PCT", "0.30"))           # 30% — wide for prediction markets
    PM_TAKE_PROFIT_PCT: float = float(os.getenv("PM_TAKE_PROFIT_PCT", "0.60"))       # 60% — let winners run
    PM_ADAPTIVE_EXITS: bool = os.getenv("PM_ADAPTIVE_EXITS", "true").lower() in ("true", "1", "yes")
    PM_LEARNING_REFRESH_SECONDS: int = int(os.getenv("PM_LEARNING_REFRESH_SECONDS", "1800"))  # 30 min
    # Session 45: Intelligent Exit Engine — cost-aware, regime-adapted, TTR-decayed exits
    PM_COST_AWARE_EXITS: bool = os.getenv("PM_COST_AWARE_EXITS", "true").lower() in ("true", "1", "yes")  # Kill switch: false reverts to fixed thresholds
    PM_STRONG_REVERSAL_THRESHOLD: float = float(os.getenv("PM_STRONG_REVERSAL_THRESHOLD", "0.35"))  # Force exit below this prob
    PM_VOL_STOP_MULTIPLIER: float = float(os.getenv("PM_VOL_STOP_MULTIPLIER", "2.0"))  # ATR multiplier for vol-based stops
    PM_NEAR_RESOLUTION_HOURS: float = float(os.getenv("PM_NEAR_RESOLUTION_HOURS", "48.0"))  # Hold-to-resolution threshold
    PM_BASE_STOP_LOSS_PCT: float = float(os.getenv("PM_BASE_STOP_LOSS_PCT", "0.30"))  # Base stop before dynamic adjustment
    PM_BASE_TAKE_PROFIT_PCT: float = float(os.getenv("PM_BASE_TAKE_PROFIT_PCT", "0.60"))  # Base TP before dynamic adjustment
    USE_SIGNALS_IN_BOTS: bool = os.getenv("USE_SIGNALS_IN_BOTS", "true").lower() in ("true", "1", "yes")
    USE_ORDER_FLOW_IN_BOTS: bool = os.getenv("USE_ORDER_FLOW_IN_BOTS", "true").lower() in ("true", "1", "yes")
    USE_SIGNAL_FEATURES_IN_PREDICTION: bool = os.getenv("USE_SIGNAL_FEATURES_IN_PREDICTION", "true").lower() in ("true", "1", "yes")
    ENSEMBLE_MIN_CONFIDENCE: float = float(os.getenv("ENSEMBLE_MIN_CONFIDENCE", "0.45"))  # Session 47: was 0.55
    # I13: Model disagreement constants — single source of truth (was hardcoded in ensemble_bot.py)
    ENSEMBLE_DISAGREEMENT_THRESHOLD: float = float(os.getenv("ENSEMBLE_DISAGREEMENT_THRESHOLD", "0.20"))
    ENSEMBLE_DISAGREEMENT_PENALTY: float = float(os.getenv("ENSEMBLE_DISAGREEMENT_PENALTY", "0.15"))
    MIN_MARKET_LIQUIDITY: float = float(os.getenv("MIN_MARKET_LIQUIDITY", "100"))
    SCAN_MARKET_LIMIT: int = int(os.getenv("SCAN_MARKET_LIMIT", "1500"))  # S101b: raised from 800 — pagination found 1139 markets (114 events)
    USE_GOOGLE_TRENDS: bool = os.getenv("USE_GOOGLE_TRENDS", "true").lower() in ("true", "1", "yes")
    CALIBRATION_TRACKING_ENABLED: bool = os.getenv("CALIBRATION_TRACKING_ENABLED", "true").lower() in ("true", "1", "yes")
    PREDICTION_LOG_ENABLED: bool = os.getenv("PREDICTION_LOG_ENABLED", "true").lower() in ("true", "1", "yes")
    USE_ELITE_NET_DIRECTION: bool = os.getenv("USE_ELITE_NET_DIRECTION", "true").lower() in ("true", "1", "yes")
    # Compute user win_rate/profit as-of each trade's timestamp (LATERAL JOIN, point-in-time).
    # Fixes temporal leakage where training used cumulative lifetime stats (future user performance).
    # Set to false to revert to cumulative users table stats (faster training, higher leakage).
    USE_TEMPORAL_USER_STATS: bool = os.getenv("USE_TEMPORAL_USER_STATS", "true").lower() in ("true", "1", "yes")
    # Per-bot enable/disable — authoritative definitions in 2026 Alpha section below
    # Sentiment — authoritative definitions in 2026 Alpha section below
    # Prediction bots: use DB markets (ensures features exist) instead of API-only
    USE_DB_MARKETS_FOR_PREDICTION_BOTS: bool = os.getenv("USE_DB_MARKETS_FOR_PREDICTION_BOTS", "true").lower() in ("true", "1", "yes")
    # Kelly: use fractional Kelly when Brier below this threshold (0 = always use Kelly when enabled)
    # T3 FIX: Enable Kelly by default — sizes on edge (prediction - price) not just confidence
    USE_KELLY_SIZING: bool = os.getenv("USE_KELLY_SIZING", "true").lower() in ("true", "1", "yes")
    KELLY_FRACTION: float = float(os.getenv("KELLY_FRACTION", "0.25"))
    KELLY_MAX_BRIER: float = float(os.getenv("KELLY_MAX_BRIER", "0.20"))
    # Phase 7.3: Volatility-scaled sizing factor. At vol=0.5: size /= (1 + 0.5*2.0) = 2.0×; vol=1.0: 3.0×
    VOL_SCALE_FACTOR: float = float(os.getenv("VOL_SCALE_FACTOR", "2.0"))
    # Phase 6.1: Kalshi cross-platform lead signals
    KALSHI_SIGNAL_ENABLED: bool = os.getenv("KALSHI_SIGNAL_ENABLED", "true").lower() in ("true", "1", "yes")
    CROSS_PLATFORM_POLL_INTERVAL_S: int = int(os.getenv("CROSS_PLATFORM_POLL_INTERVAL_S", "10"))
    CROSS_PLATFORM_SIGNAL_THRESHOLD: float = float(os.getenv("CROSS_PLATFORM_SIGNAL_THRESHOLD", "0.03"))
    # Latency logging: log decision-to-order time in OrderGateway
    LOG_ORDER_LATENCY: bool = os.getenv("LOG_ORDER_LATENCY", "true").lower() in ("true", "1", "yes")
    ORDER_LATENCY_ALERT_MS: int = int(os.getenv("ORDER_LATENCY_ALERT_MS", "5000"))
    WS_SIGNAL_LATENCY_ALERT_MS: int = int(os.getenv("WS_SIGNAL_LATENCY_ALERT_MS", "500"))  # Session 46: was 50, spams on shared VPS
    # Capital allocator: auto-adjust per-bot capital based on PnL performance
    USE_CAPITAL_ALLOCATOR: bool = os.getenv("USE_CAPITAL_ALLOCATOR", "false").lower() in ("true", "1", "yes")
    # MetaLearner: run tuning every N retrain cycles
    META_LEARNER_CYCLE_INTERVAL: int = int(os.getenv("META_LEARNER_CYCLE_INTERVAL", "4"))
    # Self-tuning levers: set to false to disable automatic updates (weights, blend, features)
    SELF_TUNE_MODEL_WEIGHTS: bool = os.getenv("SELF_TUNE_MODEL_WEIGHTS", "true").lower() in ("true", "1", "yes")
    SELF_TUNE_ENSEMBLE_BLEND: bool = os.getenv("SELF_TUNE_ENSEMBLE_BLEND", "true").lower() in ("true", "1", "yes")
    SELF_TUNE_FEATURES: bool = os.getenv("SELF_TUNE_FEATURES", "true").lower() in ("true", "1", "yes")
    MIN_RESOLVED_FOR_FEATURE_SELECTION: int = int(os.getenv("MIN_RESOLVED_FOR_FEATURE_SELECTION", "50"))
    # Feature importance tuning
    CAUSAL_IMPORTANCE_WEIGHT: float = float(os.getenv("CAUSAL_IMPORTANCE_WEIGHT", "0.3"))
    FEATURE_IMPORTANCE_MIN_THRESHOLD: float = float(os.getenv("FEATURE_IMPORTANCE_MIN_THRESHOLD", "0.01"))
    USE_FEATURE_ENGINEER: bool = os.getenv("USE_FEATURE_ENGINEER", "true").lower() in ("true", "1", "yes")
    USE_CAUSAL_IMPORTANCE: bool = os.getenv("USE_CAUSAL_IMPORTANCE", "true").lower() in ("true", "1", "yes")
    # Dead-module wiring gates
    USE_FEATURE_STORE: bool = os.getenv("USE_FEATURE_STORE", "true").lower() in ("true", "1", "yes")
    USE_ERROR_TRACKER: bool = os.getenv("USE_ERROR_TRACKER", "true").lower() in ("true", "1", "yes")
    USE_MODEL_VERSIONING: bool = os.getenv("USE_MODEL_VERSIONING", "true").lower() in ("true", "1", "yes")
    USE_SENTIMENT_ANALYZER: bool = os.getenv("USE_SENTIMENT_ANALYZER", "true").lower() in ("true", "1", "yes")
    USE_METRICS_COLLECTOR: bool = os.getenv("USE_METRICS_COLLECTOR", "true").lower() in ("true", "1", "yes")
    USE_DISTRIBUTED_TRACING: bool = os.getenv("USE_DISTRIBUTED_TRACING", "true").lower() in ("true", "1", "yes")
    USE_QUALITY_METRICS: bool = os.getenv("USE_QUALITY_METRICS", "true").lower() in ("true", "1", "yes")
    USE_SNAPSHOT_MANAGER: bool = os.getenv("USE_SNAPSHOT_MANAGER", "true").lower() in ("true", "1", "yes")
    USE_PIPELINE_GATE: bool = os.getenv("USE_PIPELINE_GATE", "true").lower() in ("true", "1", "yes")
    # Paper trading (SIMULATION_MODE): full pipeline runs (scan → predict → risk check) but orders go to PaperTradingEngine and paper_trades table, not CLOB. Set SIMULATION_MODE=false for real money.
    SIMULATION_MODE: bool = os.getenv("SIMULATION_MODE", "true").lower() in ("true", "1", "yes")
    # S120: Raised to $10M so shared paper cash pool is never the bottleneck.
    # Real sizing limits come from BotBankrollManager ($300 max bet, $10K daily cap per bot).
    PAPER_TRADING_CAPITAL: float = float(os.getenv("PAPER_TRADING_CAPITAL", "10000000"))
    
    # Execution Settings (Maximum Speed)
    ORDER_TIMEOUT_SECONDS: int = 5
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_BASE: float = 1.5
    
    # Phase 6: Speed profile - "default" or "colocated" (shorter timeouts for low-latency VPS)
    SPEED_PROFILE: str = os.getenv("SPEED_PROFILE", "default").lower() or "default"
    
    # Network Timeouts (SECURITY FIX: Centralized timeout configuration)
    HTTP_TIMEOUT_SECONDS: int = 30
    WEBSOCKET_TIMEOUT_SECONDS: int = 60
    DATABASE_TIMEOUT_SECONDS: int = 30
    REDIS_TIMEOUT_SECONDS: int = 5
    # Colocated overrides (used when SPEED_PROFILE == "colocated")
    HTTP_TIMEOUT_COLOCATED: int = 10
    ORDER_EXECUTION_TIMEOUT_COLOCATED: int = 10
    ORDER_TIMEOUT_COLOCATED: int = 3
    MAX_RETRIES_COLOCATED: int = 2
    
    # Trading Timeouts
    ORDER_EXECUTION_TIMEOUT_SECONDS: int = 30
    APPROVAL_TIMEOUT_SECONDS: int = 30
    PRICE_CHECK_TIMEOUT_SECONDS: int = 10
    
    # System Timeouts
    STARTUP_TIMEOUT_SECONDS: int = 60
    SHUTDOWN_TIMEOUT_SECONDS: int = 30
    HEALTH_CHECK_TIMEOUT_SECONDS: int = 5
    
    # Wallet
    PRIVATE_KEY: Optional[str] = os.getenv("PRIVATE_KEY")
    WALLET_ADDRESS: Optional[str] = os.getenv("WALLET_ADDRESS")

    # CLOB API (py-clob-client L2: optional; if set, ExecutionEngine uses official client for orders)
    CLOB_API_KEY: Optional[str] = os.getenv("CLOB_API_KEY")
    CLOB_SECRET: Optional[str] = os.getenv("CLOB_SECRET")
    CLOB_PASSPHRASE: Optional[str] = os.getenv("CLOB_PASSPHRASE")
    POLYGON_CHAIN_ID: int = int(os.getenv("POLYGON_CHAIN_ID", "137"))
    
    # Direct connection — VPS IP handles geo-access (no VPN/proxy needed)
    # Phase 7: User/order WebSocket channel (order_filled, order_update -> EventBus). Requires CLOB API keys.
    USER_ORDER_WS_ENABLED: bool = os.getenv("USER_ORDER_WS_ENABLED", "false").lower() in ("true", "1", "yes")
    # Phase 1: Run USDCe pre-approval (MAX_UINT256) once at engine start to populate ApprovalCache and skip per-order checks
    PREAPPROVE_ON_STARTUP: bool = os.getenv("PREAPPROVE_ON_STARTUP", "true").lower() in ("true", "1", "yes")
    # S120: Balance & fill confirmation settings (live trading)
    BALANCE_WARNING_THRESHOLD_USD: float = float(os.getenv("BALANCE_WARNING_THRESHOLD_USD", "100.0"))
    ORDER_FILL_TIMEOUT_S: float = float(os.getenv("ORDER_FILL_TIMEOUT_S", "60.0"))
    
    # Price Ingestion Configuration
    # Polymarket moved to CLOB (order book) system - blockchain pricing is outdated
    USE_BLOCKCHAIN_PRICES: bool = os.getenv("USE_BLOCKCHAIN_PRICES", "false").lower() == "true"  # Disabled by default
    USE_THEGRAPH_QUERIES: bool = False  # TheGraph queries don't work with current Polymarket schema

    # Ingestion scheduler (periodic markets fetch)
    INGESTION_SCHEDULER_INTERVAL_MINUTES: int = int(os.getenv("INGESTION_SCHEDULER_INTERVAL_MINUTES", "5"))
    INGESTION_TOP_MARKETS_COUNT: int = int(os.getenv("INGESTION_TOP_MARKETS_COUNT", "500"))
    INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS: int = int(os.getenv("INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS", "30"))

    # One-time backfill (last year of data in small batches)
    BACKFILL_DAYS: int = int(os.getenv("BACKFILL_DAYS", "365"))
    BACKFILL_MARKETS_BATCH_SIZE: int = int(os.getenv("BACKFILL_MARKETS_BATCH_SIZE", "100"))
    BACKFILL_PRICES_MARKETS_PER_BATCH: int = int(os.getenv("BACKFILL_PRICES_MARKETS_PER_BATCH", "50"))
    BACKFILL_BATCH_DELAY_SECONDS: float = float(os.getenv("BACKFILL_BATCH_DELAY_SECONDS", "2.0"))

    # Price history ingestion (99% coverage: chunking, rate limit)
    PRICE_HISTORY_DAYS_PER_REQUEST: int = int(os.getenv("PRICE_HISTORY_DAYS_PER_REQUEST", "30"))
    PRICE_HISTORY_MAX_CONCURRENT_MARKETS: int = int(os.getenv("PRICE_HISTORY_MAX_CONCURRENT_MARKETS", "8"))
    PRICE_HISTORY_DELAY_BETWEEN_REQUESTS_SECONDS: float = float(os.getenv("PRICE_HISTORY_DELAY_BETWEEN_REQUESTS_SECONDS", "0.15"))
    PRICE_HISTORY_INTERVAL: str = os.getenv("PRICE_HISTORY_INTERVAL", "1h")  # 1h, 6h, 1d for chunked fallback
    PRICE_HISTORY_TRY_MAX_FIRST: bool = os.getenv("PRICE_HISTORY_TRY_MAX_FIRST", "true").lower() in ("true", "1", "yes")
    PRICE_HISTORY_BULK_BATCH_SIZE: int = int(os.getenv("PRICE_HISTORY_BULK_BATCH_SIZE", "2000"))

    # Daily full ingestion (markets + recent prices for continued learning)
    DAILY_FULL_INGESTION_ENABLED: bool = os.getenv("DAILY_FULL_INGESTION_ENABLED", "true").lower() in ("true", "1", "yes")
    DAILY_INGESTION_DAYS_BACK: int = int(os.getenv("DAILY_INGESTION_DAYS_BACK", "365"))
    DAILY_INGESTION_MARKETS_COUNT: int = int(os.getenv("DAILY_INGESTION_MARKETS_COUNT", "3000"))
    DAILY_INGESTION_PRICES_MARKETS: int = int(os.getenv("DAILY_INGESTION_PRICES_MARKETS", "3000"))

    # Incremental price ingestion (scheduled runs): shorter window + skip recently-updated markets
    PRICE_HISTORY_INCREMENTAL_DAYS: int = int(os.getenv("PRICE_HISTORY_INCREMENTAL_DAYS", "7"))
    PRICE_HISTORY_SKIP_RECENT_HOURS: float = float(os.getenv("PRICE_HISTORY_SKIP_RECENT_HOURS", "6"))
    # Disabled by default: when DB is sparse, range-aware skips too much; set true once you have full data
    PRICE_HISTORY_RANGE_AWARE_FETCH: bool = os.getenv("PRICE_HISTORY_RANGE_AWARE_FETCH", "false").lower() in ("true", "1", "yes")

    # Stale sync_log cleanup: mark "running" entries older than N hours as failed (removes orphaned locks)
    # Full price ingestion for 3000 markets can take 2-4h — needs enough time to complete
    SYNC_LOG_STALE_HOURS: float = float(os.getenv("SYNC_LOG_STALE_HOURS", "6.0"))  # 6h — allows full ingestion to complete

    # PipelineGate: post-condition thresholds (close fire-and-forget loop)
    PIPELINE_GATE_MARKETS_FRESHNESS_HOURS: float = float(os.getenv("PIPELINE_GATE_MARKETS_FRESHNESS_HOURS", "2"))
    PIPELINE_GATE_PRICES_FRESHNESS_HOURS: float = float(os.getenv("PIPELINE_GATE_PRICES_FRESHNESS_HOURS", "24"))
    PIPELINE_GATE_MIN_MARKETS_COUNT: int = int(os.getenv("PIPELINE_GATE_MIN_MARKETS_COUNT", "100"))
    PIPELINE_GATE_SYNC_SUCCESS_MIN_RATE: float = float(os.getenv("PIPELINE_GATE_SYNC_SUCCESS_MIN_RATE", "0.5"))
    PIPELINE_GATE_SYNC_LOOKBACK_HOURS: float = float(os.getenv("PIPELINE_GATE_SYNC_LOOKBACK_HOURS", "24"))
    PIPELINE_GATE_TRAINING_MAX_STALENESS_HOURS: float = float(os.getenv("PIPELINE_GATE_TRAINING_MAX_STALENESS_HOURS", "24"))
    PIPELINE_GATE_MIN_TRAINING_SAMPLES: int = int(os.getenv("PIPELINE_GATE_MIN_TRAINING_SAMPLES", "50"))
    PIPELINE_GATE_RISK_MAX_STALENESS_HOURS: float = float(os.getenv("PIPELINE_GATE_RISK_MAX_STALENESS_HOURS", "24"))

    # Game theory / strategic timing (StrategicTimer)
    TIMING_JITTER_PCT: float = float(os.getenv("TIMING_JITTER_PCT", "0.3"))
    TIMING_SKIP_PROB: float = float(os.getenv("TIMING_SKIP_PROB", "0.05"))
    TIMING_BURST_PROB: float = float(os.getenv("TIMING_BURST_PROB", "0.02"))

    # ExecutionEngine: retries on transient failures (timeout, 429, 5xx)
    EXECUTION_ENGINE_MAX_RETRIES: int = int(os.getenv("EXECUTION_ENGINE_MAX_RETRIES", "2"))

    # BacktestEngine: slippage in basis points (e.g. 50 = 0.5%). Set 0 to disable.
    BACKTEST_SLIPPAGE_BPS: int = int(os.getenv("BACKTEST_SLIPPAGE_BPS", "50"))
    # BacktestEngine: include taker fee in P&L. Uses TAKER_FEE_BPS above.
    BACKTEST_INCLUDE_FEES: bool = os.getenv("BACKTEST_INCLUDE_FEES", "true").lower() in ("true", "1", "yes")

    # Weekly full ingestion: run full 365-day price refresh once per week (0=Monday, 6=Sunday)
    WEEKLY_FULL_INGESTION_ENABLED: bool = os.getenv("WEEKLY_FULL_INGESTION_ENABLED", "true").lower() in ("true", "1", "yes")
    WEEKLY_FULL_INGESTION_WEEKDAY: int = int(os.getenv("WEEKLY_FULL_INGESTION_WEEKDAY", "0"))  # 0=Monday

    # Optimal flow: resolution backfill (fetch missing markets, backfill resolution for learnable trades)
    RESOLUTION_BACKFILL_ENABLED: bool = os.getenv("RESOLUTION_BACKFILL_ENABLED", "true").lower() in ("true", "1", "yes")
    PIPELINE_CANARY_AFTER_INGESTION: bool = os.getenv("PIPELINE_CANARY_AFTER_INGESTION", "true").lower() in ("true", "1", "yes")
    RUN_ORPHAN_CLEANUP_AFTER_INGESTION: bool = os.getenv("RUN_ORPHAN_CLEANUP_AFTER_INGESTION", "true").lower() in ("true", "1", "yes")
    SKIP_RERESOLVED_MARKETS: bool = os.getenv("SKIP_RERESOLVED_MARKETS", "true").lower() in ("true", "1", "yes")
    PRICE_INGESTION_STALE_FIRST: bool = os.getenv("PRICE_INGESTION_STALE_FIRST", "true").lower() in ("true", "1", "yes")
    # P4: Skip markets that have returned empty price history this many times in a row
    PRICE_FETCH_EMPTY_MAX_ATTEMPTS: int = int(os.getenv("PRICE_FETCH_EMPTY_MAX_ATTEMPTS", "5"))
    BACKTEST_LATENCY_SIMULATION_MS: float = float(os.getenv("BACKTEST_LATENCY_SIMULATION_MS", "0"))
    RESOLUTION_BACKFILL_AFTER_DAILY: bool = os.getenv("RESOLUTION_BACKFILL_AFTER_DAILY", "true").lower() in ("true", "1", "yes")
    # Markets to resolve per scheduler cycle (every ~285s). Higher = faster backlog clearance.
    # S125: Bumped 200→500 to clear 1900-market backlog from queue starvation.
    RESOLUTION_QUEUE_BATCH_SIZE: int = int(os.getenv("RESOLUTION_QUEUE_BATCH_SIZE", "500"))

    # I51: Hard timeout for ingest_everything() — raise to 1800s for slow VPS DB
    INGESTION_TIMEOUT_SECONDS: float = float(os.getenv("INGESTION_TIMEOUT_SECONDS", "600"))
    # Master timeout for entire _run_ingestion() cycle — prevents silent scheduler death
    # when a sub-task hangs (e.g. corrupted asyncpg connection after cancellation)
    RUN_INGESTION_MAX_SECONDS: float = float(os.getenv("RUN_INGESTION_MAX_SECONDS", "2400"))

    # Archival/retention: days to keep market_prices before archival (0=disabled)
    MARKET_PRICES_RETENTION_DAYS: int = int(os.getenv("MARKET_PRICES_RETENTION_DAYS", "730"))  # 2 years

    # Retention: delete market_prices older than N days; 0 disables
    PRICE_RETENTION_DAYS: int = int(os.getenv("PRICE_RETENTION_DAYS", "0"))

    # =====================================================
    # ELEVATION PLAN — New Settings
    # =====================================================

    # P5-05: Cross-Platform Arbitrage (Kalshi)
    # Priority 3: Enabled by default — arb formula fixed (S-1), Coinbase rate inversion fixed (S-2).
    # Disable via BOT_ENABLED_CROSS_PLATFORM_ARB=false if Kalshi API key unavailable.
    BOT_ENABLED_CROSS_PLATFORM_ARB: bool = os.getenv("BOT_ENABLED_CROSS_PLATFORM_ARB", "true").lower() in ("true", "1", "yes")
    CROSS_ARB_ENABLED: bool = os.getenv("CROSS_ARB_ENABLED", "false").lower() in ("true", "1", "yes")
    CROSS_ARB_MIN_SPREAD: float = float(os.getenv("CROSS_ARB_MIN_SPREAD", "0.04"))
    CROSS_ARB_MAX_POSITION: float = float(os.getenv("CROSS_ARB_MAX_POSITION", "200"))
    CROSS_ARB_MIN_LAG_THRESHOLD: float = float(os.getenv("CROSS_ARB_MIN_LAG_THRESHOLD", "0.02"))
    KALSHI_API_KEY: Optional[str] = os.getenv("KALSHI_API_KEY")
    KALSHI_EMAIL: Optional[str] = os.getenv("KALSHI_EMAIL")

    # P4-01: CVaR Risk Management
    CVAR_CONFIDENCE_LEVEL: float = float(os.getenv("CVAR_CONFIDENCE_LEVEL", "0.95"))
    CVAR_MAX_PORTFOLIO_CVAR: float = float(os.getenv("CVAR_MAX_PORTFOLIO_CVAR", "500"))
    CVAR_SIMULATIONS: int = int(os.getenv("CVAR_SIMULATIONS", "10000"))

    # P4-03: Multi-Layer Kill Switch
    BOT_KILL_AUTO_RESET_MINUTES: int = int(os.getenv("BOT_KILL_AUTO_RESET_MINUTES", "60"))
    PORTFOLIO_KILL_AUTO_RESET_HOURS: int = int(os.getenv("PORTFOLIO_KILL_AUTO_RESET_HOURS", "24"))

    # P5-01: Tunable Config
    TUNABLE_CONFIG_ENABLED: bool = os.getenv("TUNABLE_CONFIG_ENABLED", "true").lower() in ("true", "1", "yes")
    CONFIG_TUNER_INTERVAL_HOURS: float = float(os.getenv("CONFIG_TUNER_INTERVAL_HOURS", "6"))

    # P5-03: LLM Probability Estimation
    LLM_PROBABILITY_ENABLED: bool = os.getenv("LLM_PROBABILITY_ENABLED", "false").lower() in ("true", "1", "yes")
    LLM_PROBABILITY_CACHE_TTL: int = int(os.getenv("LLM_PROBABILITY_CACHE_TTL", "3600"))
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")

    # Tier 2 #16: Resolution clarity scoring
    # LLM-powered (60%) + regex (40%) blend. Scores 0.0 (ambiguous) → 1.0 (crystal clear).
    # Cached per-market for RESOLUTION_CLARITY_CACHE_TTL_HOURS (default 24h).
    # Used as an ML feature (clarity_score) AND as a multiplier in EnsembleBot confidence.
    RESOLUTION_CLARITY_ENABLED: bool = os.getenv("RESOLUTION_CLARITY_ENABLED", "true").lower() in ("true", "1", "yes")
    RESOLUTION_CLARITY_CACHE_TTL_HOURS: float = float(os.getenv("RESOLUTION_CLARITY_CACHE_TTL_HOURS", "24"))
    # OPENAI_API_KEY — defined earlier in External API Keys section (line ~52)

    # P5-06: Oracle Monitor
    UMA_ORACLE_CONTRACT: Optional[str] = os.getenv("UMA_ORACLE_CONTRACT")
    POLYGON_RPC_URL: Optional[str] = os.getenv("POLYGON_RPC_URL")

    # P6-02: Secret Manager
    VAULT_PASSWORD: Optional[str] = os.getenv("VAULT_PASSWORD")

    # P3-06: Calibration
    CALIBRATION_FIT_ON_STARTUP: bool = os.getenv("CALIBRATION_FIT_ON_STARTUP", "true").lower() in ("true", "1", "yes")

    # =====================================================
    # 2026 ALPHA ROADMAP — Bot Roster & Infrastructure
    # =====================================================

    # Bot enable flags (new bots)
    BOT_ENABLED_ENSEMBLE: bool = os.getenv("BOT_ENABLED_ENSEMBLE", "true").lower() in ("true", "1", "yes")
    BOT_ENABLED_ARBITRAGE: bool = os.getenv("BOT_ENABLED_ARBITRAGE", "true").lower() in ("true", "1", "yes")
    BOT_ENABLED_MIRROR: bool = os.getenv("BOT_ENABLED_MIRROR", "true").lower() in ("true", "1", "yes")
    BOT_ENABLED_ORACLE: bool = os.getenv("BOT_ENABLED_ORACLE", "false").lower() in ("true", "1", "yes")
    BOT_ENABLED_SPORTS: bool = os.getenv("BOT_ENABLED_SPORTS", "false").lower() in ("true", "1", "yes")
    BOT_ENABLED_LLM_FORECASTER: bool = os.getenv("BOT_ENABLED_LLM_FORECASTER", "false").lower() in ("true", "1", "yes")
    # Priority 3: WeatherBot enabled by default — SWOT upgrades complete, central Kelly wired.
    # Disable via BOT_ENABLED_WEATHER=false in .env if NOAA API unavailable.
    BOT_ENABLED_WEATHER: bool = os.getenv("BOT_ENABLED_WEATHER", "true").lower() in ("true", "1", "yes")

    # WeatherBot configuration
    SCAN_INTERVAL_WEATHER: int = int(os.getenv("SCAN_INTERVAL_WEATHER", "300"))
    WEATHER_MIN_EDGE: float = float(os.getenv("WEATHER_MIN_EDGE", "0.08"))
    WEATHER_INTL_MIN_EDGE: float = float(os.getenv("WEATHER_INTL_MIN_EDGE", "0.12"))  # Floor for intl cities without local hi-res model
    WEATHER_GROUP_CONCURRENCY: int = int(os.getenv("WEATHER_GROUP_CONCURRENCY", "16"))  # S97: raised 12→16. Max concurrent group analyses per scan
    WEATHER_RATE_LIMIT_PER_MIN: int = int(os.getenv("WEATHER_RATE_LIMIT_PER_MIN", "120"))  # Open-Meteo API rate limit (free tier burst-tolerant to 600/min)
    WEATHER_MIN_CONFIDENCE: float = float(os.getenv("WEATHER_MIN_CONFIDENCE", "0.10"))  # Multi-bucket: 9 outcomes → peak ~35-40%; lowered to 0.10 to not block boundary-risk trades
    WEATHER_MAX_POSITIONS: int = int(os.getenv("WEATHER_MAX_POSITIONS", "1000"))  # S122: 500→1000
    WEATHER_MAX_PER_GROUP_USD: float = float(os.getenv("WEATHER_MAX_PER_GROUP_USD", "10000"))  # S122: 1000→10000
    WEATHER_DAILY_LOSS_LIMIT: float = float(os.getenv("WEATHER_DAILY_LOSS_LIMIT", "10000"))
    WEATHER_MAX_CORRELATED_EXPOSURE: float = float(os.getenv("WEATHER_MAX_CORRELATED_EXPOSURE", "5000"))  # S122: 2000→5000
    WEATHER_KELLY_FRACTION: float = float(os.getenv("WEATHER_KELLY_FRACTION", "0.25"))
    WEATHER_DEFAULT_SIZE: float = float(os.getenv("WEATHER_DEFAULT_SIZE", "25"))
    WEATHER_FORECAST_CACHE_TTL: int = int(os.getenv("WEATHER_FORECAST_CACHE_TTL", "1800"))
    WEATHER_MAX_LEAD_TIME_HOURS: int = int(os.getenv("WEATHER_MAX_LEAD_TIME_HOURS", "168"))
    WEATHER_SKIP_COORDINATOR_BUY: bool = os.getenv("WEATHER_SKIP_COORDINATOR_BUY", "true").lower() in ("true", "1", "yes")
    WEATHER_TRADE_CONCURRENCY: int = int(os.getenv("WEATHER_TRADE_CONCURRENCY", "8"))
    WEATHER_MAX_TOTAL_EXPOSURE_USD: float = float(os.getenv("WEATHER_MAX_TOTAL_EXPOSURE_USD", "50000"))
    WEATHER_TOTAL_CAPITAL: float = float(os.getenv("WEATHER_TOTAL_CAPITAL", "20000"))  # S105: aligned to $20K
    # S107: Re-entry cooldown per market_id after exit (was 15min, now 4hr)
    WEATHER_EXIT_COOLDOWN_SECS: float = float(os.getenv("WEATHER_EXIT_COOLDOWN_SECS", "14400"))
    # S107: Baker-McHale uncertainty floor (prevents 0.26x crush on high-spread forecasts)
    WEATHER_BM_FLOOR: float = float(os.getenv("WEATHER_BM_FLOOR", "0.50"))
    # S107: Minimum trade size in USD (was $1, now $5 — eliminates dust positions)
    WEATHER_MIN_TRADE_USD: float = float(os.getenv("WEATHER_MIN_TRADE_USD", "5.0"))
    # S115: Bühlmann credibility denominator — higher k = slower sizing ramp for new stations
    WEATHER_BUHLMANN_KAPPA: float = float(os.getenv("WEATHER_BUHLMANN_KAPPA", "30.0"))
    # S116: YES-side confidence gate threshold. S135: default 0.35 (kills 6.4% WR dust trades)
    WEATHER_YES_MIN_CONFIDENCE: float = float(os.getenv("WEATHER_YES_MIN_CONFIDENCE", "0.35"))
    # S115: Combined sizing boost cap (expiry * regime * jump * nbm * bm * station * calibration)
    WEATHER_COMBINED_BOOST_CAP: float = float(os.getenv("WEATHER_COMBINED_BOOST_CAP", "1.5"))  # S122: 2.0→1.5
    # S118: NO entry price cap — NO trades with entry price above this are skipped.
    # Data: 70-80¢ bucket is -$484 (76.4% WR, 0.24x win/loss). <60¢ is +$1,836.
    WEATHER_NO_MAX_ENTRY_PRICE: float = float(os.getenv("WEATHER_NO_MAX_ENTRY_PRICE", "1.0"))  # S122: removed (was 0.65). Set to 1.0 = no cap.
    # S118: Max buckets per city+date group — limits correlated blowup risk.
    WEATHER_MAX_BUCKETS_PER_GROUP: int = int(os.getenv("WEATHER_MAX_BUCKETS_PER_GROUP", "5"))  # S122: 3→5
    # S118: Confidence discount for high-price NO trades — reduces Kelly sizing.
    # Applied when NO entry price > NO_CONFIDENCE_DISCOUNT_THRESHOLD.
    # Data: 90-95% confidence NO trades lost -$3,412 from 133 trades.
    WEATHER_NO_CONFIDENCE_DISCOUNT: float = float(os.getenv("WEATHER_NO_CONFIDENCE_DISCOUNT", "0.80"))
    WEATHER_NO_CONFIDENCE_DISCOUNT_THRESHOLD: float = float(os.getenv("WEATHER_NO_CONFIDENCE_DISCOUNT_THRESHOLD", "0.70"))
    # S99: Fill-failure cooldown
    WEATHER_FILL_FAIL_COOLDOWN_SCANS: int = int(os.getenv("WEATHER_FILL_FAIL_COOLDOWN_SCANS", "2"))
    WEATHER_FILL_FAIL_COOLDOWN_SECS: float = float(os.getenv("WEATHER_FILL_FAIL_COOLDOWN_SECS", "120"))  # S101: 900→120s — IOC gas negligible, 2min = 1 scan cycle
    # S99: Fill probability floor (price-depth factor)
    WEATHER_MIN_FILL_PROB_ESTIMATE: float = float(os.getenv("WEATHER_MIN_FILL_PROB_ESTIMATE", "0.15"))  # S101: 0.25→0.15 — pre-flight only, full model still gates
    # S99: PSW every-other-scan
    WEATHER_PSW_SCAN_DIVISOR: int = int(os.getenv("WEATHER_PSW_SCAN_DIVISOR", "2"))
    # S99: Adaptive backoff
    WEATHER_ADAPTIVE_BACKOFF_THRESHOLD: int = int(os.getenv("WEATHER_ADAPTIVE_BACKOFF_THRESHOLD", "6"))
    WEATHER_MAX_SCAN_INTERVAL: float = float(os.getenv("WEATHER_MAX_SCAN_INTERVAL", "600"))
    # S120: EMOS calibration rolling window (days). Prevents seasonal contamination.
    WEATHER_EMOS_WINDOW_DAYS: int = int(os.getenv("WEATHER_EMOS_WINDOW_DAYS", "90"))
    # S121: Externalized hardcoded values
    WEATHER_ALPHA_DECAY_HALF_LIFE_S: float = float(os.getenv("WEATHER_ALPHA_DECAY_HALF_LIFE_S", "1800"))
    WEATHER_NBM_BOOST: float = float(os.getenv("WEATHER_NBM_BOOST", "1.3"))
    # NOTE: WEATHER_COMBINED_BOOST_CAP defined above at S122 (1.5). Do not duplicate here.
    # S121: Model freshness sizing — scale by NWP model age (hours since last init)
    WEATHER_MODEL_FRESH_HOURS: float = float(os.getenv("WEATHER_MODEL_FRESH_HOURS", "2.0"))
    WEATHER_MODEL_STALE_HOURS: float = float(os.getenv("WEATHER_MODEL_STALE_HOURS", "8.0"))
    WEATHER_MODEL_FRESH_BOOST: float = float(os.getenv("WEATHER_MODEL_FRESH_BOOST", "1.2"))
    WEATHER_MODEL_STALE_DISCOUNT: float = float(os.getenv("WEATHER_MODEL_STALE_DISCOUNT", "0.8"))

    # S123: Platt + Isotonic confidence calibration for Kelly sizing
    WEATHER_CONFIDENCE_CAL_ENABLED: bool = os.getenv("WEATHER_CONFIDENCE_CAL_ENABLED", "true").lower() == "true"
    WEATHER_CONFIDENCE_CAL_WINDOW_DAYS: int = int(os.getenv("WEATHER_CONFIDENCE_CAL_WINDOW_DAYS", "30"))
    WEATHER_CONFIDENCE_CAL_MIN_SAMPLES: int = int(os.getenv("WEATHER_CONFIDENCE_CAL_MIN_SAMPLES", "200"))
    # S143: YES-side fallback window — wider window when 30d has <30 YES samples
    WEATHER_CONFIDENCE_CAL_YES_FALLBACK_WINDOW_DAYS: int = int(os.getenv("WEATHER_CONFIDENCE_CAL_YES_FALLBACK_WINDOW_DAYS", "90"))
    # S135: Disable combined_boost for YES side — NO keeps all boosts
    WEATHER_YES_BOOST_ENABLED: bool = os.getenv("WEATHER_YES_BOOST_ENABLED", "false").lower() == "true"

    # S140: Bid-ask spread gate — reject markets with spread > threshold (cents).
    # Replaces S132-removed spread inflation as a hard floor.
    WEATHER_MAX_SPREAD: float = float(os.getenv("WEATHER_MAX_SPREAD", "0.30"))

    # S124: Lead-time spread inflation for probability engine (default OFF)
    # S126: Two-component spread inflation to reduce overconfidence.
    # BASE applies uniformly at all lead times (fixes short-lead overconfidence).
    # FACTOR adds sqrt(lead_days)-scaled inflation for longer forecasts.
    # With base=0.15, factor=0.10: 24h→×1.15, 48h→×1.19, 72h→×1.22, 120h→×1.27
    WEATHER_SPREAD_INFLATION_BASE: float = float(os.getenv("WEATHER_SPREAD_INFLATION_BASE", "0.0"))
    WEATHER_SPREAD_INFLATION_FACTOR: float = float(os.getenv("WEATHER_SPREAD_INFLATION_FACTOR", "0.0"))
    # S126: ISO-8601 start date for auto-decay (10%/day, hard zero at day 23)
    WEATHER_SPREAD_INFLATION_START: str = os.getenv("WEATHER_SPREAD_INFLATION_START", "")

    # Scan intervals (seconds)
    SCAN_INTERVAL_ENSEMBLE: int = int(os.getenv("SCAN_INTERVAL_ENSEMBLE", "60"))
    # WebSocket reactive scan: trigger immediate analysis on significant price moves
    ENSEMBLE_WS_PRICE_CHANGE_PCT: float = float(os.getenv("ENSEMBLE_WS_PRICE_CHANGE_PCT", "0.005"))
    ENSEMBLE_WS_COOLDOWN_SECONDS: float = float(os.getenv("ENSEMBLE_WS_COOLDOWN_SECONDS", "5"))
    ENSEMBLE_SCAN_CONCURRENCY: int = int(os.getenv("ENSEMBLE_SCAN_CONCURRENCY", "10"))  # parallel market analyses per scan (warm cache = near-zero DB pressure, pool_size=10)
    SCAN_INTERVAL_ARBITRAGE: int = int(os.getenv("SCAN_INTERVAL_ARBITRAGE", "30"))
    ARB_WS_PRICE_CHANGE_PCT: float = float(os.getenv("ARB_WS_PRICE_CHANGE_PCT", "0.008"))
    ARB_WS_COOLDOWN_SECONDS: float = float(os.getenv("ARB_WS_COOLDOWN_SECONDS", "2"))
    SCAN_INTERVAL_MIRROR: int = int(os.getenv("SCAN_INTERVAL_MIRROR", "45"))
    BOT_SCAN_TIMEOUT_SECONDS: int = int(os.getenv("BOT_SCAN_TIMEOUT_SECONDS", "300"))
    SCAN_INTERVAL_CROSS_PLATFORM_ARB: int = int(os.getenv("SCAN_INTERVAL_CROSS_PLATFORM_ARB", "15"))
    SCAN_INTERVAL_SPORTS: int = int(os.getenv("SCAN_INTERVAL_SPORTS", "120"))
    SCAN_INTERVAL_ORACLE: int = int(os.getenv("SCAN_INTERVAL_ORACLE", "60"))
    SCAN_INTERVAL_LLM_FORECASTER: int = int(os.getenv("SCAN_INTERVAL_LLM_FORECASTER", "120"))

    # EnsembleBot: category filtering (absorbed from CryptoPoliticalBot)
    ENSEMBLE_TARGET_CATEGORIES: str = os.getenv("ENSEMBLE_TARGET_CATEGORIES", "")  # empty = all categories

    # A1: Category-scaled FLB delta (Becker 2026 — YES longshot bias varies 43× by category)
    # Scale factor applied to the flat ±0.03 FLB delta per category.
    # World Events: 7.32pp gap → 4.0×. Finance: 0.17pp → 0.09× (near-efficient, almost no bias).
    CATEGORY_BIAS_SCALE: dict = {
        "world events": 4.0,
        "media": 3.9,       # 7.28pp gap (nearly equal to world events)
        "entertainment": 2.6,
        "politics": 1.5,
        "sports": 1.2,
        "crypto": 1.0,
        "science": 0.7,
        "finance": 0.09,    # near-efficient — barely any longshot bias
    }

    # B2: Recency-weighted training — exponential decay lambda for sample_weight
    # w_i = exp(-TRAINING_RECENCY_LAMBDA * (T - t_i) / T)
    # 1.0 = moderate recency bias. 0.0 = uniform weights (disabled). 2.0+ = strong recency.
    TRAINING_RECENCY_LAMBDA: float = float(os.getenv("TRAINING_RECENCY_LAMBDA", "1.0"))

    # ArbitrageBot: NegRisk multi-outcome arbitrage (Tier 2 #12)
    # Max total capital deployed across ALL outcome legs of a single NegRisk trade
    NEGRISK_MAX_TOTAL_RISK: float = float(os.getenv("NEGRISK_MAX_TOTAL_RISK", "300.0"))

    # ArbitrageBot: bond strategy
    BOND_STRATEGY_MIN_PRICE: float = float(os.getenv("BOND_STRATEGY_MIN_PRICE", "0.95"))
    BOND_STRATEGY_MAX_RESOLUTION_DAYS: int = int(os.getenv("BOND_STRATEGY_MAX_RESOLUTION_DAYS", "7"))
    BOND_MAX_PER_SCAN: int = int(os.getenv("BOND_MAX_PER_SCAN", "3"))
    BOND_MAX_SIZE: float = float(os.getenv("BOND_MAX_SIZE", "200"))

    # Exchange adapters
    COINBASE_PRED_API_KEY: Optional[str] = os.getenv("COINBASE_PRED_API_KEY")
    COINBASE_PRED_API_SECRET: Optional[str] = os.getenv("COINBASE_PRED_API_SECRET")
    COINBASE_PRED_ENABLED: bool = os.getenv("COINBASE_PRED_ENABLED", "false").lower() in ("true", "1", "yes")
    FORECASTEX_ENABLED: bool = os.getenv("FORECASTEX_ENABLED", "false").lower() in ("true", "1", "yes")
    FORECASTEX_IB_HOST: str = os.getenv("FORECASTEX_IB_HOST", "127.0.0.1")
    FORECASTEX_IB_PORT: int = int(os.getenv("FORECASTEX_IB_PORT", "7497"))
    FORECASTEX_IB_CLIENT_ID: int = int(os.getenv("FORECASTEX_IB_CLIENT_ID", "10"))

    # OracleBot
    ORACLE_BOT_MAX_ENTRY_PRICE: float = float(os.getenv("ORACLE_BOT_MAX_ENTRY_PRICE", "0.97"))
    ORACLE_BOT_MAX_POSITION: float = float(os.getenv("ORACLE_BOT_MAX_POSITION", "200"))

    # SportsBot
    SPORTS_RAPID_RESOLUTION_MODE: bool = os.getenv("SPORTS_RAPID_RESOLUTION_MODE", "true").lower() in ("true", "1", "yes")
    WORLD_CUP_MODE: bool = os.getenv("WORLD_CUP_MODE", "false").lower() in ("true", "1", "yes")
    API_FOOTBALL_KEY: Optional[str] = os.getenv("API_FOOTBALL_KEY")

    # ─── Phase-Based Guardrails (pre-live-money controls) ────────────────────────
    # TRADING_PHASE: current operational phase.
    # paper     → conservative ($15/bet, consec=0, side-bias=75%)
    # learning  → tighter ($20/bet, consec=3, side-bias=65%)
    # graduated → looser ($200/bet, consec=4, side-bias=70%)
    # production → full Kelly ($1000/bet, consec=5, side-bias=75%)
    TRADING_PHASE: str = os.getenv("TRADING_PHASE", "paper")

    # Phase-based max bet USD cap (hard floor applied after Kelly).
    # JSON dict: {"paper": 15.0, "learning": 20.0, "graduated": 200.0, "production": 1000.0}
    PHASE_MAX_BET_USD: str = os.getenv(
        "PHASE_MAX_BET_USD",
        '{"paper": 15.0, "learning": 20.0, "graduated": 200.0, "production": 1000.0}'
    )

    # Category-specific base Kelly fraction (replaces global KELLY_FRACTION per category).
    # Volatile/uncertain categories get lower fractions; high-edge categories can go higher.
    # JSON dict: {"weather": 0.25, "crypto": 0.125, "politics": 0.20, "sports": 0.15}
    CATEGORY_KELLY_FRACTIONS: str = os.getenv(
        "CATEGORY_KELLY_FRACTIONS",
        '{"weather": 0.25, "crypto": 0.125, "politics": 0.20, "sports": 0.15, "finance": 0.10}'
    )

    # Politics profit-taking exit: close positions when this fraction of max edge is captured.
    # 0.65 = exit when P&L = 65% of (max_possible_profit). 0 = disabled.
    POLITICS_EXIT_ENABLED: bool = os.getenv("POLITICS_EXIT_ENABLED", "true").lower() in ("true", "1", "yes")
    POLITICS_EXIT_PCT: float = float(os.getenv("POLITICS_EXIT_PCT", "0.65"))
    # Minimum unrealized P&L USD before politics exit is considered (avoid tiny exits)
    POLITICS_EXIT_MIN_PROFIT_USD: float = float(os.getenv("POLITICS_EXIT_MIN_PROFIT_USD", "2.0"))

    # Weather hold-to-resolution: boost near-expiry sizing window (NOAA edge widens).
    # P1: Model-run jump detection — sizing boost when ensemble mean shifts ≥ threshold between model runs
    WEATHER_JUMP_THRESHOLD_F: float = float(os.getenv("WEATHER_JUMP_THRESHOLD_F", "3.0"))  # °F shift to trigger boost
    WEATHER_JUMP_MAX_BOOST: float = float(os.getenv("WEATHER_JUMP_MAX_BOOST", "1.5"))  # Max sizing multiplier from jump
    # P2: NBM CDF benchmark — flag high-conviction when NBM disagrees with market by ≥ threshold
    WEATHER_NBM_DISAGREE_THRESHOLD: float = float(os.getenv("WEATHER_NBM_DISAGREE_THRESHOLD", "0.15"))  # 15pp

    # WEATHER_HOLD_HOURS_BEFORE_RESOLUTION: hours before resolution where expiry boost is applied.
    # 48 = boost starts 48h before market resolves (2× boost at <12h, 1.5× at <24h, 1.2× at <48h).
    WEATHER_HOLD_HOURS_BEFORE_RESOLUTION: float = float(os.getenv("WEATHER_HOLD_HOURS_BEFORE_RESOLUTION", "48.0"))

    # Phase graduation tracker: evaluate metrics every N hours; log promotion/demotion guidance.
    PHASE_GRADUATION_ENABLED: bool = os.getenv("PHASE_GRADUATION_ENABLED", "true").lower() in ("true", "1", "yes")
    PHASE_GRADUATION_CHECK_HOURS: float = float(os.getenv("PHASE_GRADUATION_CHECK_HOURS", "24.0"))
    # Paper → Learning thresholds
    PHASE_PAPER_TO_LEARNING_WIN_RATE: float = float(os.getenv("PHASE_PAPER_TO_LEARNING_WIN_RATE", "0.52"))
    PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS: int = int(os.getenv("PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS", "100"))
    PHASE_PAPER_TO_LEARNING_MAX_BRIER: float = float(os.getenv("PHASE_PAPER_TO_LEARNING_MAX_BRIER", "0.22"))
    # Learning → Graduated thresholds
    PHASE_LEARNING_TO_GRADUATED_WIN_RATE: float = float(os.getenv("PHASE_LEARNING_TO_GRADUATED_WIN_RATE", "0.55"))
    PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS: int = int(os.getenv("PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS", "300"))
    PHASE_LEARNING_TO_GRADUATED_MAX_BRIER: float = float(os.getenv("PHASE_LEARNING_TO_GRADUATED_MAX_BRIER", "0.20"))

    # RLVR (Reinforcement Learning with Verifiable Rewards)
    RLVR_ENABLED: bool = os.getenv("RLVR_ENABLED", "false").lower() in ("true", "1", "yes")
    RLVR_MODEL_PATH: str = os.getenv("RLVR_MODEL_PATH", "")
    RLVR_ENSEMBLE_RUNS: int = int(os.getenv("RLVR_ENSEMBLE_RUNS", "7"))
    RLVR_BATCH_INTERVAL_MINUTES: int = int(os.getenv("RLVR_BATCH_INTERVAL_MINUTES", "30"))


    # ML features
    CAUSAL_INFERENCE_ENABLED: bool = os.getenv("CAUSAL_INFERENCE_ENABLED", "false").lower() in ("true", "1", "yes")
    AGENTIC_RAG_ENABLED: bool = os.getenv("AGENTIC_RAG_ENABLED", "false").lower() in ("true", "1", "yes")
    LLM_ENSEMBLE_ENABLED: bool = os.getenv("LLM_ENSEMBLE_ENABLED", "false").lower() in ("true", "1", "yes")
    GOOGLE_GEMINI_API_KEY: Optional[str] = os.getenv("GOOGLE_GEMINI_API_KEY")

    # Regulatory / Compliance
    GEO_RESTRICTION_ENABLED: bool = os.getenv("GEO_RESTRICTION_ENABLED", "false").lower() in ("true", "1", "yes")
    USER_STATE: str = os.getenv("USER_STATE", "")
    USER_COUNTRY: str = os.getenv("USER_COUNTRY", "US")
    REGULATORY_MONITOR_ENABLED: bool = os.getenv("REGULATORY_MONITOR_ENABLED", "false").lower() in ("true", "1", "yes")
    TAX_CLASSIFICATION: str = os.getenv("TAX_CLASSIFICATION", "dual")  # "section_1256", "gambling", "dual"

    # Oracle hardening
    ORACLE_MANIPULATION_RISK_ENABLED: bool = os.getenv("ORACLE_MANIPULATION_RISK_ENABLED", "true").lower() in ("true", "1", "yes")

    # Contract change monitoring
    CONTRACT_CHANGE_MONITOR_ENABLED: bool = os.getenv("CONTRACT_CHANGE_MONITOR_ENABLED", "false").lower() in ("true", "1", "yes")

    # Airdrop tracking
    AIRDROP_TRACKER_ENABLED: bool = os.getenv("AIRDROP_TRACKER_ENABLED", "false").lower() in ("true", "1", "yes")

    # Sentiment (from CryptoPoliticalBot, now in EnsembleBot)
    SENTIMENT_CACHE_TTL_SECONDS: int = int(os.getenv("SENTIMENT_CACHE_TTL_SECONDS", "600"))
    SENTIMENT_CACHE_MAX_SIZE: int = int(os.getenv("SENTIMENT_CACHE_MAX_SIZE", "500"))
    SENTIMENT_MIN_TRADE_COUNT: int = int(os.getenv("SENTIMENT_MIN_TRADE_COUNT", "15"))
    SENTIMENT_NEUTRAL_THRESHOLD: float = float(os.getenv("SENTIMENT_NEUTRAL_THRESHOLD", "0.05"))

    # Sentiment pipeline — FinBERT/CardiffNLP cascade
    SENTIMENT_USE_FINBERT: bool = os.getenv("SENTIMENT_USE_FINBERT", "true").lower() in ("true", "1", "yes")
    SENTIMENT_VADER_THRESHOLD: float = float(os.getenv("SENTIMENT_VADER_THRESHOLD", "0.6"))
    SIGNAL_DEDUP_WINDOW_SECONDS: int = int(os.getenv("SIGNAL_DEDUP_WINDOW_SECONDS", "1800"))

    # Spike detection
    SPIKE_Z_SCORE_NOTABLE: float = float(os.getenv("SPIKE_Z_SCORE_NOTABLE", "2.0"))
    SPIKE_Z_SCORE_MAJOR: float = float(os.getenv("SPIKE_Z_SCORE_MAJOR", "3.0"))

    # Velocity engine
    VELOCITY_SPIKE_THRESHOLD: float = float(os.getenv("VELOCITY_SPIKE_THRESHOLD", "3.0"))
    VELOCITY_MAJOR_THRESHOLD: float = float(os.getenv("VELOCITY_MAJOR_THRESHOLD", "5.0"))

    # Reddit streaming (register free app at reddit.com/prefs/apps)
    REDDIT_USERNAME: Optional[str] = os.getenv("REDDIT_USERNAME", None)
    REDDIT_PASSWORD: Optional[str] = os.getenv("REDDIT_PASSWORD", None)
    REDDIT_SUBREDDITS: str = os.getenv("REDDIT_SUBREDDITS", "polymarket,politics,worldnews,cryptocurrency,wallstreetbets")
    USE_REDDIT_STREAMING: bool = os.getenv("USE_REDDIT_STREAMING", "false").lower() in ("true", "1", "yes")

    # Telegram streaming (register at my.telegram.org/apps)
    TELEGRAM_API_ID: Optional[str] = os.getenv("TELEGRAM_API_ID", None)
    TELEGRAM_API_HASH: Optional[str] = os.getenv("TELEGRAM_API_HASH", None)
    TELEGRAM_CHANNELS: str = os.getenv("TELEGRAM_CHANNELS", "polymarket_chat,crypto_signals")

    # Discord streaming (create at discord.com/developers/applications)
    DISCORD_BOT_TOKEN: Optional[str] = os.getenv("DISCORD_BOT_TOKEN", None)
    DISCORD_CHANNEL_IDS: str = os.getenv("DISCORD_CHANNEL_IDS", "")

    # Discord webhook for AlertingSystem (Server Settings → Integrations → Webhooks)
    DISCORD_WEBHOOK_URL: Optional[str] = os.getenv("DISCORD_WEBHOOK_URL") or None

    # Bot heartbeat staleness threshold (Session 51)
    BOT_HEARTBEAT_STALE_MINUTES: int = int(os.getenv("BOT_HEARTBEAT_STALE_MINUTES", "15"))

    # Cascade/Persuasion detection (already existed as flags, re-exposed for new bot wiring)
    CASCADE_CHECK_ENABLED: bool = os.getenv("CASCADE_CHECK_ENABLED", "true").lower() in ("true", "1", "yes")
    CASCADE_SCORE_THRESHOLD: float = float(os.getenv("CASCADE_SCORE_THRESHOLD", "0.6"))

    # LLM A/B testing
    LLM_AB_TEST_PROMPTS: bool = os.getenv("LLM_AB_TEST_PROMPTS", "false").lower() in ("true", "1", "yes")

    # AIA-style independent CoT ensemble (5x LLM cost — only on trade candidates via aia_mode=True)
    LLM_AIA_ENSEMBLE: bool = os.getenv("LLM_AIA_ENSEMBLE", "false").lower() in ("true", "1", "yes")
    LLM_AIA_MIN_VOLUME: float = float(os.getenv("LLM_AIA_MIN_VOLUME", "10000"))  # min market volume to trigger
    LLM_AIA_CACHE_TTL: int = int(os.getenv("LLM_AIA_CACHE_TTL", "21600"))  # 6h — weather questions are stable

    # Canary deployment: graduated capital rollout (5% → 25% → 50% → 100%)
    CANARY_STAGE: int = int(os.getenv("CANARY_STAGE", "0"))  # 0=off, 1=5%, 2=25%, 3=50%, 4=100%
    CANARY_MIN_SHARPE: float = float(os.getenv("CANARY_MIN_SHARPE", "0.5"))
    CANARY_MAX_BRIER: float = float(os.getenv("CANARY_MAX_BRIER", "0.25"))
    CANARY_AUTO_ADVANCE: bool = os.getenv("CANARY_AUTO_ADVANCE", "true").lower() in ("true", "1", "yes")
    CANARY_MIN_TRADES_PER_STAGE: int = int(os.getenv("CANARY_MIN_TRADES_PER_STAGE", "50"))

    # PSI feature drift detection (Item 21)
    PSI_CHECK_INTERVAL: int = int(os.getenv("PSI_CHECK_INTERVAL", "1000"))
    PSI_DRIFT_THRESHOLD: float = float(os.getenv("PSI_DRIFT_THRESHOLD", "0.2"))

    # ============================================================
    # SPORTS BETTING — Migration 022
    # Three bots: SportsInjuryBot / SportsLiveBot / SportsArbBot
    # All disabled by default; enable per-phase as code matures.
    # ============================================================

    # --- Bot enable flags ---
    BOT_ENABLED_SPORTS_INJURY: bool = os.getenv("BOT_ENABLED_SPORTS_INJURY", "false").lower() in ("true", "1", "yes")
    BOT_ENABLED_SPORTS_LIVE: bool = os.getenv("BOT_ENABLED_SPORTS_LIVE", "false").lower() in ("true", "1", "yes")
    BOT_ENABLED_SPORTS_ARB: bool = os.getenv("BOT_ENABLED_SPORTS_ARB", "false").lower() in ("true", "1", "yes")

    # --- Scan intervals (seconds) ---
    SCAN_INTERVAL_SPORTS_INJURY: int = int(os.getenv("SCAN_INTERVAL_SPORTS_INJURY", "10"))
    SCAN_INTERVAL_SPORTS_LIVE: int = int(os.getenv("SCAN_INTERVAL_SPORTS_LIVE", "10"))
    SCAN_INTERVAL_SPORTS_ARB: int = int(os.getenv("SCAN_INTERVAL_SPORTS_ARB", "30"))

    # --- Bankroll / sizing limits ---
    SPORTS_MAX_BET_USD: float = float(os.getenv("SPORTS_MAX_BET_USD", "100.0"))
    SPORTS_MAX_DAILY_USD: float = float(os.getenv("SPORTS_MAX_DAILY_USD", "500.0"))
    SPORTS_TOTAL_CAPITAL: float = float(os.getenv("SPORTS_TOTAL_CAPITAL", "10000.0"))
    SPORTS_MIN_EDGE: float = float(os.getenv("SPORTS_MIN_EDGE", "0.05"))
    SPORTS_MIN_CONFIDENCE: float = float(os.getenv("SPORTS_MIN_CONFIDENCE", "0.60"))
    SPORTS_LIVE_MIN_CONFIDENCE: float = float(os.getenv("SPORTS_LIVE_MIN_CONFIDENCE", "0.70"))
    SPORTS_ARB_MIN_SPREAD: float = float(os.getenv("SPORTS_ARB_MIN_SPREAD", "0.04"))

    # --- Data API credentials ---
    SPORTS_DATA_IO_API_KEY: Optional[str] = os.getenv("SPORTS_DATA_IO_API_KEY")
    SPORTS_DATA_IO_BASE_URL: str = os.getenv("SPORTS_DATA_IO_BASE_URL", "https://api.sportsdata.io/v3")
    KALSHI_RSA_PRIVATE_KEY_PATH: Optional[str] = os.getenv("KALSHI_RSA_PRIVATE_KEY_PATH")

    # --- Twitter / X filtered stream ---
    SPORTS_TWITTER_STREAM_ENABLED: bool = os.getenv("SPORTS_TWITTER_STREAM_ENABLED", "false").lower() in ("true", "1", "yes")
    SPORTS_TWITTER_BEAT_REPORTER_FILE: str = os.getenv("SPORTS_TWITTER_BEAT_REPORTER_FILE", "sports/data/beat_reporters.json")
    SPORTS_TWITTER_RECONNECT_MAX_BACKOFF: int = int(os.getenv("SPORTS_TWITTER_RECONNECT_MAX_BACKOFF", "300"))

    # --- RSS polling ---
    SPORTS_RSS_POLL_INTERVAL: int = int(os.getenv("SPORTS_RSS_POLL_INTERVAL", "60"))

    # --- Reddit polling (Phase 3) ---
    SPORTS_REDDIT_POLL_INTERVAL: int = int(os.getenv("SPORTS_REDDIT_POLL_INTERVAL", "120"))

    # --- Discord / Telegram monitoring (Phase 3) ---
    SPORTS_DISCORD_ENABLED: bool = os.getenv("SPORTS_DISCORD_ENABLED", "false").lower() in ("true", "1", "yes")
    DISCORD_BOT_TOKEN: Optional[str] = os.getenv("DISCORD_BOT_TOKEN")
    SPORTS_TELEGRAM_ENABLED: bool = os.getenv("SPORTS_TELEGRAM_ENABLED", "false").lower() in ("true", "1", "yes")
    TELEGRAM_API_ID: Optional[str] = os.getenv("TELEGRAM_API_ID")
    TELEGRAM_API_HASH: Optional[str] = os.getenv("TELEGRAM_API_HASH")

    # --- NLP injury detection ---
    INJURY_NLP_TIER: str = os.getenv("INJURY_NLP_TIER", "regex")           # regex | spacy | llm
    INJURY_LLM_CONFIDENCE_THRESHOLD: float = float(os.getenv("INJURY_LLM_CONFIDENCE_THRESHOLD", "0.70"))
    INJURY_DEDUP_WINDOW_MINUTES: int = int(os.getenv("INJURY_DEDUP_WINDOW_MINUTES", "60"))

    # --- Injury bot queue ---
    SPORTS_INJURY_MAX_EVENTS_PER_SCAN: int = int(os.getenv("SPORTS_INJURY_MAX_EVENTS_PER_SCAN", "10"))

    # --- Live game detection thresholds ---
    SPORTS_LIVE_MAX_BETS_PER_GAME: int = int(os.getenv("SPORTS_LIVE_MAX_BETS_PER_GAME", "3"))
    SPORTS_LIVE_BET_COOLDOWN_SECONDS: int = int(os.getenv("SPORTS_LIVE_BET_COOLDOWN_SECONDS", "30"))
    SPORTS_NBA_BLOWOUT_THRESHOLD: int = int(os.getenv("SPORTS_NBA_BLOWOUT_THRESHOLD", "20"))
    SPORTS_NFL_BLOWOUT_THRESHOLD: int = int(os.getenv("SPORTS_NFL_BLOWOUT_THRESHOLD", "17"))
    SPORTS_SOCCER_BLOWOUT_GOALS: int = int(os.getenv("SPORTS_SOCCER_BLOWOUT_GOALS", "2"))

    # --- Adaptive Kelly ---
    SPORTS_KELLY_DEFAULT_FRACTION: float = float(os.getenv("SPORTS_KELLY_DEFAULT_FRACTION", "0.25"))
    SPORTS_KELLY_MIN_FRACTION: float = float(os.getenv("SPORTS_KELLY_MIN_FRACTION", "0.10"))
    SPORTS_KELLY_MAX_FRACTION: float = float(os.getenv("SPORTS_KELLY_MAX_FRACTION", "0.50"))
    SPORTS_CALIBRATION_UPDATE_INTERVAL: int = int(os.getenv("SPORTS_CALIBRATION_UPDATE_INTERVAL", "3600"))

    # ══════════════════════════════════════════════════════════════════
    # ESPORTS BOT SETTINGS  (Migration 024 — LoL / CS2 / Dota 2 / Valorant)
    # ══════════════════════════════════════════════════════════════════

    # --- Bot enable flags (all disabled by default) ---
    BOT_ENABLED_ESPORTS: bool = os.getenv("BOT_ENABLED_ESPORTS", "false").lower() in ("true", "1", "yes")
    BOT_ENABLED_ESPORTS_LIVE: bool = os.getenv("BOT_ENABLED_ESPORTS_LIVE", "false").lower() in ("true", "1", "yes")
    # --- Scan intervals (seconds) ---
    SCAN_INTERVAL_ESPORTS: int = int(os.getenv("SCAN_INTERVAL_ESPORTS", "120"))
    SCAN_INTERVAL_ESPORTS_LIVE: int = int(os.getenv("SCAN_INTERVAL_ESPORTS_LIVE", "2"))

    # --- Edge / confidence thresholds ---
    ESPORTS_MIN_EDGE: float = float(os.getenv("ESPORTS_MIN_EDGE", "0.05"))
    ESPORTS_MIN_CONFIDENCE: float = float(os.getenv("ESPORTS_MIN_CONFIDENCE", "0.20"))  # S127: lowered for signal_quality dampening
    ESPORTS_EGM_D: float = float(os.getenv("ESPORTS_EGM_D", "1.2"))  # S138: 1.5→1.2 (reduce compound extremization)
    ESPORTS_SERIES_MIN_EDGE: float = float(os.getenv("ESPORTS_SERIES_MIN_EDGE", "0.10"))
    ESPORTS_SERIES_REVERSE_SWEEP_FLOOR: float = float(os.getenv("ESPORTS_SERIES_REVERSE_SWEEP_FLOOR", "0.05"))
    ESPORTS_SERIES_HEDGE_ENABLED: bool = os.getenv("ESPORTS_SERIES_HEDGE_ENABLED", "true").lower() not in ("false", "0", "no")

    # --- Exposure cap (bot-specific, same pattern as WEATHER_MAX_TOTAL_EXPOSURE_USD) ---
    ESPORTS_MAX_TOTAL_EXPOSURE_USD: float = float(os.getenv("ESPORTS_MAX_TOTAL_EXPOSURE_USD", "15000"))

    # --- Bankroll / sizing (separate Kelly pool) ---
    ESPORTS_TOTAL_CAPITAL: float = float(os.getenv("ESPORTS_TOTAL_CAPITAL", "20000.0"))  # S105: aligned to $20K
    ESPORTS_MAX_BET_USD: float = float(os.getenv("ESPORTS_MAX_BET_USD", "300.0"))
    ESPORTS_MIN_TRADE_USD: float = float(os.getenv("ESPORTS_MIN_TRADE_USD", "10.0"))
    ESPORTS_MAX_DAILY_USD: float = float(os.getenv("ESPORTS_MAX_DAILY_USD", "20000.0"))
    ESPORTS_KELLY_DEFAULT_FRACTION: float = float(os.getenv("ESPORTS_KELLY_DEFAULT_FRACTION", "0.25"))
    ESPORTS_BM_ACTIVE: bool = os.getenv("ESPORTS_BM_ACTIVE", "false").lower() == "true"

    # --- Execution ---
    ESPORTS_MAKER_FALLBACK_TIMEOUT_S: float = float(os.getenv("ESPORTS_MAKER_FALLBACK_TIMEOUT_S", "3.0"))
    ESPORTS_OBSERVATION_HOURS: int = int(os.getenv("ESPORTS_OBSERVATION_HOURS", "48"))

    # --- Model training pipeline ---
    ESPORTS_MODEL_MIN_ACCURACY: float = float(os.getenv("ESPORTS_MODEL_MIN_ACCURACY", "0.55"))
    ESPORTS_MODEL_MAX_BRIER: float = float(os.getenv("ESPORTS_MODEL_MAX_BRIER", "0.24"))
    ESPORTS_RETRAIN_INTERVAL_HOURS: int = int(os.getenv("ESPORTS_RETRAIN_INTERVAL_HOURS", "24"))
    ESPORTS_MIN_ACCURACY_TO_TRADE: float = float(os.getenv("ESPORTS_MIN_ACCURACY_TO_TRADE", "0.52"))
    ESPORTS_LOL_HEURISTIC_ENABLED: bool = os.getenv("ESPORTS_LOL_HEURISTIC_ENABLED", "true").lower() in ("true", "1", "yes")
    ESPORTS_VALIDATION_SPLIT: float = float(os.getenv("ESPORTS_VALIDATION_SPLIT", "0.2"))
    ESPORTS_MIN_LOL_SAMPLES: int = int(os.getenv("ESPORTS_MIN_LOL_SAMPLES", "50"))
    ESPORTS_MIN_CS2_SAMPLES: int = int(os.getenv("ESPORTS_MIN_CS2_SAMPLES", "100"))
    ESPORTS_MIN_CS2_UNIQUE_MATCHES: int = int(os.getenv("ESPORTS_MIN_CS2_UNIQUE_MATCHES", "15"))
    ESPORTS_EARLY_STOPPING_ROUNDS: int = int(os.getenv("ESPORTS_EARLY_STOPPING_ROUNDS", "20"))
    ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES: int = int(os.getenv("ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES", "20"))

    # --- Exposure limits (per-game/tournament/team concentration caps) ---
    # S116: raised — CS2 hitting $3K cap constantly (4 markets blocked/scan).
    # Hierarchy: team ($2K) < game ($5K) < tournament ($8K) < total ($15K) < capital ($20K)
    ESPORTS_MAX_GAME_EXPOSURE: float = float(os.getenv("ESPORTS_MAX_GAME_EXPOSURE", "5000.0"))
    ESPORTS_MAX_TOURNAMENT_EXPOSURE: float = float(os.getenv("ESPORTS_MAX_TOURNAMENT_EXPOSURE", "8000.0"))
    ESPORTS_MAX_TEAM_EXPOSURE: float = float(os.getenv("ESPORTS_MAX_TEAM_EXPOSURE", "2000.0"))

    # S136 Phase 8A: Percentage-based caps (scaled by ESPORTS_TOTAL_CAPITAL)
    # These override absolute caps when ESPORTS_PCT_CAPS_ENABLED=true
    ESPORTS_PCT_CAPS_ENABLED: bool = os.getenv("ESPORTS_PCT_CAPS_ENABLED", "true").lower() in ("true", "1", "yes")
    ESPORTS_PCT_PER_TRADE: float = float(os.getenv("ESPORTS_PCT_PER_TRADE", "0.015"))  # 1.5%
    ESPORTS_PCT_PER_MARKET: float = float(os.getenv("ESPORTS_PCT_PER_MARKET", "0.03"))  # 3%
    ESPORTS_PCT_PER_TEAM: float = float(os.getenv("ESPORTS_PCT_PER_TEAM", "0.03"))  # 3%
    ESPORTS_PCT_PER_GAME: float = float(os.getenv("ESPORTS_PCT_PER_GAME", "0.04"))  # 4%
    ESPORTS_PCT_PER_TOURNAMENT: float = float(os.getenv("ESPORTS_PCT_PER_TOURNAMENT", "0.12"))  # 12%
    ESPORTS_PCT_TOTAL_PORTFOLIO: float = float(os.getenv("ESPORTS_PCT_TOTAL_PORTFOLIO", "0.60"))  # 60%

    # --- External API keys (esports data enrichment) ---
    ALIGULAC_API_KEY: str = os.getenv("ALIGULAC_API_KEY", "")
    ODDSPAPI_API_KEY: str = os.getenv("ODDSPAPI_API_KEY", "")
    BALLCHASING_API_KEY: str = os.getenv("BALLCHASING_API_KEY", "")

    ESPORTS_REENTRY_MIN_EDGE: float = float(os.getenv("ESPORTS_REENTRY_MIN_EDGE", "0.08"))
    ESPORTS_PER_MARKET_CAP: float = float(os.getenv("ESPORTS_PER_MARKET_CAP", "600"))
    ESPORTS_FRESHNESS_DECAY_SECONDS: float = float(os.getenv("ESPORTS_FRESHNESS_DECAY_SECONDS", "120.0"))
    ESPORTS_FRESHNESS_DECAY_PREGAME_SECONDS: float = float(os.getenv("ESPORTS_FRESHNESS_DECAY_PREGAME_SECONDS", "600.0"))
    ESPORTS_WHALE_SMART_MONEY_THRESHOLD: float = float(os.getenv("ESPORTS_WHALE_SMART_MONEY_THRESHOLD", "0.60"))

    # --- WebSocket reactive ---
    ESPORTS_WS_PRICE_CHANGE_PCT: float = float(os.getenv("ESPORTS_WS_PRICE_CHANGE_PCT", "0.01"))
    ESPORTS_WS_COOLDOWN_SECONDS: int = int(os.getenv("ESPORTS_WS_COOLDOWN_SECONDS", "10"))
    ESPORTS_LIVE_WS_PRICE_CHANGE_PCT: float = float(os.getenv("ESPORTS_LIVE_WS_PRICE_CHANGE_PCT", "0.005"))
    ESPORTS_LIVE_WS_COOLDOWN_SECONDS: int = int(os.getenv("ESPORTS_LIVE_WS_COOLDOWN_SECONDS", "5"))
    ESPORTS_SERIES_WS_PRICE_CHANGE_PCT: float = float(os.getenv("ESPORTS_SERIES_WS_PRICE_CHANGE_PCT", "0.01"))
    ESPORTS_SERIES_WS_COOLDOWN_SECONDS: int = int(os.getenv("ESPORTS_SERIES_WS_COOLDOWN_SECONDS", "10"))

    # --- Live trigger ---
    ESPORTS_LIVE_COOLDOWN_SECONDS: float = float(os.getenv("ESPORTS_LIVE_COOLDOWN_SECONDS", "60.0"))
    ESPORTS_LIVE_MAX_PER_MATCH: int = int(os.getenv("ESPORTS_LIVE_MAX_PER_MATCH", "5"))
    ESPORTS_LIVE_MAX_PER_MAP: int = int(os.getenv("ESPORTS_LIVE_MAX_PER_MAP", "2"))
    ESPORTS_LIVE_MAX_EVENTS_PER_SCAN: int = int(os.getenv("ESPORTS_LIVE_MAX_EVENTS_PER_SCAN", "50"))
    ESPORTS_LIVE_EVENT_MAX_AGE_SECONDS: float = float(os.getenv("ESPORTS_LIVE_EVENT_MAX_AGE_SECONDS", "60.0"))

    # --- Game monitor ---
    ESPORTS_MONITOR_BASE_BACKOFF: int = int(os.getenv("ESPORTS_MONITOR_BASE_BACKOFF", "30"))
    ESPORTS_MONITOR_MAX_BACKOFF: int = int(os.getenv("ESPORTS_MONITOR_MAX_BACKOFF", "300"))
    ESPORTS_MONITOR_POLL_INTERVAL: int = int(os.getenv("ESPORTS_MONITOR_POLL_INTERVAL", "15"))

    # --- Latency tracking ---
    ESPORTS_PANDASCORE_REFRESH_INTERVAL: int = int(os.getenv("ESPORTS_PANDASCORE_REFRESH_INTERVAL", "15"))
    ESPORTS_PANDASCORE_TIMEOUT: float = float(os.getenv("ESPORTS_PANDASCORE_TIMEOUT", "5.0"))
    ESPORTS_SERIES_REFRESH_INTERVAL: int = int(os.getenv("ESPORTS_SERIES_REFRESH_INTERVAL", "30"))

    # --- PandaScore rate limits (configurable for paid tier upgrade) ---
    PANDASCORE_RATE_LIMIT_PER_HOUR: int = int(os.getenv("PANDASCORE_RATE_LIMIT_PER_HOUR", "1000"))
    PANDASCORE_CIRCUIT_BREAKER_BUFFER: int = int(os.getenv("PANDASCORE_CIRCUIT_BREAKER_BUFFER", "50"))
    PANDASCORE_USE_WEBSOCKET: bool = os.getenv("PANDASCORE_USE_WEBSOCKET", "false").lower() in ("true", "1", "yes")

    # --- API keys (PANDASCORE required — bots fail fast if missing) ---
    PANDASCORE_API_KEY: Optional[str] = os.getenv("PANDASCORE_API_KEY")
    RIOT_API_KEY: Optional[str] = os.getenv("RIOT_API_KEY")

    # --- Per-game thresholds (LoL) ---
    ESPORTS_LOL_GOLD_DIFF_THRESHOLD: int = int(os.getenv("ESPORTS_LOL_GOLD_DIFF_THRESHOLD", "5000"))
    ESPORTS_LOL_TOWER_DIFF_THRESHOLD: int = int(os.getenv("ESPORTS_LOL_TOWER_DIFF_THRESHOLD", "3"))

    # --- Per-game thresholds (CS2) ---
    ESPORTS_CS2_ROUND_DIFF_THRESHOLD: int = int(os.getenv("ESPORTS_CS2_ROUND_DIFF_THRESHOLD", "5"))
    # --- Risk guardrails (A1+A8: daily loss limit + drawdown halt) ---
    # Paper-trading defaults — loose enough for training. Tighten via env for live.
    ESPORTS_DAILY_LOSS_LIMIT: float = float(os.getenv("ESPORTS_DAILY_LOSS_LIMIT", "10000.0"))  # S105: aligned to $10K (matching max_daily_usd)
    ESPORTS_DRAWDOWN_HALT_PCT: float = float(os.getenv("ESPORTS_DRAWDOWN_HALT_PCT", "0.40"))
    ESPORTS_DRAWDOWN_REDUCE_PCT: float = float(os.getenv("ESPORTS_DRAWDOWN_REDUCE_PCT", "0.20"))

    # --- Stop-loss (B1) ---
    # 25% stop-loss + 96h hold — esports resolve fast (24-48h), these are safety nets.
    ESPORTS_STOP_LOSS_PCT: float = float(os.getenv("ESPORTS_STOP_LOSS_PCT", "0.25"))
    ESPORTS_MAX_HOLD_HOURS: float = float(os.getenv("ESPORTS_MAX_HOLD_HOURS", "96"))
    # S109: Post-exit cooldown (seconds) — prevents stop-loss churn (RC1)
    ESPORTS_EXIT_COOLDOWN_SECONDS: float = float(os.getenv("ESPORTS_EXIT_COOLDOWN_SECONDS", "300.0"))
    # S138: Extended cooldown after edge_gone exit — prevents churn loop
    ESPORTS_EDGE_GONE_COOLDOWN_SECONDS: float = float(os.getenv("ESPORTS_EDGE_GONE_COOLDOWN_SECONDS", "1800.0"))
    # S138: Minimum hold time (minutes) before edge_gone exit can fire
    ESPORTS_MIN_HOLD_MINUTES: float = float(os.getenv("ESPORTS_MIN_HOLD_MINUTES", "10.0"))
    # S135: Cooldown (seconds) after execution failure — stops spam-retrying dead markets
    ESPORTS_EXEC_FAIL_COOLDOWN_S: float = float(os.getenv("ESPORTS_EXEC_FAIL_COOLDOWN_S", "300"))
    # S109: Max entries per market per rolling window — backstop against churn (RC3)
    ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW: int = int(os.getenv("ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW", "2"))
    ESPORTS_ENTRY_WINDOW_HOURS: float = float(os.getenv("ESPORTS_ENTRY_WINDOW_HOURS", "12.0"))

    # --- Hard game disable (comma-separated lowercase game names, e.g. "cod,r6") ---
    ESPORTS_DISABLED_GAMES: str = os.getenv("ESPORTS_DISABLED_GAMES", "")

    # S135: Max model-market divergence — reject when abs(model_prob - market_price) exceeds this.
    # Data: >0.30 divergence = 5.4% accuracy (2/37), ≤0.15 = 74.4% (29/39).
    ESPORTS_MAX_MODEL_DIVERGENCE: float = float(os.getenv("ESPORTS_MAX_MODEL_DIVERGENCE", "0.25"))

    # --- Monitoring halt threshold (Brier score above this → halt trading for game) ---
    ESPORTS_BRIER_HALT_THRESHOLD: float = float(os.getenv("ESPORTS_BRIER_HALT_THRESHOLD", "0.30"))  # S127: lowered to actually halt bad games
    # S142: Statistical halt lower bound — 90% CI lower bound must exceed this to halt.
    # Lowered from 0.25 to 0.22 so CS2 (Brier 0.339, lb=0.249) actually gets caught.
    ESPORTS_BRIER_HALT_LOWER_BOUND: float = float(os.getenv("ESPORTS_BRIER_HALT_LOWER_BOUND", "0.22"))

    # --- Per-game Kelly multiplier thresholds ---
    ESPORTS_KELLY_BRIER_PENALTY: float = float(os.getenv("ESPORTS_KELLY_BRIER_PENALTY", "0.25"))
    ESPORTS_KELLY_BRIER_BOOST: float = float(os.getenv("ESPORTS_KELLY_BRIER_BOOST", "0.20"))
    ESPORTS_KELLY_MAX_FRACTION: float = float(os.getenv("ESPORTS_KELLY_MAX_FRACTION", "0.35"))
    ESPORTS_KELLY_DEGRADE_BRIER: float = float(os.getenv("ESPORTS_KELLY_DEGRADE_BRIER", "0.28"))

    # --- Parallel analysis ---
    ESPORTS_ANALYSIS_CONCURRENCY: int = int(os.getenv("ESPORTS_ANALYSIS_CONCURRENCY", "25"))

    # --- Conformal prediction (Session 83) ---
    ESPORTS_CONFORMAL_ALPHA: float = float(os.getenv("ESPORTS_CONFORMAL_ALPHA", "0.10"))  # 90% prediction interval

    # --- CoT validation (Session 83) ---
    ESPORTS_COT_EDGE_THRESHOLD: float = float(os.getenv("ESPORTS_COT_EDGE_THRESHOLD", "0.15"))
    ESPORTS_COT_MAX_PER_SCAN: int = int(os.getenv("ESPORTS_COT_MAX_PER_SCAN", "3"))

    # --- Stale match detection (E3) ---
    ESPORTS_STALE_MATCH_SECONDS: int = int(os.getenv("ESPORTS_STALE_MATCH_SECONDS", "1800"))  # 30 min

    # --- Live polling timeout ---
    ESPORTS_LIVE_POLL_TIMEOUT: int = int(os.getenv("ESPORTS_LIVE_POLL_TIMEOUT", "10"))  # seconds

    # --- Market-price fallback for unknown teams ---
    ESPORTS_MARKET_FALLBACK_ENABLED: bool = os.getenv("ESPORTS_MARKET_FALLBACK_ENABLED", "true").lower() in ("true", "1", "yes")
    ESPORTS_MARKET_FALLBACK_MIN_EDGE: float = float(os.getenv("ESPORTS_MARKET_FALLBACK_MIN_EDGE", "0.15"))

    # --- Conformal prediction ---
    ESPORTS_USE_CONFORMAL: bool = os.getenv("ESPORTS_USE_CONFORMAL", "false").lower() in ("true", "1", "yes")
    ESPORTS_CONFORMAL_MIN_RESOLVED: int = int(os.getenv("ESPORTS_CONFORMAL_MIN_RESOLVED", "50"))
    # S138: Enabled at 0.03 — compresses extreme favorite predictions. A/B logged.
    ESPORTS_RFLB_STRENGTH: float = float(os.getenv("ESPORTS_RFLB_STRENGTH", "0.03"))
    ESPORTS_LAN_ADJUSTMENT_ENABLED: bool = os.getenv("ESPORTS_LAN_ADJUSTMENT_ENABLED", "true").lower() in ("true", "1", "yes")
    ESPORTS_LOL_BLUE_SIDE_BONUS: float = float(os.getenv("ESPORTS_LOL_BLUE_SIDE_BONUS", "0.019"))
    ESPORTS_UPSET_RISK_ENABLED: bool = os.getenv("ESPORTS_UPSET_RISK_ENABLED", "true").lower() in ("true", "1", "yes")
    ESPORTS_ROSTER_CHANGE_PENALTY: float = float(os.getenv("ESPORTS_ROSTER_CHANGE_PENALTY", "0.15"))
    ESPORTS_ROSTER_CHANGE_DECAY_DAYS: int = int(os.getenv("ESPORTS_ROSTER_CHANGE_DECAY_DAYS", "7"))

    # --- S136: Per-game Glicko-2 tau ---
    ESPORTS_GLICKO2_TAU_CS2: float = float(os.getenv("ESPORTS_GLICKO2_TAU_CS2", "0.45"))
    ESPORTS_GLICKO2_TAU_LOL: float = float(os.getenv("ESPORTS_GLICKO2_TAU_LOL", "0.55"))
    ESPORTS_GLICKO2_TAU_DOTA2: float = float(os.getenv("ESPORTS_GLICKO2_TAU_DOTA2", "0.50"))
    ESPORTS_GLICKO2_TAU_VALORANT: float = float(os.getenv("ESPORTS_GLICKO2_TAU_VALORANT", "0.50"))
    ESPORTS_GLICKO2_TAU_SC2: float = float(os.getenv("ESPORTS_GLICKO2_TAU_SC2", "0.70"))
    ESPORTS_GLICKO2_TAU_DEFAULT: float = float(os.getenv("ESPORTS_GLICKO2_TAU_DEFAULT", "0.50"))

    # --- Retention ---
    ESPORTS_TRAINING_RETENTION_DAYS: int = int(os.getenv("ESPORTS_TRAINING_RETENTION_DAYS", "365"))
    ESPORTS_PREDICTION_RETENTION_DAYS: int = int(os.getenv("ESPORTS_PREDICTION_RETENTION_DAYS", "180"))

    # --- Draft feature engineering (Session B) ---
    ESPORTS_DRAFT_FEATURES_ENABLED: bool = os.getenv("ESPORTS_DRAFT_FEATURES_ENABLED", "true").lower() in ("true", "1", "yes")
    ESPORTS_DRAFT_MIN_SAMPLES: int = int(os.getenv("ESPORTS_DRAFT_MIN_SAMPLES", "20"))
    ESPORTS_DRAFT_SYNERGY_MIN_COOCCUR: int = int(os.getenv("ESPORTS_DRAFT_SYNERGY_MIN_COOCCUR", "5"))

    # --- CatBoost draft model (Session C) ---
    ESPORTS_CATBOOST_ENABLED: bool = os.getenv("ESPORTS_CATBOOST_ENABLED", "false").lower() in ("true", "1", "yes")
    ESPORTS_CATBOOST_MIN_SAMPLES: int = int(os.getenv("ESPORTS_CATBOOST_MIN_SAMPLES", "200"))
    ESPORTS_CATBOOST_BLEND_WEIGHT: float = float(os.getenv("ESPORTS_CATBOOST_BLEND_WEIGHT", "0.4"))
    ESPORTS_CATBOOST_RETRAIN_HOURS: int = int(os.getenv("ESPORTS_CATBOOST_RETRAIN_HOURS", "24"))

    # --- CLV-gated position scaling (WS2) ---
    ESPORTS_CLV_SCALING_ENABLED: bool = os.getenv("ESPORTS_CLV_SCALING_ENABLED", "false").lower() in ("true", "1", "yes")
    ESPORTS_SCALE_CONSERVATIVE_MAX_BET: float = float(os.getenv("ESPORTS_SCALE_CONSERVATIVE_MAX_BET", "100.0"))
    ESPORTS_SCALE_MODERATE_MAX_BET: float = float(os.getenv("ESPORTS_SCALE_MODERATE_MAX_BET", "200.0"))
    ESPORTS_SCALE_AGGRESSIVE_MAX_BET: float = float(os.getenv("ESPORTS_SCALE_AGGRESSIVE_MAX_BET", "300.0"))
    ESPORTS_SCALE_CONSERVATIVE_DAILY: float = float(os.getenv("ESPORTS_SCALE_CONSERVATIVE_DAILY", "500.0"))
    ESPORTS_SCALE_MODERATE_DAILY: float = float(os.getenv("ESPORTS_SCALE_MODERATE_DAILY", "2000.0"))
    ESPORTS_SCALE_AGGRESSIVE_DAILY: float = float(os.getenv("ESPORTS_SCALE_AGGRESSIVE_DAILY", "5000.0"))

    # --- Series draft adjustment (Session D) ---
    ESPORTS_SERIES_DRAFT_ADJUST_ENABLED: bool = os.getenv("ESPORTS_SERIES_DRAFT_ADJUST_ENABLED", "false").lower() in ("true", "1", "yes")
    ESPORTS_SERIES_DRAFT_BLEND_WEIGHT: float = float(os.getenv("ESPORTS_SERIES_DRAFT_BLEND_WEIGHT", "0.3"))

    # --- Unknown team backfill budget (WS7) ---
    ESPORTS_MAX_BACKFILLS_PER_SCAN: int = int(os.getenv("ESPORTS_MAX_BACKFILLS_PER_SCAN", "10"))

    # --- Pinnacle / cross-market (Phase 2 — deferred) ---
    ESPORTS_PINNACLE_ENABLED: bool = os.getenv("ESPORTS_PINNACLE_ENABLED", "false").lower() in ("true", "1", "yes")

    # ══════════════════════════════════════════════════════════════════
    # ELITE MODEL ELEVATION — Deep Dive Roadmap Items
    # ══════════════════════════════════════════════════════════════════

    # --- P0: Polling Data Pipeline ---
    VOTEHUB_API_KEY: Optional[str] = os.getenv("VOTEHUB_API_KEY")
    POLLING_POLL_INTERVAL_SECONDS: int = int(os.getenv("POLLING_POLL_INTERVAL_SECONDS", "3600"))  # 1h

    # --- P2: Legislative Intelligence ---
    CONGRESS_GOV_API_KEY: Optional[str] = os.getenv("CONGRESS_GOV_API_KEY")
    PROPUBLICA_API_KEY: Optional[str] = os.getenv("PROPUBLICA_API_KEY")
    LEGISLATIVE_POLL_INTERVAL_SECONDS: int = int(os.getenv("LEGISLATIVE_POLL_INTERVAL_SECONDS", "1800"))  # 30min

    # --- P3: Court & Executive Monitoring ---
    COURTLISTENER_API_TOKEN: Optional[str] = os.getenv("COURTLISTENER_API_TOKEN")
    COURT_MONITOR_POLL_INTERVAL_SECONDS: int = int(os.getenv("COURT_MONITOR_POLL_INTERVAL_SECONDS", "1800"))  # 30min

    # --- P3: International Elections ---
    INTL_ELECTIONS_POLL_INTERVAL_SECONDS: int = int(os.getenv("INTL_ELECTIONS_POLL_INTERVAL_SECONDS", "43200"))  # 12h

    # --- P1: Multi-LLM Consensus ---
    # "fallback" = sequential (cheapest), "parallel_vote" = majority vote, "median" = median probability
    LLM_CONSENSUS_MODE: str = os.getenv("LLM_CONSENSUS_MODE", "fallback")

    # --- P1: Cross-Market Logical Arbitrage ---
    LOGICAL_ARB_ENABLED: bool = os.getenv("LOGICAL_ARB_ENABLED", "false").lower() in ("true", "1", "yes")
    LOGICAL_ARB_SCAN_INTERVAL_SECONDS: int = int(os.getenv("LOGICAL_ARB_SCAN_INTERVAL_SECONDS", "300"))  # 5min
    LOGICAL_ARB_MIN_SPREAD: float = float(os.getenv("LOGICAL_ARB_MIN_SPREAD", "0.025"))
    LOGICAL_ARB_MAX_POSITION_USD: float = float(os.getenv("LOGICAL_ARB_MAX_POSITION_USD", "200"))
    BOT_ENABLED_LOGICAL_ARB: bool = os.getenv("BOT_ENABLED_LOGICAL_ARB", "false").lower() in ("true", "1", "yes")
    SCAN_INTERVAL_LOGICAL_ARB: int = int(os.getenv("SCAN_INTERVAL_LOGICAL_ARB", "300"))  # 5min, mirrors LOGICAL_ARB_SCAN_INTERVAL_SECONDS

    # --- P2: PCA Correlation Clusters ---
    RISK_MAX_FACTOR_EXPOSURE_USD: float = float(os.getenv("RISK_MAX_FACTOR_EXPOSURE_USD", "500.0"))
    PCA_LOOKBACK_DAYS: int = int(os.getenv("PCA_LOOKBACK_DAYS", "30"))
    PCA_N_FACTORS: int = int(os.getenv("PCA_N_FACTORS", "3"))

    # --- P2: Time-Horizon Capital Bucketing ---
    BUCKET_SHORT_TERM_PCT: float = float(os.getenv("BUCKET_SHORT_TERM_PCT", "0.40"))   # <30 days
    BUCKET_MEDIUM_TERM_PCT: float = float(os.getenv("BUCKET_MEDIUM_TERM_PCT", "0.35"))  # 30-180 days
    BUCKET_LONG_TERM_PCT: float = float(os.getenv("BUCKET_LONG_TERM_PCT", "0.05"))     # >180 days
    BUCKET_LIQUID_RESERVE_PCT: float = float(os.getenv("BUCKET_LIQUID_RESERVE_PCT", "0.20"))

    # --- P2: Bayesian Polling Model ---
    BAYESIAN_MODEL_ENABLED: bool = os.getenv("BAYESIAN_MODEL_ENABLED", "false").lower() in ("true", "1", "yes")
    BAYESIAN_FUNDAMENTALS_GDP_Q2: float = float(os.getenv("BAYESIAN_FUNDAMENTALS_GDP_Q2", "2.0"))
    BAYESIAN_FUNDAMENTALS_APPROVAL: float = float(os.getenv("BAYESIAN_FUNDAMENTALS_APPROVAL", "45.0"))
    BAYESIAN_FUNDAMENTALS_FIRST_TERM: bool = os.getenv("BAYESIAN_FUNDAMENTALS_FIRST_TERM", "true").lower() in ("true", "1", "yes")

    @model_validator(mode="after")
    def _warn_deprecated_keys(self) -> "Settings":
        """Warn if deprecated env vars are set."""
        if os.environ.get("WEEKLY_FULL_REFRESH_DAY"):
            warnings.warn(
                "WEEKLY_FULL_REFRESH_DAY is deprecated and ignored. Use WEEKLY_FULL_INGESTION_WEEKDAY instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return self

    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="allow",  # Allow extra fields like SIMULATION_MODE
    )


settings = Settings()
