"""
EnsembleBot — Primary directional trading bot for all market categories.

ML-driven predictions with calibration, adaptive confidence, model disagreement penalty,
LLM probability nudge, and signal enhancements.

Absorbs category-specific logic from the former CryptoPoliticalBot:
  - Category filtering (crypto, politics, sports, etc.)
  - 24h trade sentiment as an additional feature
  - Event calendar confidence boost (1.05x when scheduled event within 6h)
"""
import asyncio
import math
import time
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from structlog import get_logger
from bots.base_bot import BaseBot, get_end_date_from_dict
from base_engine.base_engine import BaseEngine
from config.settings import settings

logger = get_logger()

# Model staleness: skip taking new trades if models older than this (hours)
ENSEMBLE_MODEL_STALENESS_HOURS = 24.0
# I13: Model disagreement constants read from settings (single source of truth).
# Defaults preserved: threshold=0.20, penalty=0.15.
# Override via ENSEMBLE_DISAGREEMENT_THRESHOLD / ENSEMBLE_DISAGREEMENT_PENALTY env vars.
ENSEMBLE_DISAGREEMENT_THRESHOLD: float = getattr(settings, "ENSEMBLE_DISAGREEMENT_THRESHOLD", 0.20)
ENSEMBLE_DISAGREEMENT_PENALTY: float = getattr(settings, "ENSEMBLE_DISAGREEMENT_PENALTY", 0.15)

# Sentiment (absorbed from CryptoPoliticalBot)
SENTIMENT_CACHE_TTL = 600
SENTIMENT_CACHE_MAX_SIZE = 500
SENTIMENT_NEUTRAL_THRESHOLD = 0.05
SENTIMENT_MIN_TRADE_COUNT = 15


def _infer_side_from_token(market_data: Dict, token_id: str) -> Optional[str]:
    """Infer YES/NO from market_data. Returns 'YES', 'NO', or None if unknown."""
    yes_tid = (market_data.get("yes_token_id") or market_data.get("yesTokenId") or "").strip()
    no_tid = (market_data.get("no_token_id") or market_data.get("noTokenId") or "").strip()
    tid = (token_id or "").strip()
    if yes_tid and tid == yes_tid:
        return "YES"
    if no_tid and tid == no_tid:
        return "NO"
    # API often has tokens[0]=YES, tokens[1]=NO
    tokens = market_data.get("tokens") or []
    if isinstance(tokens, list) and len(tokens) >= 2:
        t0 = tokens[0] if isinstance(tokens[0], dict) else {}
        t1 = tokens[1] if isinstance(tokens[1], dict) else {}
        tid0 = (t0.get("tokenId") or t0.get("token_id") or "").strip()
        tid1 = (t1.get("tokenId") or t1.get("token_id") or "").strip()
        if tid == tid0:
            return "YES"
        if tid == tid1:
            return "NO"
    if len(tokens) == 1 and (tokens[0] if isinstance(tokens[0], dict) else {}).get("tokenId") == tid:
        return "YES"
    return None


class EnsembleBot(BaseBot):
    def __init__(self, base_engine: BaseEngine):
        super().__init__("EnsembleBot", base_engine)
        self.model_weights = {
            "random_forest": 0.12,
            "xgboost": 0.15,
            "gradient_boosting": 0.10,
            "extra_trees": 0.10,
            "hist_gradient_boosting": 0.12,
            "lightgbm": 0.12,
            "catboost": 0.12,
            "logistic_regression": 0.07,
            "ridge": 0.05,
            "knn": 0.05,
        }
        # I53: single source of truth — settings.py defines ENSEMBLE_MIN_CONFIDENCE (default 0.55)
        # Old code used getattr fallback of 0.65, diverging from settings.py's 0.55 default.
        self._base_min_confidence = settings.ENSEMBLE_MIN_CONFIDENCE
        self.min_consensus_confidence = self._base_min_confidence
        self.target_accuracy = 0.99
        self._last_adaptive_check = 0.0  # monotonic timestamp

        # Category filtering (absorbed from CryptoPoliticalBot)
        # Default: no filter (all categories). Set via ENSEMBLE_TARGET_CATEGORIES env.
        raw_cats = getattr(settings, "ENSEMBLE_TARGET_CATEGORIES", "")
        self.target_categories: Optional[List[str]] = (
            [c.strip().lower() for c in raw_cats.split(",") if c.strip()]
            if raw_cats else None
        )

        # Sentiment cache: market_id -> (score, expiry_monotonic)
        self._sentiment_cache: Dict[str, Tuple[float, float]] = {}

        # Delta scan tracking: only re-analyze markets with WS price changes since last scan
        self._changed_markets: set = set()
        self._last_full_scan: float = 0.0  # monotonic time of last full scan

        # Re-entry cooldown: prevent churning same market after exit
        self._recently_exited: Dict[str, float] = {}   # market_id → wall-clock unix timestamp of exit
        self._exit_count: Dict[str, int] = {}           # market_id → consecutive exit count (progressive cooldown)
        self._prev_open_markets: set = set()            # market IDs open at end of previous scan

        # Side-bias tracker: last N traded sides for bias detection
        self._recent_trade_sides: list = []  # list of "YES"/"NO" strings, max 50
        self._SIDE_BIAS_WINDOW: int = 50
        self._SIDE_BIAS_MAX_PCT: float = float(getattr(settings, "ENSEMBLE_SIDE_BIAS_THRESHOLD", 0.75))

        # L2: Category confidence multipliers from PerformanceRecord
        self._category_mults: Dict[str, float] = {}
        self._category_mults_last_refresh: float = 0.0
        self._category_mults_ttl: float = 900.0  # refresh every 15 minutes

        # Tier 2 #18: VPIN toxicity cache — token_id → (result_dict, expires_monotonic)
        self._vpin_cache: Dict[str, Tuple[Dict, float]] = {}

        # Tier 2 #19: Wallet clustering — last refresh timestamp (monotonic)
        self._wallet_cluster_last_refresh: float = 0.0
        self._WALLET_CLUSTER_REFRESH_INTERVAL: float = 1800.0  # refresh every 30 min

        # Tier 2 #20: Order flow cache — market_id → (result_dict, expires_monotonic)
        self._order_flow_cache: Dict[str, Tuple[Dict, float]] = {}

    def _on_bg_task_done(self, task, name):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("bg_task_failed", task_name=name, error=str(exc))

    async def start(self) -> None:
        """
        I04: Reset feature cache state on restart to prevent warm-skip loop.

        Without this, if prediction_engine._feature_cache_warmed was left True from a
        prior session (stale vectors), the bot would run predictions on old features
        indefinitely. On restart: reset warm flag + clear vector cache so the next
        scan triggers a fresh warm pass.
        """
        pe = getattr(self.base_engine, "prediction_engine", None)
        if pe is not None:
            pe._feature_cache_warmed = False
            pe._feature_cache_warming_task_started = False
            pe._warm_fail_count = 0  # I04: reset retry counter on restart
            if hasattr(pe, "_feature_vector_cache"):
                pe._feature_vector_cache.clear()
            logger.info("EnsembleBot: I04 — feature cache cleared and warm flag reset on start()")
        await super().start()

    async def _refresh_category_mults(self) -> None:
        """L2: Refresh category confidence multipliers from PerformanceTracker (every 15 min)."""
        now = time.monotonic()
        if now - self._category_mults_last_refresh < self._category_mults_ttl:
            return
        self._category_mults_last_refresh = now
        try:
            pt = getattr(self.base_engine, "performance_tracker", None)
            if pt and hasattr(pt, "get_category_confidence_multipliers"):
                self._category_mults = await pt.get_category_confidence_multipliers()
        except Exception as e:
            logger.debug("L2: category multiplier refresh failed: %s", e)

    async def _adapt_min_confidence(self) -> None:
        """Adjust min_consensus_confidence based on recent prediction accuracy.

        T6/K6 FIX: Exponential smoothing prevents oscillation between aggressive/defensive.
        Hysteresis bands: separate thresholds for loosening (0.68) vs tightening (0.42).
        Only uses predictions from last 48h (not all-time) to avoid cold-start amplification.
        Min sample count of 20 prevents over-fitting to small samples.
        """
        now = time.monotonic()
        if now - self._last_adaptive_check < 300:  # re-check every 5 minutes max
            return
        self._last_adaptive_check = now
        db = getattr(self.base_engine, "db", None)
        if not db or not db.session_factory:
            return
        try:
            perf = await db.get_recent_brier_from_prediction_log(
                getattr(settings, "AUTO_RETRAIN_RECENT_N", 50)
            )
            # Need at least 20 resolved predictions to adapt (prevents cold-start over-tightening)
            if not perf or perf.get("count", 0) < 20:
                self.min_consensus_confidence = self._base_min_confidence
                return
            accuracy = perf.get("accuracy", 0.5)
            base = self._base_min_confidence

            # Compute raw target with hysteresis bands (different thresholds for up/down)
            current = self.min_consensus_confidence
            # Floor/ceiling respect the configured base — don't hardcode 0.55 which would override
            # the .env ENSEMBLE_MIN_CONFIDENCE setting (e.g. 0.45 for learning mode).
            _floor = max(base - 0.10, base * 0.85)  # at most 15% below base (or base-0.10, whichever is higher)
            _ceiling = min(base + 0.20, 0.90)        # at most 20% above base
            if accuracy >= 0.68:
                # Loosen only when clearly above 0.68 (not 0.65 — hysteresis gap)
                target = _floor
            elif accuracy < 0.42:
                # Tighten only when clearly below 0.42 (not 0.45 — hysteresis gap)
                target = _ceiling
            else:
                # Dead zone: no change pressure — drift toward base
                target = base

            # Exponential smoothing: 80% old + 20% new to prevent oscillation
            _smoothing = 0.20
            self.min_consensus_confidence = current * (1.0 - _smoothing) + target * _smoothing
            # Clamp to valid range anchored on base (not hardcoded 0.55)
            self.min_consensus_confidence = max(_floor, min(_ceiling, self.min_consensus_confidence))

            logger.debug(
                "EnsembleBot adaptive confidence: acc=%.2f target=%.2f smoothed=%.3f",
                accuracy, target, self.min_consensus_confidence,
            )
        except Exception as e:
            logger.debug("Adaptive confidence check failed: %s", e)

    def _is_prediction_engine_ready(self) -> bool:
        """Return True if prediction engine is initialized so we don't spam failed predictions."""
        pe = getattr(self.base_engine, "prediction_engine", None)
        if not pe:
            return False
        return getattr(pe, "initialized", False) and getattr(pe, "models", None)

    def _is_model_stale(self) -> bool:
        """Return True if models are too old; avoid trading on stale models."""
        pe = getattr(self.base_engine, "prediction_engine", None)
        if not pe:
            return True
        last = getattr(pe, "last_trained_at", None)
        if not last:
            return True  # P2-3: No training timestamp = model is untrained/stale, not fresh
        now = datetime.now(timezone.utc)
        if getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=timezone.utc)
        age_hours = (now - last).total_seconds() / 3600
        return age_hours > ENSEMBLE_MODEL_STALENESS_HOURS

    async def _has_open_position_on_market(self, market_id: str) -> bool:
        """True if this bot already has an open position on this market (skip re-entry)."""
        db = getattr(self.base_engine, "db", None)
        if not db or not getattr(db, "session_factory", None):
            return False
        try:
            from sqlalchemy import select
            from base_engine.data.database import Position
            from sqlalchemy import or_
            async with db.get_session() as session:
                r = await session.execute(
                    select(Position.id).where(
                        or_(
                            Position.bot_id == self.bot_name,
                            Position.source_bot == self.bot_name,
                        ),
                        Position.market_id == market_id,
                        Position.status == "open",
                    ).limit(1)
                )
                return r.scalar_one_or_none() is not None
        except Exception as e:
            logger.debug("has_open_position_on_market failed: %s", e)
            return False

    # ── I46: Centralised market-tradeable gate ────────────────────────────

    def _check_market_tradeable(self, market: Dict) -> Optional[str]:
        """Return a gate-name string (reason) if this market should be skipped, else None.

        I46: Centralises the 7+ scattered rejection checks so metrics can reveal which
        gate fires most often. Log at INFO for any rejection (was silent DEBUG or implicit
        `continue`).  Only checks static/synchronous properties; async gates (open-position
        lookup, cooldown) are left in-place because they require await.
        """
        market_id = market.get("id")
        if not market_id:
            return "missing_id"

        # Gate 1 — inactive or closed
        if not market.get("active", True) or market.get("closed", False):
            return "inactive_or_closed"

        # Gate 2 — past end date
        _end = get_end_date_from_dict(market)
        if _end:
            try:
                _end_dt = datetime.fromisoformat(str(_end).replace("Z", "+00:00"))
                if _end_dt.tzinfo is None:
                    _end_dt = _end_dt.replace(tzinfo=timezone.utc)
                if _end_dt < datetime.now(timezone.utc):
                    return "end_date_past"
            except (ValueError, TypeError):
                pass

        # Gate 3 — stale model (models older than ENSEMBLE_MODEL_STALENESS_HOURS)
        if self._is_model_stale():
            return "stale_models"

        return None  # Market passes all sync gates

    # ── Sentiment (absorbed from CryptoPoliticalBot) ──────────────────────

    def _sentiment_cache_get(self, market_id: str) -> Optional[float]:
        now = time.monotonic()
        if market_id in self._sentiment_cache:
            score, expiry = self._sentiment_cache[market_id]
            if now < expiry:
                return score
            del self._sentiment_cache[market_id]
        return None

    def _sentiment_cache_set(self, market_id: str, score: float) -> None:
        now = time.monotonic()
        ttl = getattr(settings, "SENTIMENT_CACHE_TTL_SECONDS", SENTIMENT_CACHE_TTL)
        max_size = getattr(settings, "SENTIMENT_CACHE_MAX_SIZE", SENTIMENT_CACHE_MAX_SIZE)
        while len(self._sentiment_cache) >= max_size and self._sentiment_cache:
            oldest_key = next(iter(self._sentiment_cache))
            del self._sentiment_cache[oldest_key]
        self._sentiment_cache[market_id] = (score, now + ttl)

    async def _calculate_sentiment(self, market_id: str) -> Optional[float]:
        """Calculate 24h trade sentiment for a market. Returns -1..+1 or None."""
        cached = self._sentiment_cache_get(market_id)
        if cached is not None:
            return cached
        try:
            db = getattr(self.base_engine, "db", None)
            if not db or not getattr(db, "session_factory", None):
                return None
            async with db.get_session() as session:
                from sqlalchemy import text
                min_trades = getattr(settings, "SENTIMENT_MIN_TRADE_COUNT", SENTIMENT_MIN_TRADE_COUNT)
                query = text("""
                    SELECT
                        AVG(CASE WHEN side = 'YES' THEN 1.0 ELSE -1.0 END) as sentiment,
                        COUNT(*) as trade_count
                    FROM trades
                    WHERE market_id = :market_id
                    AND timestamp >= NOW() - INTERVAL '24 hours'
                """)
                result = await session.execute(query, {"market_id": market_id})
                row = result.fetchone()
                if row and row[1] >= min_trades:
                    sentiment_raw = row[0]
                    try:
                        sentiment = float(sentiment_raw) if sentiment_raw is not None else 0.0
                        sentiment = max(-1.0, min(1.0, sentiment))
                        self._sentiment_cache_set(market_id, sentiment)
                        return sentiment
                    except (ValueError, TypeError):
                        return None
        except Exception as e:
            logger.debug("Sentiment calculation failed: %s", e)
        return None

    # ── Event calendar boost (absorbed from CryptoPoliticalBot) ─────────

    async def _event_calendar_confidence_mult(self, market_id: str) -> float:
        """Return confidence multiplier (e.g. 1.05) when an event is within 6h."""
        try:
            sig = getattr(self.base_engine, "signal_ingestion", None)
            if not sig or not getattr(sig, "event_calendar", None):
                return 1.0
            events = await sig.event_calendar.get_upcoming_events(hours=6, market_id=market_id)
            if events:
                return 1.05
        except Exception as e:
            logger.debug("Event calendar check failed: %s", e)
        return 1.0

    # ── Tier 2 #16: Resolution clarity scoring ────────────────────────

    async def _get_resolution_clarity(self, market_data: Dict) -> float:
        """Get LLM resolution clarity score (0=ambiguous, 1=clear). Falls back to regex-only."""
        try:
            rra = getattr(self.base_engine, "resolution_risk_analyzer", None)
            if not rra:
                return 1.0  # No analyzer = assume clear (no penalty)
            mid = str(market_data.get("id") or "")
            if not mid:
                return 1.0
            # Short-circuit: return cached score without a DB query if already scored
            from datetime import datetime, timezone as _tz
            cached = rra._clarity_cache.get(mid)
            if cached is not None:
                score, ts = cached
                import os as _os
                ttl = float(_os.getenv("RESOLUTION_CLARITY_CACHE_TTL_HOURS", "24")) * 3600
                if (datetime.now(_tz.utc) - ts).total_seconds() < ttl:
                    return score
            # Cache miss — need full ORM object for LLM prompt
            db = getattr(self.base_engine, "db", None)
            if not db:
                return 1.0
            from base_engine.data.database import Market
            from sqlalchemy import select
            async with db.get_session() as session:
                result = await session.execute(
                    select(Market).where(Market.id == mid).limit(1)
                )
                market_obj = result.scalar_one_or_none()
            if not market_obj:
                return 1.0
            return await rra.analyze_llm_clarity(market_obj)
        except Exception as e:
            logger.debug("Resolution clarity check failed: %s", e)
            return 1.0  # Default: no penalty on failure

    # ── Tier 2 #18: VPIN toxicity detection ─────────────────────────

    async def _get_vpin_toxicity(self, token_id: str) -> Dict:
        """
        Get VPIN toxicity for a token. Returns {vpin, toxic, trade_count, large_trade_pct}.

        B3: Large-trade concentration score added (Ng et al. SSRN 2025 — large trades are
        the best Polymarket proxy for informed flow). If large_trade_pct > 0.10 AND VPIN < 0.5,
        apply 0.85× confidence mult downstream (informed flow missed by VPIN clock).

        B3 FIX: Was reading nonexistent `tfa._recent_trades` attribute (always empty dict).
        Now calls `tfa.analyze_recent_trades()` directly to get large_trade data.
        large_trade_pct = count(large_trades returned) / trade_count.
        TradeFlowAnalyzer.analyze_recent_trades() defines large_trades as the top-10%
        by size (90th-percentile threshold), so large_trade_pct ≈ 0.10 for normal markets.
        B3 informed-flow signal triggers when large_trade_pct > 0.10 AND VPIN < 0.5
        (elevated large-trade concentration with low VPIN clock → hidden informed flow).

        Result is cached for 60 seconds to avoid repeated DB queries per scan cycle.
        """
        _VPIN_CACHE_TTL = 60.0
        _fallback = {"vpin": 0.0, "toxic": False, "trade_count": 0, "large_trade_pct": 0.0}
        try:
            tfa = getattr(self.base_engine, "trade_flow_analyzer", None)
            if not tfa:
                return _fallback

            # Cache check — 60s TTL
            _now_m = time.monotonic()
            _cached = self._vpin_cache.get(token_id)
            if _cached is not None:
                _cresult, _cexpires = _cached
                if _now_m < _cexpires:
                    return _cresult

            # Get VPIN metric
            result = await tfa.get_vpin(token_id, minutes=60)

            # B3 FIX: Get large_trade data from analyze_recent_trades (not tfa._recent_trades)
            try:
                flow_data = await tfa.analyze_recent_trades(token_id, minutes=60)
                _tc = flow_data.get("trade_count", 0)
                _large_list = flow_data.get("large_trades", [])  # top-10% by size (up to 10 entries)
                _avg = flow_data.get("avg_trade_size", 0.0)
                if _tc >= 5 and _large_list and _avg > 0:
                    # large_trade_pct: fraction of trades in top-10% size band
                    # In a normal market ≈ 0.10; elevated (>0.10) = right-skewed concentration
                    _large_trade_pct = min(1.0, len(_large_list) / max(_tc, 1))
                    result["large_trade_pct"] = round(_large_trade_pct, 3)
                    # Informed-flow signal: large-trade concentration above expected + low VPIN
                    # Threshold 0.10: top-10% trades are larger-than-expected relative to avg
                    if _large_trade_pct > 0.10 and result.get("vpin", 0.0) < 0.5:
                        result["b3_informed_flow"] = True
            except Exception:
                pass  # B3 computation is best-effort

            if "large_trade_pct" not in result:
                result["large_trade_pct"] = 0.0

            # Cache result for 60 seconds
            self._vpin_cache[token_id] = (result, _now_m + _VPIN_CACHE_TTL)
            return result
        except Exception as e:
            logger.debug("VPIN toxicity check failed: %s", e)
            return _fallback

    # ── Tier 2 #19: Wallet clustering signal ─────────────────────────────

    async def _refresh_wallet_clusters(self) -> None:
        """Periodically refresh wallet cluster data (every 30 min, non-blocking)."""
        _now_m = time.monotonic()
        if _now_m - self._wallet_cluster_last_refresh < self._WALLET_CLUSTER_REFRESH_INTERVAL:
            return
        wc = getattr(self.base_engine, "wallet_clustering", None)
        if not wc:
            return
        self._wallet_cluster_last_refresh = _now_m
        try:
            await wc.identify_clusters()
        except Exception as e:
            logger.debug("WalletClustering refresh failed (non-fatal): %s", e)

    async def _get_wallet_cluster_mult(self) -> float:
        """
        Return a confidence multiplier based on wallet cluster concentration.

        Logic: If the market has many diverse wallet clusters (many independent entities),
        it signals a healthy, hard-to-manipulate market → slight boost (1.02).
        If clustering data is unavailable or no clusters identified → neutral (1.0).
        If cluster data is available but shows high concentration (few large clusters
        with many wallets) → slight penalty (0.95) — coordinated activity risk.

        Refreshes cluster data at most every 30 minutes in the background.
        """
        try:
            # Trigger refresh if stale (non-blocking: refresh happens only if interval passed)
            await self._refresh_wallet_clusters()
            wc = getattr(self.base_engine, "wallet_clustering", None)
            if not wc:
                return 1.0

            n_clusters = len(wc.clusters)
            n_clustered_wallets = len(wc.wallet_to_cluster)

            if n_clusters == 0 or n_clustered_wallets == 0:
                return 1.0  # No cluster data yet → neutral

            # Concentration: avg wallets per cluster (high = coordinated)
            avg_cluster_size = n_clustered_wallets / n_clusters
            if avg_cluster_size >= 5.0:
                # Large coordinated clusters → slight penalty (herding / manipulation risk)
                return 0.95
            elif n_clusters >= 10:
                # Many small independent clusters → healthy diverse market
                return 1.02
            return 1.0
        except Exception as e:
            logger.debug("Wallet cluster mult failed: %s", e)
            return 1.0

    # ── Tier 2 #20: Order flow fingerprinting signal ──────────────────────

    async def _get_order_flow_signal(self, market_id: str, side: str) -> float:
        """
        Return a confidence multiplier from order-flow analysis (120s cache).

        Uses OrderFlowAnalyzer.analyze_order_flow() which computes buy/sell volume
        ratio and detects large orders from recent DB trades.

        Multipliers:
            flow aligns with side:   1.05 (flow confirms the trade direction)
            flow opposes side:       0.95 (flow contradicts direction → caution)
            flow neutral / no data:  1.0
        """
        _ORDER_FLOW_CACHE_TTL = 120.0
        try:
            ofa = getattr(self.base_engine, "order_flow_analyzer", None)
            if not ofa:
                return 1.0

            # Cache check — 120s TTL
            _now_m = time.monotonic()
            _cached = self._order_flow_cache.get(market_id)
            if _cached is not None:
                _cresult, _cexpires = _cached
                if _now_m < _cexpires:
                    # Use cached result
                    _signals = _cresult.get("signals", {})
                    return self._order_flow_mult_from_signals(_signals, side)

            # Fetch order flow analysis
            flow = await ofa.analyze_order_flow(market_id, lookback_minutes=60)
            self._order_flow_cache[market_id] = (flow, _now_m + _ORDER_FLOW_CACHE_TTL)

            _signals = flow.get("signals", {})
            return self._order_flow_mult_from_signals(_signals, side)
        except Exception as e:
            logger.debug("Order flow signal failed: %s", e)
            return 1.0

    @staticmethod
    def _order_flow_mult_from_signals(signals: Dict, side: str) -> float:
        """Convert order flow signals dict → confidence multiplier."""
        flow_signal = signals.get("flow_signal", "neutral")
        if flow_signal == "bullish":
            return 1.05 if side == "YES" else 0.95
        if flow_signal == "bearish":
            return 1.05 if side == "NO" else 0.95
        return 1.0

    # ── Partition Dependence Filter (Sonnemann et al. PNAS 2013) ─────────

    def _partition_dependence_penalty(self, market_data: Dict, side: str, price: float) -> float:
        """
        Partition dependence: binary YES/NO framing anchors to 50/50 ignorance prior,
        inflating YES prices by 15-20pp on newly listed low-liquidity markets.
        Returns additive penalty to subtract from consensus_confidence.
        Strategy: short YES on newly listed (<24h), low-liquidity (<$1000 vol),
        ambiguous questions before informed traders arrive and correct the anchor.
        """
        if side != "YES":
            return 0.0  # Only YES is overpriced by partition dependence
        if price < 0.35 or price > 0.65:
            return 0.0  # Outside the ignorance-prior anchor zone (near 50%)

        try:
            # Check market age: <24h qualifies
            _created = market_data.get("created_at") or market_data.get("createdAt")
            if not _created:
                return 0.0
            _dt = datetime.fromisoformat(str(_created).replace("Z", "+00:00"))
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=timezone.utc)
            _age_h = (datetime.now(timezone.utc) - _dt).total_seconds() / 3600.0
            if _age_h > 24.0:
                return 0.0  # Too old — informed traders have likely corrected it

            # Check liquidity: market must have sufficient volume (configurable, default $5000)
            _vol = float(market_data.get("volume") or market_data.get("volumeNum") or 0)
            _min_vol_pd = getattr(settings, "ENSEMBLE_MIN_MARKET_VOLUME_USD", 5000.0)
            if _vol > _min_vol_pd:
                return 0.0  # Enough liquidity — anchor bias likely corrected already

            # Young + low-liquidity + near-50 YES = partition dependence candidate
            # Penalty decays from −0.04 at age=0 to 0 at age=24h
            _penalty = 0.04 * (1.0 - _age_h / 24.0)
            logger.debug(
                "Partition dependence penalty: market=%s age_h=%.1f vol=%.0f penalty=%.4f",
                market_data.get("id", "?"), _age_h, _vol, _penalty,
            )
            return _penalty
        except Exception:
            return 0.0

    # ── B8: Price velocity + volume acceleration signal ──────────────────

    async def _get_price_momentum_signal(self, token_id: str, market_data: Dict) -> Dict:
        """
        B8: OFI order-book proxy via price velocity + volume acceleration.
        (Cont, Kukanov & Stoikov OFI achieves R²~65% for mid-price changes.)
        Uses streaming data already captured — no new data sources needed.

        Returns:
            {velocity_adj: float (+0.02/-0.02/0.0), velocity: float, vol_acc: float}
        """
        try:
            sp = getattr(self.base_engine, "streaming_persister", None)
            if not sp:
                return {"velocity_adj": 0.0}

            recent = getattr(sp, "_price_buffer", {}).get(token_id, [])
            if len(recent) < 12:
                return {"velocity_adj": 0.0}

            # Price velocity: (p_t - p_{t-10}) / 10
            _prices = [float(r.get("price", 0)) for r in recent[-12:] if r.get("price")]
            if len(_prices) < 11:
                return {"velocity_adj": 0.0}
            _velocity = (_prices[-1] - _prices[-11]) / 10.0

            # Volume acceleration: vol_recent / vol_baseline - 1
            _vols = [float(r.get("volume", 0)) for r in recent[-12:] if r.get("volume") is not None]
            _vol_acc = 0.0
            if len(_vols) >= 8:
                _vol_recent = sum(_vols[-4:]) / 4.0
                _vol_baseline = sum(_vols[:-4]) / max(len(_vols) - 4, 1)
                if _vol_baseline > 0:
                    _vol_acc = _vol_recent / _vol_baseline - 1.0

            # Signal direction check: does momentum agree with our trade side?
            # (velocity > 0 = price rising, vol_acc > 0 = accelerating volume)
            _velocity_adj = 0.0
            if abs(_velocity) > 0.001:  # meaningful move
                _velocity_adj = 0.02 if _velocity > 0 else -0.02
                # Double-check with volume acceleration confirmation
                if _vol_acc > 0.5 and _velocity > 0:
                    _velocity_adj = 0.03  # strong confirmation
                elif _vol_acc < -0.3 and _velocity < 0:
                    _velocity_adj = -0.03

            return {"velocity_adj": _velocity_adj, "velocity": round(_velocity, 5), "vol_acc": round(_vol_acc, 3)}
        except Exception as e:
            logger.debug("B8 price momentum signal failed: %s", e)
            return {"velocity_adj": 0.0}

    # ── WebSocket-driven reactive scan ──────────────────────────────────

    async def on_price_update(self, event: dict) -> None:
        """React to real-time WS price updates with ms-latency: no REST/DB calls."""
        await super().on_price_update(event)
        if not self.running or not self._is_prediction_engine_ready():
            return
        # Cold-start guard: don't trade before scan loop has populated the index
        if not self.base_engine._market_index_populated:
            return
        market_id = event.get("market_id", "")
        token_id = event.get("token_id", "")
        new_price = float(event.get("price", 0))
        if not market_id or new_price <= 0:
            return
        # Track changed markets for delta scanning
        self._changed_markets.add(str(market_id))
        # Update cached feature vector price (keeps predict() fast-path accurate)
        # Must resolve to numeric id (precompute keys use numeric, WS sends condition_id)
        pe = getattr(self.base_engine, "prediction_engine", None)
        if pe and token_id:
            _md = self.base_engine.get_market_from_index(str(market_id))
            _numeric_id = str(_md.get("id", market_id)) if _md else str(market_id)
            pe.update_cached_price(_numeric_id, token_id, new_price)
        # Only react to significant price moves
        threshold = getattr(settings, "ENSEMBLE_WS_PRICE_CHANGE_PCT", 0.005)
        if not hasattr(self, "_ws_prev_prices"):
            self._ws_prev_prices: dict = {}
        old_price = self._ws_prev_prices.get(market_id)
        self._ws_prev_prices[market_id] = new_price
        if old_price is None or abs(new_price - old_price) / max(old_price, 0.01) < threshold:
            return
        # Cooldown per market
        now = time.monotonic()
        if not hasattr(self, "_ws_scan_cooldowns"):
            self._ws_scan_cooldowns: dict = {}
        cooldown = getattr(settings, "ENSEMBLE_WS_COOLDOWN_SECONDS", 5)
        if now - self._ws_scan_cooldowns.get(market_id, 0) < cooldown:
            return
        self._ws_scan_cooldowns[market_id] = now
        try:
            # FAST PATH: O(1) dict lookup instead of 500-market REST call
            market_data = self.base_engine.get_market_from_index(str(market_id))
            if not market_data:
                return
            # FAST PATH: O(1) in-memory position check instead of DB query
            og = getattr(self.base_engine, "order_gateway", None)
            if og is not None and og.has_open_position(self.bot_name, str(market_id)):
                return
            elif og is None and await self._has_open_position_on_market(str(market_id)):
                return
            opp = await self.analyze_opportunity(market_data)
            if opp:
                logger.info("EnsembleBot WS reactive trade on %s (price %.4f→%.4f)", market_id, old_price, new_price)
                await self._execute_ensemble_trade(opp)
        except Exception as e:
            logger.debug("EnsembleBot WS reactive scan failed: %s", e)

    # ── Tier 2 guardrail 2g: Politics profit-taking ───────────────────────

    async def _check_politics_profit_taking(self) -> None:
        """
        2g: Politics profit-taking exit — close YES/NO positions in politics markets
        when unrealized P&L >= POLITICS_EXIT_PCT of the maximum possible profit.

        Logic:
          - YES position entered at price p: max profit = (1 - p) per share.
            If current_price >= p + POLITICS_EXIT_PCT * (1 - p), exit target hit.
          - NO position entered at price p: max profit = p per share.
            If current_price <= p - POLITICS_EXIT_PCT * p, exit target hit.

        On trigger: logs the signal and adds the market to re-entry cooldown.
        In live trading, would also call close_position(). In paper mode, position
        will hold to resolution; cooldown prevents re-entry on the same market.

        Disabled when POLITICS_EXIT_ENABLED=false or no politics positions open.
        """
        if not getattr(settings, "POLITICS_EXIT_ENABLED", True):
            return
        _exit_pct = getattr(settings, "POLITICS_EXIT_PCT", 0.65)
        _min_profit = getattr(settings, "POLITICS_EXIT_MIN_PROFIT_USD", 2.0)
        _og = getattr(self.base_engine, "order_gateway", None)
        if not _og:
            return
        try:
            # Get open positions for this bot
            _positions = getattr(_og, "_positions", {})  # market_id → {side, entry_price, size}
            _bot_positions = {
                k: v for k, v in _positions.items()
                if isinstance(v, dict) and v.get("bot_name") == self.bot_name
            }
            if not _bot_positions:
                return

            # Fetch current prices from market index (in-memory, no DB query)
            _market_index = getattr(self.base_engine, "_market_index", {})

            for market_id, pos in _bot_positions.items():
                _cat = str(pos.get("category") or "").lower()
                if _cat != "politics":
                    continue
                _side = pos.get("side", "YES")
                _entry = float(pos.get("entry_price") or pos.get("price") or 0.0)
                _size = float(pos.get("size") or 0.0)
                if _entry <= 0 or _entry >= 1 or _size <= 0:
                    continue

                # Get current market price
                _mkt = _market_index.get(str(market_id)) or {}
                _cur_price = float(_mkt.get("yes_price") or _mkt.get("price") or _entry)

                # Compute unrealized P&L and target
                if _side == "YES":
                    _max_profit_per_share = 1.0 - _entry
                    _unrealized_pnl = (_cur_price - _entry) * _size
                    _target_price = _entry + _exit_pct * _max_profit_per_share
                    _triggered = _cur_price >= _target_price
                else:  # NO
                    _max_profit_per_share = _entry
                    _no_price = 1.0 - _cur_price
                    _entry_no = 1.0 - _entry
                    _unrealized_pnl = (_entry_no - _no_price) * _size
                    _target_no = _entry_no - _exit_pct * _entry_no
                    _triggered = _no_price <= _target_no

                if not _triggered:
                    continue
                if _unrealized_pnl < _min_profit:
                    continue

                logger.info(
                    "politics_profit_taking_signal",
                    market_id=market_id,
                    side=_side,
                    entry_price=round(_entry, 4),
                    current_price=round(_cur_price, 4),
                    unrealized_pnl=round(_unrealized_pnl, 2),
                    exit_pct=_exit_pct,
                    action="close_signal_logged",
                )

                # Close position via OrderGateway (routes to paper or live engine)
                try:
                    _token_id = str(pos.get("token_id") or _mkt.get("conditionId") or "")
                    _close_result = await self.base_engine.place_order(
                        bot_name=self.bot_name,
                        market_id=market_id,
                        token_id=_token_id,
                        side="SELL",
                        size=_size,
                        price=_cur_price,
                        confidence=1.0,
                        correlation_id=f"profit_take:{market_id}",
                    )
                    logger.info("politics_position_closed", market_id=market_id, result=_close_result)
                except Exception as _ce:
                    logger.warning("politics_close_failed: %s", _ce)
                # Cooldown prevents re-entry on same market
                self._recently_exited[str(market_id)] = time.time()
                self._exit_count[str(market_id)] = 3  # 3 exits = 4× base cooldown
        except Exception as e:
            logger.debug("politics_profit_taking check failed: %s", e)

    # ── Main scan loop ──────────────────────────────────────────────────

    async def scan_and_trade(self):
        await self._adapt_min_confidence()
        await self._refresh_category_mults()  # L2: refresh every 15 min

        # Re-entry cooldown: detect positions that closed since last scan, apply cooldown.
        # Compares current open markets to previous scan snapshot — any that disappeared = exited.
        # Progressive cooldown: each consecutive exit on the same market doubles the cooldown.
        _now_wall = time.time()
        _base_cooldown = getattr(settings, "ENSEMBLE_EXIT_COOLDOWN_SECONDS", 1800)
        _max_cooldown = 3600  # Session 47: 1h cap (was 24h — no market needs 24h lockout in paper trading)
        _cur_open: set = set()
        _og = getattr(self.base_engine, "order_gateway", None)
        if _og is not None:
            _cur_open = set(
                str(mid) for mid in
                getattr(_og, "_open_position_markets", {}).get(self.bot_name, set())
            )
        for _mid in (self._prev_open_markets - _cur_open):
            self._exit_count[_mid] = self._exit_count.get(_mid, 0) + 1
            _cd = min(_max_cooldown, _base_cooldown * (2 ** (self._exit_count[_mid] - 1)))
            self._recently_exited[_mid] = _now_wall
            logger.info("EnsembleBot: market %s exited (#%d), re-entry cooldown %ds",
                        _mid, self._exit_count[_mid], _cd)
        # Reset exit count for markets that were re-entered successfully (in _cur_open but had count)
        for _mid in _cur_open:
            pass  # keep count — only reset when cooldown fully expires without re-exit
        # Prune expired cooldowns (use per-market progressive timeout)
        _to_prune = []
        for k, v in self._recently_exited.items():
            _cd = min(_max_cooldown, _base_cooldown * (2 ** (self._exit_count.get(k, 1) - 1)))
            if _now_wall - v >= _cd:
                _to_prune.append(k)
        for k in _to_prune:
            del self._recently_exited[k]
            self._exit_count.pop(k, None)  # reset count when cooldown fully expires

        # 2g: Politics profit-taking check — runs each scan cycle before market evaluation.
        # Non-blocking: failures are caught internally. Only runs when POLITICS_EXIT_ENABLED=true.
        await self._check_politics_profit_taking()

        if not self._is_prediction_engine_ready():
            # L1 FIX: Log once, then suppress until state changes. Without this, "prediction engine
            # not ready" fires every 10s during startup, flooding logs with thousands of entries
            # if training hangs or takes longer than expected.
            if not getattr(self, "_pe_not_ready_logged", False):
                logger.info("EnsembleBot: prediction engine not ready, waiting for training to complete...")
                self._pe_not_ready_logged = True
            return
        # L1 FIX: Reset the suppression flag when engine becomes ready
        if getattr(self, "_pe_not_ready_logged", False):
            logger.info("EnsembleBot: prediction engine now ready")
            self._pe_not_ready_logged = False
        if self._is_model_stale():
            logger.warning(
                "EnsembleBot: models older than %.0f hours, skipping scan",
                ENSEMBLE_MODEL_STALENESS_HOURS,
            )
            return

        # Scan tradeable markets from DB — capped at SCAN_MARKET_LIMIT per scan cycle.
        # Prevents holding DB sessions for 50-120s when 481 markets are in DB.
        # Increase SCAN_MARKET_LIMIT to 50+ after VPS migration.
        _lt = getattr(self, "_latency_tracker", None)
        markets = await self.base_engine.get_all_tradeable_markets(
            categories=self.target_categories if self.target_categories else None,
        )
        markets = markets[:settings.SCAN_MARKET_LIMIT]
        self.base_engine.update_market_index(markets)
        if _lt:
            _lt.mark("markets_fetched")

        _scan_evaluated = 0
        _scan_best_conf = 0.0
        _scan_best_market = None
        _scan_traded = 0

        # FAST PATH: Use in-memory position tracker from OrderGateway (O(1) per market)
        # instead of N+1 DB queries (one per market). Falls back to DB only if OG unavailable.
        og = getattr(self.base_engine, "order_gateway", None)

        # H5 FIX: When OG is unavailable, prefetch all open position market IDs in ONE query
        # instead of one DB query per market. With 800+ markets, the old code issued 800+ serial
        # awaits per scan — a major bottleneck when OrderGateway is temporarily unavailable.
        _db_open_market_ids: Optional[set] = None
        if og is None:
            db = getattr(self.base_engine, "db", None)
            if db and getattr(db, "session_factory", None):
                try:
                    from sqlalchemy import select
                    from base_engine.data.database import Position
                    from sqlalchemy import or_
                    async with db.get_session() as _pos_session:
                        _pos_result = await _pos_session.execute(
                            select(Position.market_id).where(
                                or_(
                                    Position.bot_id == self.bot_name,
                                    Position.source_bot == self.bot_name,
                                ),
                                Position.status == "open",
                            )
                        )
                        _db_open_market_ids = {str(r[0]) for r in _pos_result.all() if r[0]}
                except Exception as _pos_err:
                    logger.debug("EnsembleBot: prefetch open positions failed (DB fallback per-market): %s", _pos_err)

        # I46: Filter out markets that fail sync gates via _check_market_tradeable().
        # Gate name is logged at INFO so we can see which gate fires most often.
        candidates = []
        _gate_counts: Dict[str, int] = {}
        for market in markets:
            market_id = market.get("id")
            # I46: run centralised sync gate check first
            _gate = self._check_market_tradeable(market)
            if _gate is not None:
                _gate_counts[_gate] = _gate_counts.get(_gate, 0) + 1
                continue
            # Async gate: has open position on this market?
            if og is not None:
                if og.has_open_position(self.bot_name, str(market_id)):
                    _gate_counts["open_position"] = _gate_counts.get("open_position", 0) + 1
                    continue
            elif _db_open_market_ids is not None:
                # H5 FIX: Use prefetched set (O(1) lookup) instead of per-market DB query
                if str(market_id) in _db_open_market_ids:
                    _gate_counts["open_position"] = _gate_counts.get("open_position", 0) + 1
                    continue
            elif await self._has_open_position_on_market(str(market_id)):
                # Fallback: only reached if prefetch itself failed
                _gate_counts["open_position"] = _gate_counts.get("open_position", 0) + 1
                continue
            candidates.append(market)
        if _gate_counts:
            logger.info(
                "EnsembleBot gate rejections: total_in=%d, passed=%d, %s",
                len(markets), len(candidates),
                ", ".join(f"{k}={v}" for k, v in sorted(_gate_counts.items())),
            )

        # Track WS-changed markets for logging (how many had price updates since last scan)
        _ws_changed_count = len(self._changed_markets)
        self._changed_markets.clear()
        # ALL candidates analyzed every cycle — feature vector cache makes this <5ms/market
        # (zero DB queries when background precompute has warmed the cache)

        # PRE-WARM: Batch-load Market ORM objects into PredictionEngine cache.
        # Eliminates N individual SELECT queries during parallel scan.
        pe = getattr(self.base_engine, "prediction_engine", None)
        if pe and hasattr(pe, "prefetch_markets"):
            _prefetch_ids = [str(m.get("id")) for m in candidates if m.get("id")]
            _prefetched = await pe.prefetch_markets(_prefetch_ids)
            if _prefetched:
                logger.debug("Pre-warmed %d market objects for scan", _prefetched)

        # COLD-START GUARD: If background precompute hasn't warmed the cache yet, fire it
        # as a BACKGROUND TASK and skip this scan cycle to avoid the 120s+ timeout.
        # (Old code: await pe.batch_precompute_all_features() → blocked scan → BOT_SCAN_TIMEOUT)
        # The background task warms the cache; the next scan cycle (30-60s later) will be fast.
        if pe and not getattr(pe, "_feature_cache_warmed", False):
            # Short-circuit: if the base_engine background precompute loop has already
            # populated _feature_vector_cache, mark warm immediately without the 300s batch call.
            _fv_cache = getattr(pe, "_feature_vector_cache", {})
            if len(_fv_cache) >= 5:
                pe._feature_cache_warmed = True
                pe._warm_fail_count = 0
                logger.info(
                    "EnsembleBot: background precompute already warmed cache (%d markets) — skipping explicit warm",
                    len(_fv_cache),
                )
                # Fall through to scan — cache is ready
            else:
                # Cache is truly cold — kick off explicit warm and skip this scan
                if not getattr(pe, "_feature_cache_warming_task_started", False):
                    pe._feature_cache_warming_task_started = True
                    _warm_ids = [str(m.get("id")) for m in candidates if m.get("id")]
                    logger.info(
                        "EnsembleBot: feature cache cold — warming %d markets in background (skipping this scan)...",
                        len(_warm_ids),
                    )
                    async def _do_warm(_ids=_warm_ids, _pe=pe) -> None:
                        _warm_ok = False
                        try:
                            _n_warmed = await asyncio.wait_for(_pe.batch_precompute_all_features(_ids), timeout=300.0)
                            _warm_ok = _n_warmed > 0  # M8: only mark warm if features actually cached
                        except (Exception, asyncio.TimeoutError) as _warm_err:
                            logger.warning(
                                "EnsembleBot: feature cache warm failed/timed out (%s) — proceeding cold",
                                type(_warm_err).__name__,
                            )
                        finally:
                            if _warm_ok:
                                _pe._feature_cache_warmed = True
                                _pe._warm_fail_count = 0
                                logger.info(
                                    "EnsembleBot: feature cache ready for %d markets",
                                    len(_ids),
                                )
                            else:
                                # I04: Track failures — fail-open after 3 attempts so bot doesn't
                                # get stuck in a permanent warm-skip loop on persistent DB issues.
                                _fail_n = getattr(_pe, "_warm_fail_count", 0) + 1
                                _pe._warm_fail_count = _fail_n
                                if _fail_n >= 3:
                                    logger.warning(
                                        "EnsembleBot: feature cache warm failed %d times — "
                                        "failing open (predictions run on cold cache)",
                                        _fail_n,
                                    )
                                    _pe._feature_cache_warmed = True   # fail-open
                                else:
                                    # Allow retry on next scan cycle
                                    _pe._feature_cache_warming_task_started = False
                                    _pe._feature_cache_warmed = False
                                    logger.info(
                                        "EnsembleBot: feature cache FAILED (attempt %d/3) — will retry next scan",
                                        _fail_n,
                                    )
                    _t = asyncio.create_task(_do_warm())
                    _t.add_done_callback(lambda t: self._on_bg_task_done(t, "feature_cache_warm"))
                return  # Skip this scan; next cycle will use the warm cache

        # PARALLEL SCAN: Analyze ALL candidates concurrently.
        # Feature vector cache makes each market ~5ms (pure CPU). 800 × 5ms / concurrency=10 ≈ 400ms.
        _concurrency = getattr(settings, "ENSEMBLE_SCAN_CONCURRENCY", 10)
        _sem = asyncio.Semaphore(_concurrency)
        _opportunities = []  # (opportunity, market_id) pairs — collect, then execute trades serially

        async def _analyze_one(m: Dict) -> Optional[Dict]:
            async with _sem:
                try:
                    opp = await self.analyze_opportunity(m)
                    return opp
                except Exception as e:
                    logger.warning("Error analyzing market %s: %s", m.get("id"), e, exc_info=False)
                    return None

        if _lt:
            _lt.mark("filter_done")

        # Process in batches of _concurrency: analyze then execute immediately.
        # This ensures trades are placed even if the full scan times out partway through.
        # Old approach: gather ALL 100 markets first, then execute — timeout killed execution.
        _scan_evaluated = 0
        for _batch_start in range(0, len(candidates), _concurrency):
            _batch = candidates[_batch_start:_batch_start + _concurrency]
            _batch_tasks = [_analyze_one(m) for m in _batch]
            _batch_results = await asyncio.gather(*_batch_tasks, return_exceptions=True)
            _scan_evaluated += len(_batch)
            for _bi, result in enumerate(_batch_results):
                if isinstance(result, Exception):
                    logger.warning("Parallel analysis error for market %s: %s",
                                   _batch[_bi].get("id"), result, exc_info=False)
                    continue
                if result:
                    conf = result.get("confidence", 0)
                    if conf > _scan_best_conf:
                        _scan_best_conf = conf
                        _scan_best_market = _batch[_bi].get("id")
                    _opportunities.append(result)
                    # Execute immediately — don't wait for full scan to complete
                    try:
                        await self._execute_ensemble_trade(result)
                        _scan_traded += 1
                    except Exception as e:
                        logger.warning("Trade execution error: %s", e, exc_info=False)

        if _lt:
            _lt.mark("analyze_done")

        # Per-scan summary: ALWAYS INFO — never miss critical data.
        # Trades, confidence, evaluations are all operationally important.
        if not hasattr(self, "_scan_count"):
            self._scan_count = 0
        self._scan_count += 1
        logger.info(
            "EnsembleBot scan complete",
            markets_fetched=len(markets) if markets else 0,
            evaluated=_scan_evaluated,
            ws_changed=_ws_changed_count,
            trades=_scan_traded,
            best_confidence=round(_scan_best_conf, 4) if _scan_best_conf > 0 else None,
            best_market=_scan_best_market,
            min_threshold=round(self.min_consensus_confidence, 4),
            scan_number=self._scan_count,
        )

        # B5: Publish feature lift to BaseEngine for cross-bot sharing.
        # Use model_weights as proxy for "informative features" — higher weight = more lift.
        try:
            pe = getattr(self.base_engine, "prediction_engine", None)
            if pe and pe.model_weights:
                _feature_lifts = {f"model_{k}": v for k, v in pe.model_weights.items()}
                self.base_engine.publish_feature_lift(self.bot_name, _feature_lifts)
        except Exception:
            pass  # B5 is best-effort, never block scan

        # Update open market snapshot for next scan's exit detection
        self._prev_open_markets = _cur_open

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        if not market_data or not isinstance(market_data, dict):
            return None
        market_id = market_data.get("id")
        if not market_id:
            return None
        tokens = market_data.get("tokens", [])
        if not tokens or not isinstance(tokens, list):
            return None

        # Support yes_token_id/no_token_id format (e.g. from unified_market_service)
        yes_tid = (market_data.get("yes_token_id") or market_data.get("yesTokenId") or "").strip()
        no_tid = (market_data.get("no_token_id") or market_data.get("noTokenId") or "").strip()
        token_specs: List[Tuple[Dict, str, float, str]] = []  # (token_dict, token_id, price, side)
        if yes_tid and no_tid:
            yes_price_raw = None
            no_price_raw = None
            op = market_data.get("outcome_prices") or market_data.get("outcomePrices")
            if isinstance(op, list) and len(op) >= 2:
                try:
                    yes_price_raw = float(op[0]) if op[0] is not None else None
                    no_price_raw = float(op[1]) if op[1] is not None else None
                except (TypeError, ValueError):
                    pass
            if yes_price_raw is None or no_price_raw is None:
                for t in tokens:
                    if not isinstance(t, dict):
                        continue
                    tid = (t.get("tokenId") or t.get("token_id") or "").strip()
                    if tid == yes_tid:
                        yes_price_raw = t.get("outcomePrice") or t.get("outcome_price")
                    elif tid == no_tid:
                        no_price_raw = t.get("outcomePrice") or t.get("outcome_price")
            if yes_price_raw is not None:
                p = self.validate_price(yes_price_raw, market_id)
                if p is not None:
                    token_specs.append(({"tokenId": yes_tid}, yes_tid, p, "YES"))
            if no_price_raw is not None:
                p = self.validate_price(no_price_raw, market_id)
                if p is not None:
                    token_specs.append(({"tokenId": no_tid}, no_tid, p, "NO"))
        if not token_specs:
            for i, token in enumerate(tokens):
                if not isinstance(token, dict):
                    continue
                token_id = token.get("tokenId") or token.get("token_id")
                if not token_id:
                    continue
                price = self.validate_price(token.get("outcomePrice") or token.get("outcome_price"), market_id)
                # K4 FIX: Prefer fresher WS price over stale API/DB price
                ws_price = self.get_ws_price(market_id)
                if ws_price is not None:
                    price = ws_price
                if price is None:
                    continue
                side = _infer_side_from_token(market_data, str(token_id))
                if not side:
                    side = "YES" if i == 0 else "NO"
                token_specs.append((token, str(token_id), price, side))

        if not token_specs:
            return None

        best_opportunity: Optional[Dict] = None
        best_edge = 0.0

        # Parallelize YES/NO token analysis — tokens are independent, no reason to serialize
        if len(token_specs) >= 2:
            import asyncio as _aio
            tasks = [
                self._analyze_one_token(market_data, market_id, tid, pr, sd)
                for _td, tid, pr, sd in token_specs
            ]
            results = await _aio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("EnsembleBot token analysis exception: market=%s err=%s", market_id, r)
                    continue
                if r and r.get("edge", 0) > best_edge:
                    best_edge = r["edge"]
                    best_opportunity = r
        else:
            for _token_dict, token_id, price, side in token_specs:
                opp = await self._analyze_one_token(market_data, market_id, token_id, price, side)
                if opp and opp.get("edge", 0) > best_edge:
                    best_edge = opp["edge"]
                    best_opportunity = opp

        if best_opportunity:
            # TEMP DIAG: log every opportunity that passes all filters
            logger.info(
                "EnsembleBot OPPORTUNITY: market=%s side=%s conf=%.4f edge=%.4f",
                best_opportunity.get("market_id"), best_opportunity.get("side"),
                best_opportunity.get("confidence", 0), best_opportunity.get("edge", 0),
            )
        return best_opportunity

    async def _analyze_one_token(
        self,
        market_data: Dict,
        market_id: str,
        token_id: str,
        price: float,
        side: str,
    ) -> Optional[Dict]:
        # Re-entry cooldown check: skip analysis entirely if market recently exited
        # Progressive: each consecutive exit doubles cooldown (30m → 1h → 2h → ... → 24h cap)
        _base_cooldown = getattr(settings, "ENSEMBLE_EXIT_COOLDOWN_SECONDS", 1800)
        _mid_str = str(market_id)
        _exit_ts = self._recently_exited.get(_mid_str)
        if _exit_ts is not None:
            _n_exits = self._exit_count.get(_mid_str, 1)
            _cd = min(86400, _base_cooldown * (2 ** (_n_exits - 1)))
            _elapsed = time.time() - _exit_ts
            if _elapsed < _cd:
                _remaining = int(_cd - _elapsed)
                logger.debug(
                    "EnsembleBot skipping %s: re-entry cooldown %ds remaining (exit #%d)",
                    market_id, _remaining, _n_exits,
                )
                return None

        # P1: Early price guard — reject before running full ML pipeline (11 models).
        # risk_manager blocks these at order time anyway (risk_manager.py:255-259),
        # but by then all compute is wasted. Saves ~60-80% of scan CPU on penny tokens.
        _min_price = getattr(settings, "RISK_MIN_PRICE", 0.05)
        _max_price = getattr(settings, "RISK_MAX_PRICE", 0.95)
        if price < _min_price or price > _max_price:
            logger.debug(
                "EnsembleBot price guard: %s price=%.4f outside [%.2f, %.2f]",
                market_id, price, _min_price, _max_price,
            )
            return None

        try:
            # B4 FIX: Pass user_address so prediction engine uses real user stats
            # instead of defaults (win_rate=0.5, profit=0.0)
            prediction = await self.base_engine.get_predictions(
                market_id=market_id,
                token_id=token_id,
                price=price,
                user_address=getattr(settings, "WALLET_ADDRESS", None),
                correlation_id=getattr(self, "_current_correlation_id", None),
            )
        except Exception as e:
            logger.warning("Prediction engine failed for market %s: %s", market_id, e)
            return None

        if not prediction or not isinstance(prediction, dict):
            return None
        model_predictions = prediction.get("model_predictions", {})
        if not model_predictions or not isinstance(model_predictions, dict):
            return None

        # Alpha decay: degrade confidence for stale predictions (cached results)
        # confidence *= exp(-lambda * hours_since_prediction)
        _pred_ts = prediction.get("prediction_timestamp")
        _alpha_decay = 1.0
        if _pred_ts:
            try:
                _pred_dt = datetime.fromisoformat(_pred_ts)
                _age_hours = (datetime.now(timezone.utc) - _pred_dt).total_seconds() / 3600.0
                _decay_lambda = getattr(settings, "ALPHA_DECAY_LAMBDA", 0.5)
                if _age_hours > 0.01:  # >36 seconds old
                    _alpha_decay = math.exp(-_decay_lambda * _age_hours)
            except (ValueError, TypeError):
                pass

        weights_to_use = prediction.get("suggested_model_weights") or self.model_weights
        # Feature importance observability — log which features matter most
        feat_importance = prediction.get("feature_importance")
        if feat_importance:
            top_features = sorted(feat_importance.items(), key=lambda x: x[1], reverse=True)[:5]
            logger.debug("EnsembleBot: top features: %s", top_features)
        try:
            weighted_prediction = 0.0
            total_weight = 0.0
            default_w = 1.0 / max(len(model_predictions), 1)
            for name in model_predictions:
                weight = weights_to_use.get(name, default_w)
                pred_value = model_predictions.get(name, 0.5)
                if pred_value is not None:
                    pred_float = float(pred_value)
                    if not (math.isnan(pred_float) or math.isinf(pred_float)):
                        weighted_prediction += pred_float * weight
                        total_weight += weight
            if total_weight <= 0:
                return None
            weighted_prediction = weighted_prediction / total_weight
        except (ValueError, TypeError) as e:
            logger.debug("Invalid model prediction values for market %s: %s", market_id, e)
            return None

        # For NO side we want model to predict low (outcome NO wins); effective prob for "this side wins" = 1 - p_yes
        if side == "NO":
            weighted_prediction = 1.0 - weighted_prediction

        # B14 FIX: Use the prediction engine's blended confidence directly.
        # The prediction engine already blends ML ensemble with learning_confidence
        # (at prediction_engine.py:1629-1630). Re-blending here was diluting ML to ~42%.
        # Now: use weighted_prediction (pure ML) as the primary signal.
        consensus_confidence = weighted_prediction

        # Optional LLM nudge
        llm_est = prediction.get("llm_estimate")
        if isinstance(llm_est, (int, float)) and not (math.isnan(llm_est) or math.isinf(llm_est)):
            llm_for_side = (1.0 - llm_est) if side == "NO" else llm_est
            consensus_confidence = 0.9 * consensus_confidence + 0.1 * llm_for_side
            consensus_confidence = min(1.0, max(0.0, consensus_confidence))

        # Alpha decay: stale predictions get reduced confidence
        if _alpha_decay < 1.0:
            consensus_confidence *= _alpha_decay

        # B4: Continuous IQR position scaling — replaces binary disagreement penalty.
        # Wide model spread (high IQR) = uncertainty → proportional size reduction.
        # Formula: kelly_size *= (1 - (p75 - p25) / 0.5)
        # IQR=0.0 → mult=1.0 (full size), IQR=0.5 → mult=0.0, IQR=0.25 → mult=0.5
        _disagreement_mult = 1.0
        pred_values = [float(v) for v in model_predictions.values() if v is not None and not (math.isnan(float(v)) or math.isinf(float(v)))]
        if len(pred_values) >= 2:
            _sorted_pv = sorted(pred_values)
            _n_pv = len(_sorted_pv)
            _p25 = _sorted_pv[max(0, int(_n_pv * 0.25))]
            _p75 = _sorted_pv[min(_n_pv - 1, int(_n_pv * 0.75))]
            _iqr = _p75 - _p25
            # Continuous scaling: IQR / 0.5 gives fraction of max disagreement
            _disagreement_mult = max(0.0, 1.0 - (_iqr / 0.5))
            # Floor at 0.3 so we never fully zero out a signal — just shrink it heavily
            _disagreement_mult = max(0.3, _disagreement_mult)

        # Favourite-longshot bias adjustment — compute as additive delta (applied separately)
        _flb_delta = 0.0
        if price < 0.20 and side == "NO":
            _flb_delta = 0.03 * (1.0 - price / 0.20)
        elif price > 0.80 and side == "YES":
            _flb_delta = 0.02 * ((price - 0.80) / 0.20)
        elif price < 0.20 and side == "YES":
            _flb_delta = -0.03 * (1.0 - price / 0.20)

        # A1: Category-scaled FLB delta (Becker 2026 — bias varies 43× by category)
        # World Events 7.32pp, Media 7.28pp, Entertainment 4.79pp, Finance 0.17pp
        _cat_for_flb = (market_data.get("category") or market_data.get("market_category") or "").lower()
        _flb_category_scale = getattr(settings, "CATEGORY_BIAS_SCALE", {}).get(_cat_for_flb, 1.0)
        _flb_delta *= _flb_category_scale

        # Apply FLB as additive (not multiplicative — it's a bias correction)
        consensus_confidence += _flb_delta
        consensus_confidence = max(0.0, min(1.0, consensus_confidence))

        # NOTE: _disagreement_mult is computed above for position sizing (kelly_size scaling).
        # It is applied AFTER the threshold check below — it affects HOW MUCH we bet,
        # NOT whether we bet at all. Applying it before the check (old bug) would crush
        # a 0.52 ML prediction to 0.52 * 0.3 = 0.156 (floor case), guaranteeing 0 trades.

        # A4: Market lifecycle YES penalty (Becker 2026 — ignorance-prior anchor effect)
        # Newly-listed (<48h) YES-side longshot bets systematically overpriced before informed traders arrive.
        # Penalise YES confidence on young markets with price < 0.55.
        _created_raw = market_data.get("created_at") or market_data.get("createdAt")
        if _created_raw and side == "YES" and price < 0.55:
            try:
                _created_dt = datetime.fromisoformat(str(_created_raw).replace("Z", "+00:00"))
                if _created_dt.tzinfo is None:
                    _created_dt = _created_dt.replace(tzinfo=timezone.utc)
                _age_hours = (datetime.now(timezone.utc) - _created_dt).total_seconds() / 3600.0
                if _age_hours < 48:
                    # Max penalty −0.03 at hour 0, decays linearly to 0 at 48h
                    _lifecycle_penalty = 0.03 * (1.0 - _age_hours / 48.0)
                    consensus_confidence -= _lifecycle_penalty
                    logger.debug(
                        "A4 lifecycle YES penalty: market_id=%s age_h=%.1f penalty=%.4f",
                        market_id, _age_hours, _lifecycle_penalty,
                    )
            except (ValueError, TypeError):
                pass

        # Partition dependence: subtract YES anchor-inflation penalty on young low-liquidity markets
        _partition_penalty = self._partition_dependence_penalty(market_data, side, price)
        if _partition_penalty > 0:
            consensus_confidence -= _partition_penalty
            consensus_confidence = max(0.0, consensus_confidence)

        # B1: SUPER relay — penalise markets that previously produced high-surprise outcomes.
        # (NeurIPS 2023: share only top-1–5% by TD-error; we apply 0.90× mult on matching markets.)
        try:
            _pe = getattr(self.base_engine, "prediction_engine", None)
            if _pe and getattr(_pe, "_drift_tracker", None):
                if _pe._drift_tracker.is_high_surprise_market(market_id):
                    consensus_confidence *= 0.90
                    logger.debug("B1 SUPER penalty applied: market=%s (prev high-surprise)", market_id)
        except Exception:
            pass  # B1 is best-effort — never block scan

        if consensus_confidence < self.min_consensus_confidence:
            if consensus_confidence > self.min_consensus_confidence - 0.05:
                logger.debug(
                    "EnsembleBot near-miss: %s side=%s conf=%.3f (need %.3f) price=%.3f",
                    market_id, side, consensus_confidence, self.min_consensus_confidence, price,
                )
            return None

        # B4: IQR disagreement multiplier scales position SIZE only — NOT confidence.
        # DO NOT apply to consensus_confidence here: it gets passed to apply_signal_enhancements()
        # which returns signal_confidence. risk_manager re-checks that value against threshold.
        # Applying here (old bug re-introduced) causes 0.47 × 0.3 = 0.14 → risk_manager blocks.
        # _disagreement_mult is carried in the return dict and applied to Kelly size at execution.

        # Session 46: Was 6h — near-resolution markets often have the clearest signals.
        # Only skip within 1h when market is about to freeze/resolve.
        if self.should_skip_near_resolution(market_data, threshold_hours=1):
            return None

        # Parallelize sentiment + event calendar + signal enhancements + clarity + VPIN + B8 momentum
        direction_for_signals = side  # Pass YES/NO directly — signals use YES/NO not BUY/SELL
        # K2 FIX: Also fetch rich sentiment from SentimentAnalyzer (volume/orderbook/divergence)
        # Tier 2 #16: LLM resolution clarity scoring
        # Tier 2 #18: VPIN toxicity detection
        # B8: Price velocity + volume acceleration OFI proxy
        (
            sentiment_result, event_mult, signal_confidence, rich_sentiment,
            clarity_result, vpin_result, momentum_result,
            wallet_cluster_mult, order_flow_mult,
        ) = await asyncio.gather(
            self._calculate_sentiment(market_id),
            self._event_calendar_confidence_mult(market_id),
            self.apply_signal_enhancements(
                market_id, token_id, direction_for_signals, consensus_confidence, market_data
            ),
            self.get_sentiment(
                market_id,
                price_data={"current_price": price, "token_id": token_id},
                volume_data={"volume": float(market_data.get("volume") or market_data.get("volumeNum") or 0)},
            ),
            self._get_resolution_clarity(market_data),
            self._get_vpin_toxicity(token_id),
            self._get_price_momentum_signal(token_id, market_data),
            self._get_wallet_cluster_mult(),          # Tier 2 #19: wallet clustering
            self._get_order_flow_signal(market_id, side),  # Tier 2 #20: order flow
            return_exceptions=True,
        )
        # Phase 0: Use MultiplierAggregator for all post-prediction multipliers
        from base_engine.learning.multiplier_aggregator import MultiplierAggregator
        _post_agg = MultiplierAggregator()

        # Apply sentiment as multiplier
        sentiment = sentiment_result if not isinstance(sentiment_result, BaseException) else None
        if sentiment is not None and abs(sentiment) >= SENTIMENT_NEUTRAL_THRESHOLD:
            sentiment_strength = min(1.0, abs(sentiment))
            sentiment_aligns = (sentiment > 0 and side == "YES") or (sentiment < 0 and side == "NO")
            if sentiment_aligns:
                _post_agg.add("sentiment", 1.0 + sentiment_strength * 0.1)
            else:
                _post_agg.add("sentiment", 1.0 - sentiment_strength * 0.05)

        # Apply event calendar as multiplier
        if not isinstance(event_mult, BaseException):
            _post_agg.add("event_calendar", event_mult)

        # K2: Apply rich sentiment from SentimentAnalyzer if available
        if isinstance(rich_sentiment, dict) and not isinstance(rich_sentiment, BaseException):
            rs_score = rich_sentiment.get("overall_sentiment", 0.0)
            # SentimentAnalyzer returns overall_sentiment as a string enum ("bullish"/"bearish"/etc.)
            # Convert to float before arithmetic (abs, >, <)
            if isinstance(rs_score, str):
                _sent_map = {
                    "strong_bullish": 0.8, "bullish": 0.5,
                    "neutral": 0.0,
                    "bearish": -0.5, "strong_bearish": -0.8,
                }
                rs_score = _sent_map.get(rs_score.lower(), 0.0)
            if rs_score is not None and isinstance(rs_score, (int, float)) and abs(rs_score) >= 0.2:
                rs_aligns = (rs_score > 0 and side == "YES") or (rs_score < 0 and side == "NO")
                if rs_aligns:
                    _post_agg.add("rich_sentiment", 1.0 + abs(rs_score) * 0.05)
                else:
                    _post_agg.add("rich_sentiment", 1.0 - abs(rs_score) * 0.03)

        # Tier 2 #16: Resolution clarity — penalize ambiguous markets
        if isinstance(clarity_result, (int, float)) and not isinstance(clarity_result, BaseException):
            # clarity is 0.0 (ambiguous) to 1.0 (crystal clear)
            # Map: 1.0 → multiplier 1.0, 0.5 → 0.95, 0.0 → 0.85
            _clarity_mult = 0.85 + 0.15 * max(0.0, min(1.0, clarity_result))
            _post_agg.add("resolution_clarity", _clarity_mult)

        # Tier 2 #18: VPIN toxicity — penalize toxic flow (informed traders active)
        if isinstance(vpin_result, dict) and not isinstance(vpin_result, BaseException):
            _vpin = vpin_result.get("vpin", 0.0)
            if _vpin > 0.7:
                # Toxic flow: heavy penalty (informed traders = adverse selection risk)
                _post_agg.add("vpin_toxicity", 0.75)
            elif _vpin > 0.5:
                # Elevated flow: mild caution
                _post_agg.add("vpin_toxicity", 0.90)
            # B3: Large-trade concentration — informed flow signal even when VPIN clock is slow
            if vpin_result.get("b3_informed_flow"):
                _post_agg.add("b3_large_trade_toxicity", 0.85)

        # B8: Price velocity + volume acceleration OFI proxy
        if isinstance(momentum_result, dict) and not isinstance(momentum_result, BaseException):
            _vel_adj = momentum_result.get("velocity_adj", 0.0)
            if _vel_adj != 0.0:
                # Convert additive adj to multiplicative: +0.02 → 1.02×, -0.02 → 0.98×
                # Direction check: velocity > 0 = price rising = favors YES
                _vel_direction_ok = (
                    (_vel_adj > 0 and side == "YES") or (_vel_adj < 0 and side == "NO")
                )
                if _vel_direction_ok:
                    _post_agg.add("b8_momentum", 1.0 + abs(_vel_adj))
                else:
                    _post_agg.add("b8_momentum", 1.0 - abs(_vel_adj))

        # Tier 2 #19: Wallet clustering — concentration multiplier
        if isinstance(wallet_cluster_mult, (int, float)) and not isinstance(wallet_cluster_mult, BaseException):
            _wcm = float(wallet_cluster_mult)
            if _wcm != 1.0:
                _post_agg.add("wallet_clustering", _wcm)

        # Tier 2 #20: Order flow fingerprinting — directional flow multiplier
        if isinstance(order_flow_mult, (int, float)) and not isinstance(order_flow_mult, BaseException):
            _ofm = float(order_flow_mult)
            if _ofm != 1.0:
                _post_agg.add("order_flow", _ofm)

        # L2: Category confidence multiplier from PerformanceRecord
        _cat = (market_data.get("category") or market_data.get("market_category") or "").lower()
        if _cat and _cat in self._category_mults:
            _post_agg.add("category", self._category_mults[_cat])

        # Compute composite multiplier with clamp [0.3, 2.0]
        _post_composite = _post_agg.compute()

        # Choose base: signal-enhanced if available, raw consensus otherwise.
        # Then apply post-aggregator multipliers (sentiment, event, clarity, VPIN,
        # momentum, wallet clustering, order flow, category) to the chosen confidence.
        # Previously _post_composite was applied to consensus_confidence but then
        # overwritten by signal_confidence, discarding all post-aggregator adjustments.
        confidence = signal_confidence if not isinstance(signal_confidence, BaseException) else consensus_confidence
        confidence *= _post_composite
        confidence = max(0.0, min(1.0, confidence))

        # Post-multiplier gate: reject if multipliers dragged confidence below threshold
        if confidence < self.min_consensus_confidence:
            logger.debug(
                "EnsembleBot post-mult reject: %s side=%s conf=%.4f (need %.3f)",
                market_id, side, confidence, self.min_consensus_confidence,
            )
            return None

        # Fix 7 — Hard volume gate: skip thin markets regardless of model edge.
        _mkt_vol = float(market_data.get("volume") or market_data.get("volumeNum") or 0)
        _min_vol_gate = getattr(settings, "ENSEMBLE_MIN_MARKET_VOLUME_USD", 5000.0)
        if _mkt_vol < _min_vol_gate:
            logger.debug(
                "EnsembleBot volume reject: %s vol=%.0f (min %.0f)",
                market_id, _mkt_vol, _min_vol_gate,
            )
            return None

        # Edge filter: model must think this side is underpriced by at least min_edge.
        # confidence = model's P(this side wins), price = market's P(this side wins)
        # Positive edge = model thinks market is wrong in our favor
        edge = confidence - price
        _min_edge = getattr(settings, "ENSEMBLE_MIN_EDGE", 0.10)

        # Category-specific edge overrides per guardrail_settings.csv
        _cat = (market_data.get("category") or market_data.get("market_category") or "").lower()
        if _cat:
            try:
                import json as _json
                _cat_edges = _json.loads(getattr(settings, "ENSEMBLE_CATEGORY_MIN_EDGES", "{}"))
                if _cat in _cat_edges:
                    _min_edge = float(_cat_edges[_cat])
            except Exception:
                pass

        # Session 46: Only penalize extreme favorites (>95¢) — was 2×/3× at 80¢/90¢ which
        # made high-confidence markets (the BEST opportunities) completely untradeable.
        if price > 0.95:
            _min_edge *= 1.5

        # Fix 9 — CLOB spread check: fetch live bid-ask spread, reject if too wide,
        # deduct half-spread from gross edge (cost of crossing on entry).
        _spread = 0.0
        _max_spread = getattr(settings, "ENSEMBLE_MAX_SPREAD_PCT", 0.10)
        _max_relative_spread = float(getattr(settings, "ENSEMBLE_MAX_RELATIVE_SPREAD", 0.20))
        try:
            _client = getattr(self, "client", None) or getattr(self, "_client", None)
            if _client and hasattr(_client, "get_orderbook"):
                _book = await asyncio.wait_for(
                    _client.get_orderbook(market_id, token_id),
                    timeout=1.5,
                )
                _bids = _book.get("bids") or []
                _asks = _book.get("asks") or []
                if _bids and _asks:
                    _best_bid = max(float(b.get("price", 0)) for b in _bids)
                    _best_ask = min(float(a.get("price", 1)) for a in _asks)
                    if 0 < _best_bid < _best_ask <= 1:
                        _spread = _best_ask - _best_bid
                        if _spread > _max_spread:
                            logger.debug(
                                "EnsembleBot spread reject: %s spread=%.3f (max %.2f)",
                                market_id, _spread, _max_spread,
                            )
                            return None
                        # S49: Relative spread check — a 5c spread on a 15c token is 33%
                        # which destroys any edge via round-trip cost.
                        _relative_spread = _spread / price if price > 0 else 1.0
                        if _relative_spread > _max_relative_spread:
                            logger.debug(
                                "EnsembleBot relative spread reject: %s spread=%.3f price=%.4f "
                                "relative=%.1f%% (max %.0f%%)",
                                market_id, _spread, price,
                                100 * _relative_spread, 100 * _max_relative_spread,
                            )
                            return None
        except (asyncio.TimeoutError, Exception) as _se:
            logger.debug("Orderbook fetch skipped (non-fatal): %s", type(_se).__name__)

        # Net edge after round-trip cost deduction.
        # Round-trip costs: spread/2 (entry slippage) + taker fee on entry + taker fee on exit.
        # TAKER_FEE_BPS=150 → 1.5% per side → 3% fees + ~0.5% slippage = ~3.5-4% total.
        _taker_fee_bps = float(getattr(settings, "TAKER_FEE_BPS", 150))
        _taker_fee_pct = _taker_fee_bps / 10000.0
        _round_trip_cost = (_spread / 2.0) + (2 * _taker_fee_pct)
        net_edge = edge - _round_trip_cost

        if net_edge < _min_edge:
            logger.debug(
                "EnsembleBot edge reject: %s side=%s cat=%s model=%.4f mkt=%.4f "
                "edge=%.4f spread=%.4f net=%.4f (need %.3f)",
                market_id, side, _cat or "unknown", confidence, price,
                edge, _spread, net_edge, _min_edge,
            )
            return None

        return {
            "type": "ensemble",
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            "price": price,
            "edge": edge,
            "confidence": confidence,
            "iqr_size_mult": _disagreement_mult,
            "weighted_prediction": weighted_prediction,
            "model_predictions": model_predictions,
            "sentiment": sentiment,
            "calibration_quality": prediction.get("calibration_quality"),
            "regime_vol": float(prediction.get("regime_vol", 0.0)),
            "category": _cat,  # P2: Pass category for BotBankrollManager Kelly fractions
        }

    async def _execute_ensemble_trade(self, opportunity: Dict) -> None:
        market_id = opportunity["market_id"]
        token_id = opportunity["token_id"]
        side = opportunity["side"]
        # Fast path: check in-memory position tracker first (O(1), no DB).
        # OrderGateway.reserve_position() is the authoritative DB-backed check;
        # this just avoids entering the full pipeline when we know it'll fail.
        og = getattr(self.base_engine, "order_gateway", None)
        if og is not None and og.has_open_position(self.bot_name, str(market_id)):
            logger.debug("EnsembleBot trade SKIPPED (open position): %s", market_id)
            return
        # Bug B fix: dedup at token level — don't re-enter same (market, token) pair
        # The market-level check above covers most cases, but scan parallel paths
        # could both clear the market check before either records a position.
        if not hasattr(self, "_pending_orders"):
            self._pending_orders: set = set()
        _order_key = (str(market_id), str(token_id))
        if _order_key in self._pending_orders:
            logger.debug("EnsembleBot trade SKIPPED (pending order for same token): %s/%s", market_id, token_id)
            return
        self._pending_orders.add(_order_key)
        try:
            await self._execute_ensemble_trade_inner(opportunity, og)
        finally:
            self._pending_orders.discard(_order_key)

    async def _execute_ensemble_trade_inner(self, opportunity: Dict, og) -> None:
        market_id = opportunity["market_id"]
        side = opportunity["side"]

        size = await self.calculate_bot_position_size(
            opportunity["confidence"], opportunity["price"],
            calibration_quality=opportunity.get("calibration_quality"),
            market_vol=float(opportunity.get("regime_vol", 0.0)),
            category=(opportunity.get("category") or opportunity.get("market_category") or "").lower(),
        )
        # B4: Apply IQR disagreement multiplier to size (not confidence — see _analyze_one_token)
        _iqr_mult = opportunity.get("iqr_size_mult", 1.0)
        size = max(0.0, size * _iqr_mult)
        if size <= 0:
            logger.info(
                "EnsembleBot trade SKIPPED (size=0): %s conf=%.2f%% price=%.4f",
                market_id, opportunity["confidence"] * 100, opportunity["price"],
            )
            return

        # B11: Shadow maker P&L — log what price we WOULD have quoted as a maker.
        # model_mid = weighted_prediction. Spread = ±1.5% around mid.
        # When market crosses the shadow price, hypothetical fill is logged.
        # Builds data to justify switching to maker strategy at go-live.
        _model_mid = opportunity.get("weighted_prediction")
        if _model_mid is not None:
            try:
                _shadow_spread = 0.015  # ±1.5% around model mid
                _shadow_bid = max(0.01, _model_mid - _shadow_spread)
                _shadow_ask = min(0.99, _model_mid + _shadow_spread)
                if not hasattr(self, "_shadow_maker_orders"):
                    self._shadow_maker_orders: list = []
                import time as _time
                self._shadow_maker_orders.append({
                    "ts": _time.time(),
                    "market_id": str(market_id),
                    "token_id": str(opportunity["token_id"]),
                    "side": side,
                    "model_mid": round(_model_mid, 4),
                    "shadow_bid": round(_shadow_bid, 4),
                    "shadow_ask": round(_shadow_ask, 4),
                    "actual_price": opportunity["price"],
                    "filled": False,
                })
                # Keep last 500 shadow orders only
                if len(self._shadow_maker_orders) > 500:
                    self._shadow_maker_orders = self._shadow_maker_orders[-500:]
            except Exception:
                pass

        order = await self.place_order(
            market_id=market_id,
            token_id=opportunity["token_id"],
            side=side,
            size=size,
            price=opportunity["price"],
            confidence=opportunity["confidence"],
            prediction=opportunity.get("confidence"),
        )

        if not order.get("success"):
            logger.info(
                "EnsembleBot trade REJECTED: %s %s conf=%.2f%% size=%.1f price=%.4f reason=%s",
                market_id, side, opportunity["confidence"] * 100, size,
                opportunity["price"], order.get("error", "unknown"),
            )
            return

        if order.get("success"):
            # Side-bias tracking: record this trade's side for bias detection
            self._recent_trade_sides.append(side)
            if len(self._recent_trade_sides) > self._SIDE_BIAS_WINDOW:
                self._recent_trade_sides = self._recent_trade_sides[-self._SIDE_BIAS_WINDOW:]
            if len(self._recent_trade_sides) >= 10:
                _no_pct = self._recent_trade_sides.count("NO") / len(self._recent_trade_sides)
                _yes_pct = 1.0 - _no_pct
                _dominant = "NO" if _no_pct > _yes_pct else "YES"
                _dom_pct = max(_no_pct, _yes_pct)
                if _dom_pct > self._SIDE_BIAS_MAX_PCT:
                    logger.warning(
                        "EnsembleBot SIDE BIAS: %d/%d trades are %s (%.0f%%) — check model calibration",
                        int(_dom_pct * len(self._recent_trade_sides)),
                        len(self._recent_trade_sides), _dominant, _dom_pct * 100,
                    )

            logger.info(
                "Ensemble trade executed: %s %s confidence %.2f%% edge %.2f%%",
                market_id,
                side,
                opportunity["confidence"] * 100,
                opportunity.get("edge", 0) * 100,
                market=market_id,
                side=side,
                confidence=opportunity["confidence"],
            )
            # Mark prediction_log entry as traded
            try:
                if hasattr(self.base_engine, "db") and self.base_engine.db:
                    await self.base_engine.db.mark_prediction_traded(
                        market_id=str(market_id),
                        token_id=str(opportunity["token_id"]),
                        trade_side=side,
                        trade_size=float(order.get("filled", size)),
                        trade_price=float(order.get("price", opportunity["price"])),
                    )
            except Exception as e:
                logger.debug("Failed to mark prediction as traded: %s", e)

            # R2: Store signal context captured during apply_signal_enhancements for ML training.
            # Fire-and-forget background task — never blocks the trade execution path.
            _trade_id = order.get("trade_id") or order.get("order_id")
            if _trade_id:
                _t = asyncio.create_task(
                    self.store_pending_trade_signals(str(_trade_id), str(market_id))
                )
                _t.add_done_callback(lambda t: self._on_bg_task_done(t, "store_trade_signals"))

    async def optimize_weights(self, backtest_results: List[Dict]) -> None:
        """
        Optimize ensemble model weights from backtest results or per-model accuracy.

        T2/L8 FIX: Now covers ALL active models (not just 3).
        Strategy: Uses per-model Brier scores from backtest_results when available,
        then falls back to keeping current weights (no destructive 3-model grid search).
        Preserves existing weights for any model not in the search results.
        """
        if not backtest_results:
            logger.warning("optimize_weights: no backtest results")
            return

        all_model_names = list(self.model_weights.keys())
        logger.info("Optimizing ensemble weights for %d models", len(all_model_names))

        # Strategy: If backtest_results contain per-model Brier/accuracy, use inverse-Brier weighting
        per_model_scores: Dict[str, float] = {}
        for r in backtest_results:
            if not isinstance(r, dict):
                continue
            model_name = r.get("model_name")
            brier = r.get("brier_score")
            accuracy = r.get("accuracy")
            if model_name and model_name in self.model_weights:
                if brier is not None:
                    per_model_scores[model_name] = max(0.01, 1.0 - float(brier))
                elif accuracy is not None:
                    per_model_scores[model_name] = max(0.01, float(accuracy))

        if len(per_model_scores) >= max(1, len(all_model_names) // 2):
            # Enough per-model data: use inverse-Brier (or accuracy) weighted allocation
            total_score = sum(per_model_scores.values())
            if total_score > 0:
                new_weights = dict(self.model_weights)  # Preserve existing for missing models
                for name, score in per_model_scores.items():
                    new_weights[name] = score / total_score
                # Normalize so all weights sum to 1.0
                w_sum = sum(new_weights.values())
                if w_sum > 0:
                    new_weights = {k: v / w_sum for k, v in new_weights.items()}
                self.model_weights = new_weights
                logger.info(
                    "Weights optimized (per-model scoring, %d models): top=%s",
                    len(per_model_scores),
                    {k: round(v, 3) for k, v in sorted(self.model_weights.items(), key=lambda x: -x[1])[:5]},
                )
                # I60: Propagate updated weights to prediction_engine.model_weights in real-time.
                # Both EnsembleBot and PredictionEngine maintain their own weight dicts;
                # without this sync, pe.model_weights drifts stale between retrains.
                _pe = getattr(getattr(self, "base_engine", None), "prediction_engine", None)
                if _pe and hasattr(_pe, "model_weights"):
                    _pe.model_weights = dict(self.model_weights)
                    logger.debug(
                        "I60: propagated %d updated weights to prediction_engine (no restart needed)",
                        len(self.model_weights),
                    )
                return

        # Fallback: insufficient per-model data — keep current weights (no destructive overwrite)
        avg_sharpe = sum(
            r.get("sharpe_ratio", 0) for r in backtest_results if isinstance(r, dict)
        ) / max(len(backtest_results), 1)
        logger.info(
            "optimize_weights: insufficient per-model data (%d/%d). Keeping current weights (avg Sharpe=%.2f).",
            len(per_model_scores), len(all_model_names), avg_sharpe,
        )
