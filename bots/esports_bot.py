"""
EsportsBot — Pre-game + live in-play esports trading bot.

All 8 game titles: LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, Rocket League.
Pre-game: prediction engine + model-vs-market edge validation.
Live: win probability model updates on game events via PandaScore.

Requires PANDASCORE_API_KEY — fails fast if missing.
Scan interval: 120s default, 10s during live matches.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from structlog import get_logger

from bots.base_bot import BaseBot
from base_engine.data.daily_counter import increment_counter as _inc_daily, restore_counters as _restore_daily
from base_engine.features.aggregation import extremized_geometric_mean
from base_engine.monitoring.alerting import AlertSeverity
from config.settings import settings

logger = get_logger()


class EsportsBot(BaseBot):
    """
    Pre-game + live in-play esports trading bot.

    Covers match_winner, map_winner, tournament_winner, total_maps
    across LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, and Rocket League on Polymarket.
    """

    def __init__(self, base_engine):
        super().__init__("EsportsBot", base_engine)

        # Fail fast if PandaScore API key not configured
        api_key = getattr(settings, "PANDASCORE_API_KEY", None)
        if not api_key:
            raise ValueError(
                "EsportsBot requires PANDASCORE_API_KEY — set it in .env"
            )

        self._api_key = api_key
        self._pandascore = None       # Lazy init in start()
        self._patch_drift = None      # PatchDriftDetector instance
        self._market_scanner = None   # EsportsMarketScanner instance
        self._market_service = None   # EsportsMarketService instance (Commit 4/5)
        self._lol_model = None       # LoLWinModel
        self._cs2_model = None       # CS2EconomyModel
        self._dota2_model = None     # Dota2Model
        self._valorant_model = None  # ValorantModel
        self._trainer = None         # EsportsModelTrainer
        self._opendota = None        # OpenDotaClient (Dota2 enrichment)
        self._aligulac = None        # AligulacClient (SC2 ratings blend)
        self._ballchasing = None     # BallchasingClient (RL replay stats)
        self._cross_game_model = None  # XGBClassifier (cross-game meta model)
        self._bg_train_tasks: Dict[str, asyncio.Task] = {}  # game → background train task

        # Per-game/tournament/team exposure tracking (USD deployed)
        self._game_exposure: Dict[str, float] = {}        # game → USD
        self._tournament_exposure: Dict[str, float] = {}  # tournament_id → USD
        self._team_exposure: Dict[str, float] = {}        # team_name → USD

        # Live match tracking
        self._live_matches: Dict[str, Dict] = {}  # match_id → PandaScore match data
        self._last_live_refresh = 0.0
        self._live_refresh_failures: int = 0       # consecutive PandaScore refresh failures

        # Prediction cache for WS reactive path
        self._prediction_cache: Dict[str, Dict] = {}  # market_id → {prob, ts, game}

        # Token map: market_id → {"yes": yes_token_id, "no": no_token_id}
        # Populated during scan, used by WS path to identify YES vs NO token prices.
        self._market_token_map: Dict[str, Dict[str, str]] = {}

        # Market→game mapping: populated on trade execution, used for game
        # exposure decrement on exit.  Survives prediction_cache expiry (1h TTL).
        self._market_game: Dict[str, str] = {}

        # Race-condition guard: prevents concurrent WS trades on the same market.
        # Lifecycle: added at the top of _handle_ws_price_update(), removed in
        # the `finally` block — always, even on exception.
        # This set is empty between trades; it never accumulates entries.
        self._ws_pending_trades: set = set()

        # Track data collection attempts per game: game → attempt count (max 3 retries)
        self._collection_attempted: Dict[str, int] = {}

        # Latency tracker: WS price move time vs last PandaScore refresh (bounded)
        self._latency_samples: List[float] = []  # seconds since last PandaScore refresh
        self._max_latency_samples = 100

        # Prediction log dedup: market_id → (logged_prob, logged_ts)
        # Skip re-logging if prediction unchanged for same market within 10 min
        self._prediction_log_cache: Dict[str, tuple] = {}

        # E4: Monitoring thresholds — per-game Brier alerts
        self._monitoring_halted_games: set = set()  # games halted by monitoring
        self._monitoring_last_check: float = 0.0
        self._monitoring_check_interval: float = 600.0  # 10 minutes

        # Glicko-2 trackers for "easy mode" pre-game predictions
        self._glicko2_trackers: Dict[str, Any] = {}  # game → Glicko2Tracker
        self._team_name_to_id: Dict[str, str] = {}    # lowercased team name → PandaScore ID
        self._backfill_attempted: set = set()            # "game:name" keys already queried this session
        self._backfill_calls_this_scan: int = 0          # reset each scan; capped at 5
        self._max_backfills_per_scan: int = 5
        self._team_fail_logged: set = set()              # rate-limit team_match_fail logs (per session)
        self._calibration_ece: Dict[str, float] = {}     # game → latest ECE (updated every 10 min)
        self._edge_decay_data: Dict[str, Dict] = {}      # game → latest edge decay analysis
        self._game_kelly_mult: Dict[str, float] = {}     # game → Kelly multiplier (Brier-based)

        # Session 82-83: Calibration pipeline (fitted in _check_monitoring_thresholds)
        self._focal_calibrator: Any = None       # FocalTemperatureCalibrator instance
        self._bias_decomp: Any = None            # EsportsBiasDecomposition instance
        self._horizon_calibrator: Any = None     # HorizonBiasCalibrator instance
        self._onnx_cross_game_session: Any = None  # ONNX InferenceSession for cross-game XGB
        # Per-game ONNX sessions for faster inference (Session 83)
        self._onnx_lol_session: Any = None
        self._onnx_cs2_session: Any = None
        self._onnx_dota2_session: Any = None
        self._onnx_valorant_session: Any = None
        # Session 83: Per-game EGM d, edge decay multiplier, conformal intervals
        self._game_egm_d: Dict[str, float] = {}  # game → dynamic d (overrides _egm_d)
        self._edge_decay_mult: Dict[str, float] = {}  # game → sizing multiplier from edge decay
        self._conformal_predictor: Any = None  # ConformalPredictor (cross-game XGB)
        self._tabpfn_predictor: Any = None  # TabPFN ensemble for sparse games
        self._cot_validator: Any = None  # CoT LLM validator for high-edge trades

        # Parallel analysis (Item 6)
        _concurrency = int(getattr(settings, "ESPORTS_ANALYSIS_CONCURRENCY", 10))
        self._analysis_semaphore = asyncio.Semaphore(_concurrency)
        self._trade_lock = asyncio.Lock()  # serializes exposure-mutating trade execution

        # Settings
        # "Easy mode": relaxed thresholds until models graduate, then tighten.
        # Graduation = accuracy >= 55% + brier <= 0.24 on holdout.
        self._models_graduated = False
        self._min_edge = float(getattr(settings, "ESPORTS_MIN_EDGE", 0.05))  # 5% easy mode
        self._min_confidence = float(getattr(settings, "ESPORTS_MIN_CONFIDENCE", 0.52))  # easy mode
        self._max_edge = float(getattr(settings, "ESPORTS_MAX_EDGE", 0.20))  # 20% sanity cap
        self._egm_d = float(getattr(settings, "ESPORTS_EGM_D", 1.5))  # EGM extremization factor
        self._maker_timeout = float(
            getattr(settings, "ESPORTS_MAKER_FALLBACK_TIMEOUT_S", 3.0)
        )
        self._scan_count: int = 0  # P1.2: periodic outcome backfill counter
        self._exposure_restored: bool = False  # P0: seed exposure dicts from DB on first scan

        # A1+A8: Daily loss limit + drawdown halt
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: Optional[str] = None
        self._daily_loss_limit = float(getattr(settings, "ESPORTS_DAILY_LOSS_LIMIT", 500.0))
        self._drawdown_halted: bool = False

        # A3: Dynamic Kelly graduation
        self._kelly_graduated: bool = False  # True when 50+ resolved + Brier<0.24

    def _get_scan_interval_seconds(self) -> float:
        """A4: Tournament-aware scan intervals.

        10s during live matches, 60s during active tournaments, 120s otherwise.
        """
        if self._live_matches:
            return float(getattr(settings, "SCAN_INTERVAL_ESPORTS_LIVE", 10))
        # A4: Tighter scan when we have open positions (for stop-loss monitoring)
        try:
            og = getattr(self.base_engine, "order_gateway", None)
            if og is not None:
                bot_positions = getattr(og, "_open_position_markets", {})
                if isinstance(bot_positions, dict) and bot_positions.get(self.bot_name):
                    return 60.0
        except Exception:
            pass
        return float(getattr(settings, "SCAN_INTERVAL_ESPORTS", 120))

    async def start(self) -> None:
        """Initialize data clients and models, then start scan loop."""
        from esports.data.pandascore_client import PandaScoreClient
        from esports.models.patch_drift import PatchDriftDetector
        from esports.markets.esports_market_scanner import EsportsMarketScanner

        self._pandascore = PandaScoreClient(api_key=self._api_key)
        await self._pandascore.init()

        riot_key = getattr(settings, "RIOT_API_KEY", None)
        riot_client = None
        if riot_key:
            from esports.data.riot_api_client import RiotApiClient
            riot_client = RiotApiClient(api_key=riot_key)
            await riot_client.init()

        self._patch_drift = PatchDriftDetector(riot_client=riot_client)

        # OpenDota client — free Dota2 hero + team form data (no auth needed)
        try:
            from esports.data.opendota_client import OpenDotaClient
            self._opendota = OpenDotaClient()
            logger.info("EsportsBot: OpenDota client initialized")
        except Exception as exc:
            self._opendota = None
            logger.warning("EsportsBot: OpenDota client not available", error=str(exc))

        # Aligulac client — SC2 Elo ratings + match predictions (free key)
        aligulac_key = getattr(settings, "ALIGULAC_API_KEY", None)
        if aligulac_key:
            try:
                from esports.data.aligulac_client import AligulacClient
                self._aligulac = AligulacClient(api_key=aligulac_key)
                logger.info("EsportsBot: Aligulac client initialized")
            except Exception as exc:
                self._aligulac = None
                logger.warning("EsportsBot: Aligulac client not available", error=str(exc))
        else:
            self._aligulac = None

        # Ballchasing client — RL replay stats (free key)
        ballchasing_key = getattr(settings, "BALLCHASING_API_KEY", None)
        if ballchasing_key:
            try:
                from esports.data.ballchasing_client import BallchasingClient
                self._ballchasing = BallchasingClient(api_key=ballchasing_key)
                logger.info("EsportsBot: Ballchasing client initialized")
            except Exception as exc:
                self._ballchasing = None
                logger.warning("EsportsBot: Ballchasing client not available", error=str(exc))
        else:
            self._ballchasing = None

        db = getattr(self.base_engine, "db", None)
        # market_service passed below after initialization (if successful)
        self._market_scanner = EsportsMarketScanner(db=db)

        # Load models — try saved first, then train if data available
        try:
            from esports.models.lol_win_model import LoLWinModel
            self._lol_model = LoLWinModel()
            if not self._lol_model.load():
                logger.info("EsportsBot: no saved LoL model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: LoL model not loaded (no saved model yet)")

        try:
            from esports.models.cs2_economy_model import CS2EconomyModel
            self._cs2_model = CS2EconomyModel()
            if not self._cs2_model.load():
                logger.info("EsportsBot: no saved CS2 model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: CS2 model not loaded")

        try:
            from esports.models.dota2_model import Dota2Model
            self._dota2_model = Dota2Model()
            if not self._dota2_model.load():
                logger.info("EsportsBot: no saved Dota2 model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: Dota2 model not loaded")

        try:
            from esports.models.valorant_model import ValorantModel
            self._valorant_model = ValorantModel()
            if not self._valorant_model.load():
                logger.info("EsportsBot: no saved Valorant model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: Valorant model not loaded")

        # Initialize trainer for periodic retraining
        try:
            from esports.models.esports_trainer import EsportsModelTrainer
            self._trainer = EsportsModelTrainer(pandascore_client=self._pandascore)
        except Exception:
            logger.debug("EsportsBot: trainer not available")

        # Load cross-game XGBoost meta model (if previously trained)
        self._load_cross_game_model()

        # Initialize calibration pipeline (Session 82)
        try:
            from base_engine.features.calibration import FocalTemperatureCalibrator
            self._focal_calibrator = FocalTemperatureCalibrator(db=db)
            logger.info("EsportsBot: FocalTemperatureCalibrator initialized")
        except Exception as exc:
            logger.debug("EsportsBot: FocalTemp not available", error=str(exc))

        try:
            from esports.calibration.bias_decomposition import EsportsBiasDecomposition
            self._bias_decomp = EsportsBiasDecomposition()
            logger.info("EsportsBot: EsportsBiasDecomposition initialized")
        except Exception as exc:
            logger.debug("EsportsBot: BiasDecomp not available", error=str(exc))

        try:
            from base_engine.features.calibration import HorizonBiasCalibrator
            self._horizon_calibrator = HorizonBiasCalibrator(db=db)
            logger.info("EsportsBot: HorizonBiasCalibrator initialized")
        except Exception as exc:
            logger.debug("EsportsBot: HorizonBias not available", error=str(exc))

        # Session 83: Load per-game ONNX sessions
        self._load_per_game_onnx_sessions()

        # Session 83: Initialize conformal predictor for conservative Kelly
        try:
            from esports.models.conformal_wrapper import ConformalPredictor
            self._conformal_predictor = ConformalPredictor(alpha=0.10)
            logger.info("EsportsBot: ConformalPredictor initialized (alpha=0.10)")
        except Exception as exc:
            logger.debug("EsportsBot: ConformalPredictor not available", error=str(exc))

        # Session 83: Initialize TabPFN for sparse games
        try:
            from esports.models.tabpfn_ensemble import TabPFNEnsemble
            self._tabpfn_predictor = TabPFNEnsemble()
            logger.info("EsportsBot: TabPFN ensemble initialized")
        except Exception as exc:
            logger.debug("EsportsBot: TabPFN not available", error=str(exc))

        # Session 83: Initialize CoT validator for high-edge trades
        try:
            from esports.models.cot_validator import CoTValidator
            self._cot_validator = CoTValidator()
        except Exception as exc:
            logger.debug("EsportsBot: CoT validator not available", error=str(exc))

        lol_trained = self._lol_model is not None and self._lol_model.is_trained
        cs2_trained = self._cs2_model is not None and self._cs2_model.is_trained

        # Build Glicko-2 trackers and team name mapping for "easy mode" pre-game predictions.
        # These provide real signal (63% accuracy per EsportsBench) while ML models train.
        await self._init_glicko2_trackers(db)

        # Initialize dedicated esports market service — bypasses broken Gamma API.
        # Queries DB directly for esports markets + background CLOB price refresh.
        try:
            from esports.markets.esports_market_service import EsportsMarketService
            poly_client = getattr(self.base_engine, "client", None)
            self._market_service = EsportsMarketService(db=db, polymarket_client=poly_client)
            self._market_service.start_background_refresh()
            # Wire to scanner so it also benefits from DB-backed market discovery
            if self._market_scanner:
                self._market_scanner._market_service = self._market_service
        except Exception as exc:
            logger.warning("EsportsBot: market service init failed", error=str(exc))

        # P2.2: Wire trainer into LearningScheduler for unified retrain scheduling
        _sched = getattr(self.base_engine, "scheduler", None)
        if _sched is not None and self._trainer is not None:
            _sched.esports_trainer = self._trainer

        logger.info(
            "EsportsBot: initialized",
            pandascore=True,
            riot_api=bool(riot_key),
            lol_model_trained=lol_trained,
            cs2_model_trained=cs2_trained,
            glicko2_teams=len(self._team_name_to_id),
            market_service=self._market_service is not None,
        )

        await super().start()

    async def stop(self) -> None:
        """Clean up HTTP clients and market service."""
        if self._market_service:
            await self._market_service.close()
        if self._pandascore:
            await self._pandascore.close()
        await super().stop()

    async def on_price_update(self, event: dict) -> None:
        """
        React to WS price updates — cross-reference cached prediction vs new price.

        LATENCY FIX: Check prediction cache BEFORE calling super().on_price_update().
        The base_bot's on_price_update() does latency logging, price caching, and
        metrics for EVERY WS event (~26K markets). By skipping super() for non-esports
        markets, we reduce processing from ~100 events/sec to ~1/sec for this bot.

        TOKEN FIX: WS events carry a token_id (YES or NO token). Previous code keyed
        _ws_prev_prices by market_id only, so YES (0.84) and NO (0.06) prices
        overwrote each other → false 0.84→0.06 "oscillation" and fake 44-63% edges.
        Now uses _market_token_map to identify which token the price belongs to and
        converts to YES-equivalent before computing edge.
        """
        if not self.running:
            return

        market_id = event.get("market_id", "")
        if not market_id:
            return

        # Early exit: only process events for markets we've already analyzed.
        # This MUST come before super() to avoid processing all 26K markets.
        cached = self._prediction_cache.get(market_id)
        if not cached:
            return  # Skip super() for non-esports markets — avoids latency overhead

        # Only call super() for esports markets we care about
        await super().on_price_update(event)

        token_id = event.get("token_id", "")
        new_price = float(event.get("price", 0))
        if new_price <= 0 or not token_id:
            return

        # Identify which token this price update is for (YES or NO).
        # _market_token_map is populated during scan_and_trade → analyze_opportunity.
        token_map = self._market_token_map.get(market_id)
        if not token_map:
            return  # Haven't scanned this market yet — skip

        yes_token_id = token_map.get("yes", "")
        no_token_id = token_map.get("no", "")

        if token_id == yes_token_id:
            yes_price = new_price
        elif token_id == no_token_id:
            yes_price = 1.0 - new_price  # Convert NO price to YES-equivalent
        else:
            return  # Unknown token for this market — skip

        # Track latency: time since last PandaScore refresh
        if self._last_live_refresh > 0:
            latency = time.monotonic() - self._last_live_refresh
            if len(self._latency_samples) >= self._max_latency_samples:
                self._latency_samples.pop(0)
            self._latency_samples.append(latency)

        # Significance threshold — keyed by token_id to avoid YES/NO cross-contamination
        threshold = float(getattr(settings, "ESPORTS_WS_PRICE_CHANGE_PCT", 0.01))
        if not hasattr(self, "_ws_prev_prices"):
            self._ws_prev_prices: dict = {}
        old_yes_price = self._ws_prev_prices.get(token_id)
        self._ws_prev_prices[token_id] = yes_price
        if old_yes_price is None or abs(yes_price - old_yes_price) / max(old_yes_price, 0.01) < threshold:
            return

        # Cooldown
        now = time.monotonic()
        if not hasattr(self, "_ws_cooldowns"):
            self._ws_cooldowns: dict = {}
        cooldown = int(getattr(settings, "ESPORTS_WS_COOLDOWN_SECONDS", 10))
        if now - self._ws_cooldowns.get(market_id, 0) < cooldown:
            return
        self._ws_cooldowns[market_id] = now

        # Recalculate edge with correctly-identified YES price
        model_prob = cached["prob"]  # Always P(YES)
        game = cached.get("game", "")
        edge = model_prob - yes_price

        if abs(edge) < self._min_edge:
            return

        # Edge sanity cap — Glicko-2 producing >20% edge is suspicious
        if abs(edge) > self._max_edge:
            logger.debug(
                "EsportsBot WS: edge exceeds sanity cap",
                market_id=market_id,
                edge=round(edge, 4),
                max_edge=self._max_edge,
            )
            return

        side = "YES" if edge > 0 else "NO"
        trade_token_id = yes_token_id if side == "YES" else no_token_id
        trade_price = yes_price if side == "YES" else (1.0 - yes_price)

        # Confluence gate
        confluence = self._compute_confluence_score(
            model_edge=edge,
            side=side,
            token_id=trade_token_id,
            market_id=market_id,
            prediction_ts=cached.get("ts", 0),
        )
        confluence_min = float(getattr(settings, "ESPORTS_CONFLUENCE_MIN", 0.60))
        if confluence < confluence_min:
            return

        # Position check + pending trade guard (race condition prevention)
        if market_id in self._ws_pending_trades:
            return
        og = getattr(self.base_engine, "order_gateway", None)
        if og is not None and og.has_open_position(self.bot_name, str(market_id)):
            return

        self._ws_pending_trades.add(market_id)
        try:
            confidence = model_prob if side == "YES" else (1.0 - model_prob)
            opp = {
                "type": "esports_ws_reactive",
                "market_id": market_id,
                "token_id": trade_token_id,
                "side": side,
                "price": trade_price,
                "confidence": confidence,
                "prediction": model_prob,
                "edge": round(abs(edge), 4),
                "game": game,
                "market_type": "match_winner",
                "confluence": confluence,
            }
            logger.info(
                "EsportsBot WS reactive trade",
                market_id=market_id,
                side=side,
                yes_price=round(yes_price, 4),
                edge=round(abs(edge), 4),
                confluence=confluence,
            )
            await self._execute_esports_trade(opp)
            # After successful execution, extend cooldown to one full scan cycle
            # to prevent re-triggering on the same market before next scan
            self._ws_cooldowns[market_id] = time.monotonic() + 110  # +110 so total ~120s
        except Exception as exc:
            logger.debug("EsportsBot WS reactive failed", error=str(exc))
        finally:
            self._ws_pending_trades.discard(market_id)

    def _cleanup_caches(self) -> None:
        """Evict stale entries from in-memory caches to prevent unbounded growth."""
        now = time.monotonic()
        # _prediction_cache: entries have 'ts' field — evict > 1 hour old
        stale = [k for k, v in self._prediction_cache.items() if now - v.get("ts", 0) > 3600]
        for k in stale:
            del self._prediction_cache[k]
        # _prediction_log_cache: entries are (confidence, ts) tuples — evict > 1 hour
        stale_log = [k for k, v in self._prediction_log_cache.items()
                     if isinstance(v, tuple) and len(v) >= 2 and now - v[1] > 3600]
        for k in stale_log:
            del self._prediction_log_cache[k]
        # _market_token_map: cap at 1000 entries — bulk clear when oversized
        if len(self._market_token_map) > 1000:
            self._market_token_map.clear()
        if stale or stale_log:
            logger.debug("esports_cache_cleanup", prediction_evicted=len(stale),
                         log_evicted=len(stale_log), token_map_size=len(self._market_token_map))

    async def scan_and_trade(self) -> None:
        """
        Main scan loop body.

        1. Check patch observation mode
        2. Refresh live match data
        3. Get esports markets from Polymarket
        4. Analyze each market for edge
        """
        self._scan_count += 1
        self._backfill_calls_this_scan = 0
        if self._cot_validator is not None:
            self._cot_validator.reset_scan_counter()
        if self._scan_count % 100 == 0:
            self._cleanup_caches()
        db = getattr(self.base_engine, "db", None)

        # P0: Restore exposure counters from today's paper_trades on first scan
        if not self._exposure_restored:
            await self._restore_exposure_from_db(db)

        # A1: Restore daily P&L + reset at UTC midnight
        # Refresh every 10 scans to capture mid-day resolutions
        if self._scan_count % 10 == 0:
            self._daily_pnl_date = None
        await self._restore_daily_pnl_from_db(db)

        # Fetch positions once for stop-loss + re-evaluation (avoids duplicate DB query)
        _positions = None
        try:
            if db:
                _positions = await db.get_open_positions_for_bot(self.bot_name)
        except Exception:
            _positions = None

        # A1+A8: Block trading if daily loss limit or drawdown halt active
        if self._check_daily_loss_limit():
            logger.info("esportsbot_trading_blocked",
                        daily_pnl=round(self._daily_pnl, 2),
                        limit=-self._daily_loss_limit)
            # B1: Still check stop-loss exits even when new entries blocked
            await self._check_and_execute_exits(db, positions=_positions)
            return

        # B1: Check stop-loss and max hold time exits
        await self._check_and_execute_exits(db, positions=_positions)

        # A2: Re-evaluate open positions (every 5 scans, ~10 min)
        if self._scan_count % 5 == 0:
            await self._reevaluate_open_positions(db, positions=_positions)

        # A3: Dynamic Kelly graduation check (every 10 scans)
        if self._scan_count % 10 == 0:
            await self._check_kelly_graduation(db)

        # E4: Monitoring thresholds — check per-game Brier and emit alerts
        await self._check_monitoring_thresholds(db)

        # Step 0: Auto-retrain models in background (non-blocking)
        # Clean up completed background training tasks — retrieve exceptions to
        # prevent "Task exception was never retrieved" warnings.
        for game_key in list(self._bg_train_tasks):
            task = self._bg_train_tasks[game_key]
            if task.done():
                if not task.cancelled():
                    exc = task.exception()
                    if exc:
                        logger.error("bg_train_task_failed", game=game_key,
                                     error=str(exc), task_name=task.get_name())
                del self._bg_train_tasks[game_key]

        # Gather smart retrain trigger data (lightweight, degrades gracefully)
        _smart_brier: Dict[str, float] = {}
        _smart_row_count: Dict[str, int] = {}
        try:
            from esports.data.esports_db import get_rolling_accuracy
            for _rg in ("lol", "cs2"):
                _acc = await get_rolling_accuracy(db, _rg, bot_name="EsportsBot")
                if _acc and _acc["total"] >= 10:
                    _smart_brier[_rg] = _acc["brier_score"]
        except Exception as _e:
            logger.debug("esportsbot_smart_retrain_brier_failed", error=str(_e))
        try:
            from sqlalchemy import text as _sa_text
            async with db.get_session() as _sess:
                for _rg in ("lol", "cs2"):
                    _cnt = await _sess.execute(
                        _sa_text("SELECT COUNT(*) FROM esports_training_data WHERE game = :game"),
                        {"game": _rg},
                    )
                    _smart_row_count[_rg] = int(_cnt.scalar() or 0)
        except Exception as _e:
            logger.debug("esportsbot_training_data_count_failed", error=str(_e))
        _lol_patch = ""
        if self._patch_drift:
            _lol_patch = self._patch_drift._known_patches.get("lol", "")

        for _retrain_game in ("lol", "cs2"):
            if (self._trainer
                    and self._trainer.needs_retrain(
                        _retrain_game,
                        current_brier=_smart_brier.get(_retrain_game),
                        current_row_count=_smart_row_count.get(_retrain_game),
                        current_patch=_lol_patch if _retrain_game == "lol" else None,
                    )
                    and _retrain_game not in self._bg_train_tasks):
                # Rebuild Glicko-2 trackers after training if this game
                # has no tracker yet (e.g. LoL on first data collection)
                _needs_glicko = self._glicko2_trackers.get(_retrain_game) is None
                self._bg_train_tasks[_retrain_game] = asyncio.create_task(
                    self._train_in_background(_retrain_game, db, init_glicko=_needs_glicko),
                    name=f"retrain_{_retrain_game}",
                )

        # E7: Cross-game XGBoost retrain (pools all 8 games)
        if (self._trainer
                and self._trainer.needs_retrain("cross_game")
                and "cross_game" not in self._bg_train_tasks):
            _cg_task = asyncio.create_task(
                self._train_in_background("cross_game", db),
                name="retrain_cross_game",
            )

            def _on_retrain_done(t: asyncio.Task) -> None:
                if t.cancelled():
                    logger.warning("esports_cross_game_retrain_cancelled")
                elif t.exception():
                    logger.error("esports_cross_game_retrain_failed", error=str(t.exception()))

            _cg_task.add_done_callback(_on_retrain_done)
            self._bg_train_tasks["cross_game"] = _cg_task

        # Step 0a: Collect historical data for games missing Glicko-2 trackers (one-shot)
        for _game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
            if (_game not in self._glicko2_trackers
                    and self._collection_attempted.get(_game, 0) < 3
                    and self._trainer
                    and _game not in self._bg_train_tasks):
                self._collection_attempted[_game] = self._collection_attempted.get(_game, 0) + 1
                self._bg_train_tasks[_game] = asyncio.create_task(
                    self._train_in_background(_game, db, init_glicko=True),
                    name=f"collect_{_game}",
                )

        # Step 0b: Check rolling accuracy — auto-disable if below threshold
        min_acc = float(getattr(settings, "ESPORTS_MIN_ACCURACY_TO_TRADE", 0.52))
        try:
            from esports.data.esports_db import get_rolling_accuracy
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                acc_data = await get_rolling_accuracy(db, game, bot_name="EsportsBot")
                if acc_data and acc_data["total"] >= 30 and acc_data["accuracy"] < min_acc:
                    logger.warning(
                        "EsportsBot: accuracy below threshold — triggering retrain",
                        game=game,
                        accuracy=round(acc_data["accuracy"], 3),
                        threshold=min_acc,
                        brier=round(acc_data["brier_score"], 4),
                    )
                    if self._trainer:
                        self._trainer._last_train_time.pop(game, None)
        except Exception as exc:
            logger.warning("esportsbot_accuracy_check_failed", error=str(exc))

        # Steps 1-3 run in parallel — they are fully independent
        async def _step_patch_drift():
            if self._patch_drift:
                try:
                    drift_status = await asyncio.wait_for(
                        self._patch_drift.check_all_games(), timeout=10.0
                    )
                    for game, status in drift_status.items():
                        if status.get("halted"):
                            logger.warning(
                                "EsportsBot: game halted (calibration failure)",
                                game=game,
                            )
                except (asyncio.TimeoutError, Exception) as exc:
                    logger.debug("EsportsBot: patch drift check failed", error=str(exc))

        async def _step_get_markets():
            if self._market_service:
                return await self._market_service.get_tradeable_esports_markets()
            m = await self.base_engine.get_markets(active=True, limit=200)
            return self.base_engine.filter_markets_for_trading(m, categories=["esports"])

        _, _, esports_markets = await asyncio.gather(
            _step_patch_drift(),
            self._refresh_live_matches(),
            _step_get_markets(),
        )
        self._last_scan_markets = len(esports_markets) if esports_markets else 0
        if not esports_markets:
            return

        # Step 4: Analyze each market
        _opps = 0
        _trades = 0
        _skipped_position = 0
        _by_game: dict = {}  # markets per game for diagnostic
        # B4: Waterfall diagnostic counters (cross-pollinated from MirrorBot S48)
        self._wf = {"no_game": 0, "no_price": 0, "no_token": 0, "halted": 0,
                     "exposure_cap": 0, "observation": 0, "no_prediction": 0,
                     "low_edge": 0, "edge_cap": 0, "low_confidence": 0,
                     "low_confluence": 0, "passed": 0}
        self._exposure_cap_logged: set = set()  # per-scan: games already logged for cap hit
        og = getattr(self.base_engine, "order_gateway", None)

        # Pre-compute per-game counts (sync, before parallel analysis)
        for market in esports_markets:
            _q = str(market.get("question", "")).lower()
            _g = self._detect_game(_q) or "other"
            _by_game[_g] = _by_game.get(_g, 0) + 1

        # Parallel market analysis with bounded concurrency
        async def _analyze_one(m: Dict) -> tuple:
            """Analyze one market; returns (opps, trades, skips)."""
            async with self._analysis_semaphore:
                mid = str(m.get("id", ""))
                if og and mid and og.has_open_position(self.bot_name, mid):
                    return (0, 0, 1)
                opp = await self.analyze_opportunity(m)
                if opp:
                    async with self._trade_lock:
                        await self._execute_esports_trade(opp)
                    return (1, 1, 0)
                return (0, 0, 0)

        results = await asyncio.gather(
            *[_analyze_one(m) for m in esports_markets],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.debug("EsportsBot scan error: %s", r)
            else:
                _opps += r[0]
                _trades += r[1]
                _skipped_position += r[2]
        self._last_scan_opportunities = _opps
        self._last_scan_trades = _trades

        # Diagnostic log every scan — helps diagnose low trade rate
        # B4: Include waterfall funnel for pipeline visibility
        _wf_nonzero = {k: v for k, v in self._wf.items() if v > 0}
        logger.info(
            "esportsbot_scan_summary",
            markets=len(esports_markets),
            markets_by_game=_by_game,
            skipped_has_position=_skipped_position,
            opportunities=_opps,
            trades=_trades,
            live_matches=len(self._live_matches),
            halted_games=list(self._monitoring_halted_games) or None,
            min_confidence=self._min_confidence,
            min_edge=self._min_edge,
            waterfall=_wf_nonzero or None,
            backfills_this_scan=self._backfill_calls_this_scan,
        )

        # P1.2: Backfill actual_outcome for settled esports paper_trades every 10 scans
        # Non-financial bookkeeping — safe for fire-and-forget (updates prediction_log metadata)
        if self._scan_count % 10 == 0 and db is not None:
            asyncio.create_task(self._safe_backfill_outcomes(db))

    async def _safe_backfill_outcomes(self, db) -> None:
        """Background outcome backfill — non-blocking."""
        try:
            await self._backfill_esports_outcomes(db)
        except Exception as _exc:
            logger.debug("esportsbot_outcome_backfill_failed", error=str(_exc))

    async def _restore_exposure_from_db(self, db) -> None:
        """Seed _game_exposure from daily_counters on startup.

        Reads the write-through daily_counters table (migration 036) — the authoritative
        source for per-game exposure since every _execute_esports_trade() call writes
        through immediately. counter_date = CURRENT_DATE means this auto-resets at UTC midnight.

        Note: _tournament_exposure is NOT restored — tournament caps span multiple UTC days
        and daily_counters would reset them at midnight. Accept tournament exposure resets.
        """
        if self._exposure_restored:
            return
        self._exposure_restored = True
        if db is None or not getattr(db, "session_factory", None):
            return
        try:
            counters = await _restore_daily(db, "EsportsBot")
            for name, value in counters.items():
                if name.startswith("game_"):
                    game_key = name[5:]  # "game_cs2" → "cs2"
                    self._game_exposure[game_key] = value
            logger.info(
                "esports_exposure_restored",
                games=dict(self._game_exposure),
            )
        except Exception as exc:
            logger.warning("esports_restore_exposure_failed", error=str(exc))

    async def _restore_daily_pnl_from_db(self, db) -> None:
        """A1: Restore today's realized P&L from trade_events on startup."""
        if db is None:
            return
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._daily_pnl_date == today_str:
                return  # Already restored for today
            self._daily_pnl = 0.0
            self._daily_pnl_date = today_str
            self._drawdown_halted = False
            today_start = datetime.strptime(today_str, "%Y-%m-%d")
            async with db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0.0)
                    FROM trade_events
                    WHERE bot_name IN ('EsportsBot', 'EsportsLiveBot', 'EsportsSeriesBot')
                      AND event_type IN ('EXIT', 'RESOLUTION')
                      AND realized_pnl IS NOT NULL
                      AND event_time >= :today_start
                """), {"today_start": today_start})
                row = result.fetchone()
                if row and row[0] is not None:
                    self._daily_pnl = float(row[0])
            if self._daily_pnl != 0.0:
                logger.info("esports_daily_pnl_restored", pnl=round(self._daily_pnl, 2))
        except Exception as exc:
            logger.debug("esports_daily_pnl_restore_failed", error=str(exc))

    def _check_daily_loss_limit(self) -> bool:
        """A1+A8: Return True if trading should be blocked (loss limit or drawdown halt)."""
        if self._daily_pnl <= -self._daily_loss_limit:
            return True
        capital = float(getattr(settings, "ESPORTS_TOTAL_CAPITAL", 5000.0))
        if capital > 0 and self._daily_pnl < 0:
            drawdown_pct = abs(self._daily_pnl) / capital
            halt_pct = float(getattr(settings, "ESPORTS_DRAWDOWN_HALT_PCT", 0.20))
            if drawdown_pct >= halt_pct:
                if not self._drawdown_halted:
                    self._drawdown_halted = True
                    logger.warning("esportsbot_drawdown_halt",
                                   daily_pnl=round(self._daily_pnl, 2),
                                   drawdown_pct=round(drawdown_pct, 4))
                return True
        return False

    def _get_drawdown_kelly_factor(self) -> float:
        """A8: Reduce Kelly fraction when drawdown exceeds 10% of capital."""
        capital = float(getattr(settings, "ESPORTS_TOTAL_CAPITAL", 5000.0))
        if capital <= 0 or self._daily_pnl >= 0:
            return 1.0
        drawdown_pct = abs(self._daily_pnl) / capital
        reduce_pct = float(getattr(settings, "ESPORTS_DRAWDOWN_REDUCE_PCT", 0.10))
        if drawdown_pct >= reduce_pct:
            # Linear scale: 10% drawdown → 0.5x Kelly, 20% → 0x (halted before this)
            halt_pct = float(getattr(settings, "ESPORTS_DRAWDOWN_HALT_PCT", 0.20))
            factor = max(0.0, 1.0 - (drawdown_pct - reduce_pct) / max(halt_pct - reduce_pct, 0.01))
            return round(max(0.1, factor), 3)  # Floor at 10% Kelly
        return 1.0

    async def _check_and_execute_exits(self, db, positions=None) -> None:
        """B1: Stop-loss + max hold time exits for open EsportsBot positions.

        Queries the positions DB table (which has current_price updated every 10s
        by position_manager) instead of the in-memory _position_details cache
        (which lacks current_price and timestamp).
        """
        if db is None:
            return
        if positions is None:
            try:
                positions = await db.get_open_positions_for_bot(self.bot_name)
            except Exception as exc:
                logger.debug("esportsbot_exit_check_failed", error=str(exc))
                return
        if not positions:
            return

        stop_pct = float(getattr(settings, "ESPORTS_STOP_LOSS_PCT", 0.15))
        max_hold_h = float(getattr(settings, "ESPORTS_MAX_HOLD_HOURS", 72))
        now_utc = datetime.now(timezone.utc)
        positions_to_close: list = []

        for pos in positions:
            mid = pos.get("market_id", "")
            entry = float(pos.get("entry_price", 0.5) or 0.5)
            current = float(pos.get("current_price", entry) or entry)
            side = (pos.get("side") or "YES").upper()
            size = float(pos.get("size", 0) or 0)
            token_id = pos.get("token_id", "")

            if size <= 0 or not token_id:
                continue

            # P&L percentage using DB current_price (updated every 10s)
            if side == "YES":
                pnl_pct = (current - entry) / max(entry, 1e-6)
            else:
                pnl_pct = (entry - current) / max(entry, 1e-6)

            if pnl_pct <= -stop_pct:
                logger.info("esportsbot_stop_loss", market_id=mid,
                            pnl_pct=f"{pnl_pct:.2%}", side=side,
                            entry=round(entry, 4), current=round(current, 4))
                positions_to_close.append((pos, "stop_loss"))
                continue

            # Max hold time check using DB opened_at
            opened_at = pos.get("opened_at")
            if opened_at is not None:
                try:
                    if isinstance(opened_at, str):
                        opened_at = datetime.fromisoformat(opened_at)
                    if opened_at.tzinfo is None:
                        opened_at = opened_at.replace(tzinfo=timezone.utc)
                    hold_h = (now_utc - opened_at).total_seconds() / 3600
                    if hold_h >= max_hold_h:
                        logger.info("esportsbot_max_hold_exit", market_id=mid,
                                    hold_h=f"{hold_h:.1f}h")
                        positions_to_close.append((pos, "max_hold"))
                except Exception:
                    pass

        # Execute exits via SELL-side order using the SAME token_id
        # (selling back the token we hold, not buying the opposite side)
        for pos, reason in positions_to_close:
            mid = pos["market_id"]
            try:
                side = (pos.get("side") or "YES").upper()
                size = float(pos.get("size", 0) or 0)
                token_id = pos.get("token_id", "")
                current = float(pos.get("current_price", 0.5) or 0.5)
                if size <= 0 or not token_id:
                    continue
                # Exit by SELL order — bypasses risk_manager confidence check (line 448 order_gateway.py)
                _exit_result = await self.place_order(
                    market_id=mid, token_id=token_id, side="SELL",
                    size=size, price=current, confidence=0.0,
                )
                # If SELL failed (e.g. paper_trading_engine lost position on restart),
                # close the orphaned DB position directly to stop infinite stop-loss loop.
                if not _exit_result.get("success"):
                    logger.warning("esportsbot_exit_sell_failed", market_id=mid,
                                   error=_exit_result.get("error", "unknown"),
                                   reason=reason, size=round(size, 2))
                    _db = getattr(self.base_engine, "db", None)
                    if _db is not None:
                        try:
                            from sqlalchemy import text as _sa_text
                            async with _db.get_session() as _sess:
                                await _sess.execute(
                                    _sa_text("""
                                        UPDATE positions SET status = 'closed'
                                        WHERE market_id = :mid
                                          AND (bot_id = 'EsportsBot' OR source_bot = 'EsportsBot')
                                          AND status = 'open'
                                    """),
                                    {"mid": mid},
                                )
                                await _sess.commit()
                            # Also clean in-memory exposure tracker so position doesn't count
                            _og = getattr(self.base_engine, "order_gateway", None)
                            if _og is not None:
                                _og._track_position_close(self.bot_name, mid)
                            logger.info("esportsbot_orphan_position_closed", market_id=mid)
                        except Exception as _close_err:
                            logger.warning("esportsbot_orphan_close_failed", market_id=mid,
                                           error=str(_close_err))
                    self._market_game.pop(mid, None)
                    continue  # Skip exposure decrement — position was orphaned
                # B3: Decrement game exposure on exit
                # Primary: _market_game (populated on entry, survives cache expiry)
                # Fallback: prediction_cache (1h TTL, may be stale)
                game = self._market_game.get(mid, "")
                if not game:
                    game = self._prediction_cache.get(mid, {}).get("game", "")
                if game and game in self._game_exposure:
                    self._game_exposure[game] = max(0.0, self._game_exposure.get(game, 0.0) - size)
                    # Write-through decrement so daily_counters stays accurate across restarts
                    _db = getattr(self.base_engine, "db", None)
                    if _db is not None:
                        try:
                            await _inc_daily(_db, "EsportsBot", f"game_{game}", -size)
                        except Exception:
                            pass  # Non-critical: in-memory is authoritative intra-day
                # Clean up market→game mapping for exited position
                self._market_game.pop(mid, None)
                logger.info("esportsbot_exit_executed", market_id=mid, reason=reason,
                            exit_side="SELL", size=round(size, 2), game=game)
            except Exception as exc:
                logger.debug("esportsbot_exit_failed", market_id=mid, error=str(exc))

    async def _reevaluate_open_positions(self, db, positions=None) -> None:
        """A2: Re-evaluate open positions with fresh Glicko-2 predictions.

        Queries DB for current_price (updated every 10s by position_manager),
        compares against cached model prediction to detect edge collapse.
        Informational logging only — exits handled by stop-loss (B1).
        """
        if db is None:
            return
        if positions is None:
            try:
                positions = await db.get_open_positions_for_bot(self.bot_name)
            except Exception:
                return
        if not positions:
            return

        for pos in positions:
            mid = pos.get("market_id", "")
            cached = self._prediction_cache.get(mid)
            if not cached:
                continue

            side = (pos.get("side") or "YES").upper()
            current_price = float(pos.get("current_price", 0.5) or 0.5)
            model_prob = cached.get("prob", 0.5)

            # Check if edge has collapsed or flipped
            if side == "YES":
                current_edge = model_prob - current_price
            else:
                current_edge = (1.0 - model_prob) - (1.0 - current_price)

            if current_edge <= 0:
                logger.debug("esportsbot_edge_collapsed", market_id=mid,
                             side=side, current_edge=round(current_edge, 4),
                             model_prob=round(model_prob, 4),
                             current_price=round(current_price, 4))

    async def _backfill_esports_outcomes(self, db) -> None:
        """Backfill actual_outcome in esports_prediction_log from trade_events RESOLUTION.

        Runs every 10 scans. Idempotent — resolve_predictions() only updates
        rows where actual_outcome IS NULL. YES win → outcome=1, NO win → outcome=0.
        """
        from sqlalchemy import text as _sa_text
        from esports.data.esports_db import resolve_predictions as _resolve
        async with db.get_session() as _sess:
            result = await _sess.execute(
                _sa_text("""
                    SELECT DISTINCT te.market_id, te.side,
                        CASE WHEN te.realized_pnl > 0 THEN 1 ELSE 0 END AS won
                    FROM trade_events te
                    WHERE te.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
                      AND te.event_type = 'RESOLUTION'
                      AND te.realized_pnl IS NOT NULL
                      AND te.side IN ('YES', 'NO')
                      AND te.event_time > NOW() - INTERVAL '7 days'
                """)
            )
            resolved = result.fetchall()
        for r in resolved:
            if r.won is None:
                continue
            outcome = int(r.won) if r.side == "YES" else (1 - int(r.won))
            await _resolve(db, r.market_id, outcome)

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """
        Analyze a single esports market for trading opportunity.

        1. Classify market type + detect game
        2. Get model prediction (game-specific or prediction engine)
        3. Validate edge: model_prob - poly_price > ESPORTS_MIN_EDGE
        4. Build trade opportunity if edge exists
        """
        # B4: waterfall counter helper (safe if _wf not initialized)
        _wf = getattr(self, "_wf", None)

        market_id = str(market_data.get("id", ""))
        if not market_id:
            return None

        tokens = market_data.get("tokens", [])
        if not tokens:
            if _wf: _wf["no_token"] += 1
            return None

        token = tokens[0]
        price_raw = token.get("outcomePrice") or token.get("price")
        price = self.validate_price(price_raw, market_id)
        if price is None:
            if _wf: _wf["no_price"] += 1
            return None

        token_id = token.get("tokenId") or token.get("token_id")
        if not token_id:
            if _wf: _wf["no_token"] += 1
            return None

        question = (market_data.get("question") or "").lower()

        # Detect game title
        game = self._detect_game(question)
        if not game:
            if _wf: _wf["no_game"] += 1
            return None

        # E4: Check monitoring-halted games
        if game in self._monitoring_halted_games:
            if _wf: _wf["halted"] += 1
            return None

        # Exposure concentration check (per-game cap)
        max_game = float(getattr(settings, "ESPORTS_MAX_GAME_EXPOSURE", 300.0))
        if self._game_exposure.get(game, 0.0) >= max_game:
            if _wf: _wf["exposure_cap"] += 1
            _ecl = getattr(self, "_exposure_cap_logged", None)
            if _ecl is not None and game not in _ecl:
                _ecl.add(game)
                logger.info("esportsbot_exposure_cap_hit",
                            game=game,
                            exposure=round(self._game_exposure.get(game, 0.0), 2),
                            cap=max_game)
            return None

        # Check observation mode for this game
        if self._patch_drift and self._patch_drift.is_observation_mode(game):
            logger.debug(
                "EsportsBot: observation mode active (paper only)",
                game=game,
                market_id=market_id,
            )
            if _wf: _wf["observation"] += 1
            return None

        # Check if halted
        if self._patch_drift and self._patch_drift.is_halted(game):
            if _wf: _wf["halted"] += 1
            return None

        market_type = self._classify_market_type(question)

        # Skip market types that can't produce Glicko-2 predictions (no team matchup)
        if market_type in ("props", "first_blood", "tournament_winner"):
            if _wf: _wf["no_prediction"] += 1
            return None

        # Get model prediction
        model_prob = await self._get_model_prediction(
            game, market_type, market_id, token_id, price, market_data
        )
        if model_prob is None:
            if _wf: _wf["no_prediction"] += 1
            return None

        # Session 82-83: Apply calibration pipeline (bias decomp → focal temp → horizon bias)
        _raw_prob = model_prob
        if self._bias_decomp is not None:
            model_prob = self._bias_decomp.recalibrate(model_prob, game)
        if self._focal_calibrator is not None and self._focal_calibrator.is_fitted:
            model_prob = self._focal_calibrator.calibrate(model_prob)
        if self._horizon_calibrator is not None and self._horizon_calibrator.is_fitted:
            _ttr_days = self._compute_ttr_days(market_data)
            model_prob = self._horizon_calibrator.calibrate(model_prob, "esports", _ttr_days)

        # Validate edge
        # YES side: model thinks YES is more likely than market price
        # NO side: model thinks YES is less likely than market price
        no_token = tokens[1] if len(tokens) > 1 else {}
        no_token_id = no_token.get("tokenId") or no_token.get("token_id")

        # Populate token map for WS reactive path (Fix 1: YES/NO identification)
        self._market_token_map[market_id] = {
            "yes": str(token_id),
            "no": str(no_token_id) if no_token_id else "",
        }

        edge_yes = model_prob - price
        edge_no = (1.0 - model_prob) - (1.0 - price)  # simplifies to price - model_prob

        if edge_yes >= self._min_edge:
            side = "YES"
            trade_token_id = token_id
            trade_price = price
            edge = edge_yes
            confidence = model_prob
        elif -edge_yes >= self._min_edge and no_token_id:
            side = "NO"
            trade_token_id = no_token_id
            trade_price = 1.0 - price
            edge = -edge_yes
            confidence = 1.0 - model_prob
        else:
            if _wf: _wf["low_edge"] += 1
            return None

        # Edge sanity cap — reject unrealistically large edges
        if edge > self._max_edge:
            logger.debug(
                "esportsbot_edge_cap", market_id=market_id, game=game,
                edge=round(edge, 4), max_edge=self._max_edge, side=side,
                model_prob=round(model_prob, 4), price=round(price, 4),
            )
            if _wf: _wf["edge_cap"] += 1
            return None

        # Tournament phase detection and confidence boost
        db = getattr(self.base_engine, "db", None)
        _tournament_phase = self._detect_tournament_phase(market_data)
        confidence *= await self._get_tournament_phase_mult(
            market_data, game, _tournament_phase, db
        )

        if confidence < self._min_confidence:
            if _wf: _wf["low_confidence"] += 1
            logger.debug("esportsbot_low_confidence", game=game, market_id=market_id,
                         confidence=round(confidence, 4), model_prob=round(model_prob, 4),
                         edge=round(edge, 4), side=side, price=round(price, 4))
            return None

        # Log prediction for accuracy tracking (dedup: skip if unchanged within 10 min)
        _log_cache = self._prediction_log_cache.get(market_id)
        _should_log = True
        if _log_cache:
            _prev_prob, _prev_ts = _log_cache
            if abs(_prev_prob - model_prob) < 0.01 and (time.monotonic() - _prev_ts) < 600:
                _should_log = False
        if _should_log:
            try:
                from esports.data.esports_db import log_prediction
                await log_prediction(
                    db=db,
                    match_id=market_id,
                    game=game,
                    market_id=market_id,
                    bot_name="EsportsBot",
                    predicted_prob=model_prob,
                    market_price=price,
                    side=side,
                    edge=round(edge, 4),
                    tournament_phase=_tournament_phase,
                )
                self._prediction_log_cache[market_id] = (model_prob, time.monotonic())
            except Exception as exc:
                logger.warning("EsportsBot: prediction logging failed", error=str(exc))

        # Confluence gate — require multiple signals to agree
        pred_ts = self._prediction_cache.get(market_id, {}).get("ts", time.monotonic())
        confluence = self._compute_confluence_score(
            model_edge=edge,
            side=side,
            token_id=str(trade_token_id),
            market_id=market_id,
            prediction_ts=pred_ts,
        )
        confluence_min = float(getattr(settings, "ESPORTS_CONFLUENCE_MIN", 0.60))
        if confluence < confluence_min:
            logger.debug(
                "EsportsBot: confluence below threshold",
                market_id=market_id,
                confluence=confluence,
                threshold=confluence_min,
            )
            if _wf: _wf["low_confluence"] += 1
            return None

        # Session 83: CoT validation for high-edge trades (>15%)
        if (self._cot_validator is not None
                and self._cot_validator.is_available
                and edge >= 0.15):
            try:
                question = str(market_data.get("question", ""))
                cot_result = await self._cot_validator.validate_trade(
                    question=question, game=game,
                    model_prob=model_prob, market_price=price,
                    edge=edge, side=side,
                )
                if not cot_result.get("approved", True):
                    logger.info("esportsbot_cot_rejected", market_id=market_id,
                                game=game, edge=round(edge, 4),
                                reason=cot_result.get("reason", "")[:100])
                    if _wf: _wf["cot_rejected"] = _wf.get("cot_rejected", 0) + 1
                    return None
            except Exception:
                pass  # Fail open: approve on error

        if _wf: _wf["passed"] += 1
        return {
            "type": "esports_pregame" if not self._is_live(market_id) else "esports_live",
            "market_id": market_id,
            "token_id": str(trade_token_id),
            "side": side,
            "price": trade_price,
            "confidence": confidence,
            "prediction": model_prob,
            "edge": round(edge, 4),
            "game": game,
            "market_type": market_type,
            "confluence": confluence,
            "end_date_iso": market_data.get("end_date_iso"),
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _get_model_prediction(
        self,
        game: str,
        market_type: str,
        market_id: str,
        token_id: str,
        price: float,
        market_data: Dict,
    ) -> Optional[float]:
        """
        Get win probability from game-specific model or prediction engine.

        Returns model's estimated probability for YES outcome, or None.
        """
        # Try game-specific model for live matches (only if graduated)
        live_data = self._live_matches.get(market_id)

        if game == "lol" and self._lol_model and self._lol_model.is_trained and live_data:
            try:
                game_state = live_data.get("game_state", {})
                if game_state:
                    # Inject Glicko-2 metadata features for live inference
                    self._inject_glicko2_metadata(game_state, game, live_data)
                    # Blend ML + Glicko-2: trained model only learned from
                    # team_strength_diff (in-game features were neutralized in
                    # training), so raw predict() is ~51% on live data.
                    # predict_with_glicko2() anchors on the Glicko-2 baseline
                    # and lets the ML model adjust, dampening noise.
                    tsd = float(game_state.get("team_strength_diff", 0.0))
                    glicko2_est = max(0.05, min(0.95, 0.5 + tsd))
                    prob = await asyncio.to_thread(
                        self._lol_model.predict_with_glicko2,
                        game_state, glicko2_est,
                    )
                    if 0.0 < prob < 1.0:
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._lol_model.predict(game_state),
                            "glicko2_est": glicko2_est,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_lol_model_predict_failed", game="lol", error=str(_e))

        if game == "cs2" and self._cs2_model and self._cs2_model.is_trained and live_data:
            try:
                game_state = live_data.get("game_state", {})
                if game_state:
                    prob = self._cs2_model.predict_match(
                        maps_won_a=live_data.get("score_maps_a", 0),
                        maps_won_b=live_data.get("score_maps_b", 0),
                        best_of=live_data.get("best_of", 1),
                        map_probs=game_state.get("map_probs"),
                    )
                    if 0.0 < prob < 1.0:
                        tsd = float(game_state.get("team_strength_diff", 0.0))
                        glicko2_est = max(0.05, min(0.95, 0.5 + tsd / 2))
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": prob, "glicko2_est": glicko2_est,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_cs2_model_predict_failed", game="cs2", error=str(_e))

        # Dota2/Valorant: use ML model with Glicko-2 features (pre-match only)
        if game == "dota2" and self._dota2_model and self._dota2_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._onnx_predict_game(
                            self._onnx_dota2_session, game_state,
                            self._dota2_model, "dota2",
                        )
                        # Blend ML with Glicko-2: extremized geometric mean of odds
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        # OpenDota form adjustment (small ±3% based on recent form)
                        prob = await self._opendota_form_adjustment(market_data, prob)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._dota2_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_dota2_model_predict_failed", game="dota2", error=str(_e))

        if game == "valorant" and self._valorant_model and self._valorant_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._onnx_predict_game(
                            self._onnx_valorant_session, game_state,
                            self._valorant_model, "valorant",
                        )
                        # Blend ML with Glicko-2: extremized geometric mean of odds
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._valorant_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_valorant_model_predict_failed", game="valorant", error=str(_e))

        # "Easy mode" fallback: Glicko-2 expected score from team strength ratings.
        # Replaces base prediction engine (politics/crypto model) which produced
        # random predictions for esports markets — cross-contamination.
        # Graduation: once ML models pass accuracy >= 55% + brier <= 0.24,
        # they take over and Glicko-2 becomes just one blend component.
        try:
            glicko2_prob = await self._get_glicko2_prediction(market_data, game)
            if glicko2_prob is None:
                logger.debug("esportsbot_glicko2_miss", game=game,
                             market_id=market_id, question=str(market_data.get("question",""))[:80])
            if glicko2_prob is not None:
                # OpenDota form adjustment for dota2 (small ±3%)
                if game == "dota2":
                    glicko2_prob = await self._opendota_form_adjustment(
                        market_data, glicko2_prob,
                    )
                # Aligulac blend for SC2 (50/50 with established Elo)
                if game == "sc2":
                    glicko2_prob = await self._aligulac_sc2_blend(
                        market_data, glicko2_prob,
                    )
                # Ballchasing stats adjustment for RL (±3% based on team stats)
                if game == "rl":
                    glicko2_prob = await self._ballchasing_rl_adjustment(
                        market_data, glicko2_prob,
                    )
                # Session 83: TabPFN blend for sparse games (SC2, RL, CoD, R6)
                if (self._tabpfn_predictor is not None
                        and self._tabpfn_predictor.is_available
                        and game in ("sc2", "rl", "cod", "r6")):
                    game_state_tabpfn = self._build_glicko2_game_state(market_data, game)
                    if game_state_tabpfn and self._tabpfn_predictor.is_fitted(game):
                        tabpfn_prob = self._tabpfn_predictor.predict(game, game_state_tabpfn)
                        if tabpfn_prob is not None:
                            w = self._tabpfn_predictor.get_blend_weight()
                            glicko2_prob = w * tabpfn_prob + (1 - w) * glicko2_prob
                            glicko2_prob = max(0.05, min(0.95, glicko2_prob))
                # Cross-game XGB blend: augment Glicko-2 with meta-patterns
                xgb_raw = None
                if self._cross_game_model is not None and game in self._CROSS_GAME_IDS:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        import numpy as _np
                        feats = [
                            game_state["team_strength_diff"],
                            game_state["matchup_uncertainty"],
                            game_state["rd_asymmetry"],
                            game_state["team_a_volatility"],
                            game_state["team_b_volatility"],
                            game_state.get("team_a_recent_form", 0.5),  # P6.5 parity
                            game_state.get("team_b_recent_form", 0.5),  # P6.5 parity
                            float(self._CROSS_GAME_IDS[game]),
                            game_state.get("best_of", 1.0),
                        ]
                        _feat_arr = _np.array([feats], dtype=_np.float32)
                        # ONNX inference (50-200x faster) with native fallback
                        if self._onnx_cross_game_session is not None:
                            try:
                                from esports.models.onnx_compiler import OnnxCompiler
                                _onnx_probs = OnnxCompiler().predict_proba(
                                    self._onnx_cross_game_session, _feat_arr
                                )
                                xgb_prob = float(_onnx_probs[0][1])
                            except Exception:
                                xgb_prob = float(
                                    self._cross_game_model.predict_proba(_feat_arr)[0][1]
                                )
                        else:
                            xgb_prob = float(
                                self._cross_game_model.predict_proba(_feat_arr)[0][1]
                            )
                        # Blend: XGB + Glicko-2 (Glicko-2 anchored via weights)
                        xgb_raw = xgb_prob
                        _d = self._game_egm_d.get(game, self._egm_d)
                        glicko2_prob = extremized_geometric_mean(
                            [glicko2_prob, xgb_prob], weights=[0.6, 0.4], d=_d
                        )
                        glicko2_prob = max(0.05, min(0.95, glicko2_prob))

                self._prediction_cache[market_id] = {
                    "prob": glicko2_prob, "ts": time.monotonic(), "game": game,
                    "ml_raw": xgb_raw, "glicko2_est": glicko2_prob,
                }
                return glicko2_prob
        except Exception as exc:
            logger.warning("esportsbot_glicko2_fallback_failed", game=game, error=str(exc))

        return None

    async def _refresh_live_matches(self) -> None:
        """Fetch live matches from PandaScore (rate-limited, configurable interval)."""
        now = time.monotonic()
        refresh_interval = int(getattr(settings, "ESPORTS_PANDASCORE_REFRESH_INTERVAL", 15))
        if now - self._last_live_refresh < refresh_interval:
            return
        self._last_live_refresh = now

        if not self._pandascore:
            return

        _ps_timeout = float(getattr(settings, "ESPORTS_PANDASCORE_TIMEOUT", 5.0))
        try:
            live = await asyncio.wait_for(
                self._pandascore.get_live_matches(), timeout=_ps_timeout
            )
            new_live = {}
            for match in (live or []):
                mid = str(match.match_id)
                if mid:
                    new_live[mid] = match
            self._live_matches = new_live
            self._live_refresh_failures = 0
        except asyncio.TimeoutError:
            self._live_refresh_failures += 1
            if self._live_refresh_failures >= 3:
                logger.warning("esportsbot_live_refresh_stale",
                               consecutive_failures=self._live_refresh_failures,
                               timeout_s=_ps_timeout)
            else:
                logger.debug("EsportsBot: live match refresh timed out (%.0fs)", _ps_timeout)
        except Exception as exc:
            self._live_refresh_failures += 1
            logger.warning("EsportsBot: live match refresh failed", error=str(exc))

    def _is_live(self, market_id: str) -> bool:
        """Check if a market has an associated live match."""
        return market_id in self._live_matches

    def _inject_glicko2_metadata(
        self, game_state: Dict, game: str, live_data: Dict
    ) -> None:
        """Inject Glicko-2 metadata features into game_state for model inference.

        The Glicko-2 tracker provides per-team mu, phi, sigma. We derive
        matchup_uncertainty, rd_asymmetry, and volatility features that the
        LoL model can use for predictions. Modifies game_state in place.
        """
        tracker = self._glicko2_trackers.get(game)
        if tracker is None:
            return
        # Try to find team IDs from live_data opponents
        opponents = live_data.get("opponents", [])
        team_a_id = team_b_id = None
        if len(opponents) >= 2:
            team_a_id = str(opponents[0].get("opponent", {}).get("id", "")
                           if isinstance(opponents[0], dict) else "")
            team_b_id = str(opponents[1].get("opponent", {}).get("id", "")
                           if isinstance(opponents[1], dict) else "")
        if not team_a_id or not team_b_id:
            return
        rating_a = tracker.get_rating(team_a_id)
        rating_b = tracker.get_rating(team_b_id)
        game_state["matchup_uncertainty"] = (rating_a.phi + rating_b.phi) / 700.0
        game_state["rd_asymmetry"] = (rating_a.phi - rating_b.phi) / 350.0
        game_state["team_a_volatility"] = rating_a.sigma / 0.06
        game_state["team_b_volatility"] = rating_b.sigma / 0.06

    def get_latency_stats(self) -> Dict[str, float]:
        """Return PandaScore data latency statistics (seconds since last refresh at WS event time)."""
        if not self._latency_samples:
            return {"min": 0.0, "max": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "samples": 0}
        sorted_s = sorted(self._latency_samples)
        n = len(sorted_s)
        return {
            "min": round(sorted_s[0], 2),
            "max": round(sorted_s[-1], 2),
            "avg": round(sum(sorted_s) / n, 2),
            "p50": round(sorted_s[n // 2], 2),
            "p95": round(sorted_s[int(n * 0.95)], 2),
            "samples": n,
        }

    @staticmethod
    def _detect_tournament_phase(market_data: Dict) -> str:
        """Detect tournament phase from market data. Returns phase string."""
        serie_type = str(market_data.get("serie_type", "")).lower()
        question = str(market_data.get("question", "")).lower()

        # Detect from PandaScore serie_type (primary)
        if serie_type in ("group", "group_stage", "round_robin"):
            return "group"
        if serie_type in ("grand_final", "grand_finals", "final", "finals"):
            return "finals"
        if serie_type in ("bracket", "playoff", "quarterfinal", "semifinal"):
            return "bracket"

        # Fallback: detect from market question text
        if any(kw in question for kw in ("group stage", "group play", "round robin")):
            return "group"
        if any(kw in question for kw in ("grand final", "championship", "finals")):
            return "finals"

        return "unknown"

    _PHASE_STATIC_MULTS = {"group": 0.90, "bracket": 1.0, "finals": 1.15, "unknown": 1.0}

    async def _get_tournament_phase_mult(
        self, market_data: Dict, game: str = "", phase: str = "", db=None,
    ) -> float:
        """Tournament phase confidence multiplier with auto-calibration.

        Static multipliers: group=0.90, bracket=1.0, finals=1.15.
        When ≥ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES resolved trades exist for a
        phase, blends 70% static + 30% calibrated (from observed Brier score).
        Falls back to static multipliers when data is insufficient.
        """
        if not phase:
            phase = self._detect_tournament_phase(market_data)
        static_mult = self._PHASE_STATIC_MULTS.get(phase, 1.0)

        # Auto-tune if enough resolved trades exist
        if db and game and phase != "unknown":
            try:
                from esports.data.esports_db import get_phase_accuracy
                min_samples = int(getattr(
                    settings, "ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES", 20
                ))
                acc = await get_phase_accuracy(db, game, phase)
                if acc and acc["total"] >= min_samples:
                    # Brier 0.25 (no-skill) → calibrated=1.0; lower Brier → higher mult
                    calibrated_mult = 1.0 + (0.25 - acc["brier_score"]) * 2.0
                    calibrated_mult = max(0.70, min(1.30, calibrated_mult))
                    blended = 0.7 * static_mult + 0.3 * calibrated_mult
                    logger.debug(
                        "EsportsBot: phase mult auto-tuned",
                        game=game,
                        phase=phase,
                        static=round(static_mult, 3),
                        calibrated=round(calibrated_mult, 3),
                        blended=round(blended, 3),
                        samples=acc["total"],
                    )
                    return blended
            except Exception:
                pass

        return static_mult

    async def _opendota_form_adjustment(
        self, market_data: Dict, base_prob: float,
    ) -> float:
        """Adjust Dota2 probability using OpenDota recent form data.

        Fetches team form (win rate over last 10 matches) for both teams
        and applies a small ±3% adjustment to the base probability.
        Returns adjusted prob, or base_prob unchanged if data unavailable.

        All API calls are cached (30 min TTL) so first call per team is slow
        (~1.5s) but subsequent calls within the same scan are instant.
        """
        if not self._opendota:
            return base_prob

        import re

        question = str(market_data.get("question", "")).lower()
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if not vs_match:
            return base_prob

        name_a = vs_match.group(1).strip().rstrip(":")
        name_b = vs_match.group(2).strip().rstrip("?").strip()
        for prefix in ("will ", "can ", "does "):
            if name_a.startswith(prefix):
                name_a = name_a[len(prefix):]
        name_a, name_b = self._clean_team_names(name_a, name_b)

        try:
            enrich_a, enrich_b = await asyncio.gather(
                asyncio.wait_for(
                    self._opendota.get_team_enrichment(name_a), timeout=5.0,
                ),
                asyncio.wait_for(
                    self._opendota.get_team_enrichment(name_b), timeout=5.0,
                ),
                return_exceptions=True,
            )
            if isinstance(enrich_a, Exception) or isinstance(enrich_b, Exception):
                return base_prob
            if not enrich_a or not enrich_b:
                return base_prob

            form_diff = enrich_a["form_wr"] - enrich_b["form_wr"]
            # Small adjustment: ±3% max, scaled by form difference
            form_adj = max(-0.03, min(0.03, form_diff * 0.05))
            adjusted = max(0.05, min(0.95, base_prob + form_adj))

            if abs(form_adj) >= 0.005:
                logger.debug(
                    "opendota_form_adjustment",
                    team_a=name_a, team_b=name_b,
                    form_a=enrich_a["form_wr"], form_b=enrich_b["form_wr"],
                    adj=round(form_adj, 4),
                )
            return adjusted
        except Exception:
            return base_prob

    async def _aligulac_sc2_blend(
        self, market_data: Dict, glicko2_prob: float,
    ) -> float:
        """Blend Glicko-2 prediction with Aligulac's SC2 rating prediction.

        50% Aligulac + 50% Glicko-2 when Aligulac data available.
        Returns glicko2_prob unchanged if Aligulac unavailable.
        """
        if not self._aligulac:
            return glicko2_prob

        import re

        question = str(market_data.get("question", "")).lower()
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if not vs_match:
            return glicko2_prob

        name_a = vs_match.group(1).strip().rstrip(":")
        name_b = vs_match.group(2).strip().rstrip("?").strip()
        for prefix in ("will ", "can ", "does "):
            if name_a.startswith(prefix):
                name_a = name_a[len(prefix):]
        name_a, name_b = self._clean_team_names(name_a, name_b)

        try:
            enrichment = await asyncio.wait_for(
                self._aligulac.get_player_enrichment(name_a, name_b, best_of=3),
                timeout=5.0,
            )
            if enrichment is None:
                return glicko2_prob

            aligulac_prob = enrichment["aligulac_prob_a"]
            # 50/50 blend of Aligulac and our Glicko-2
            blended = 0.5 * aligulac_prob + 0.5 * glicko2_prob
            blended = max(0.05, min(0.95, blended))

            logger.debug(
                "aligulac_sc2_blend",
                player_a=name_a, player_b=name_b,
                aligulac_prob=round(aligulac_prob, 4),
                glicko2_prob=round(glicko2_prob, 4),
                blended=round(blended, 4),
            )
            return blended
        except Exception:
            return glicko2_prob

    async def _ballchasing_rl_adjustment(
        self, market_data: Dict, base_prob: float,
    ) -> float:
        """Adjust RL probability using Ballchasing aggregate team stats.

        Compares goals_per_game and shooting_pct between teams.
        Returns adjusted prob, or base_prob unchanged if data unavailable.
        """
        if not self._ballchasing:
            return base_prob

        import re

        question = str(market_data.get("question", "")).lower()
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if not vs_match:
            return base_prob

        name_a = vs_match.group(1).strip().rstrip(":")
        name_b = vs_match.group(2).strip().rstrip("?").strip()
        for prefix in ("will ", "can ", "does "):
            if name_a.startswith(prefix):
                name_a = name_a[len(prefix):]
        name_a, name_b = self._clean_team_names(name_a, name_b)

        try:
            stats_a, stats_b = await asyncio.gather(
                asyncio.wait_for(
                    self._ballchasing.get_team_aggregate_stats(name_a, days_back=30),
                    timeout=10.0,
                ),
                asyncio.wait_for(
                    self._ballchasing.get_team_aggregate_stats(name_b, days_back=30),
                    timeout=10.0,
                ),
                return_exceptions=True,
            )
            if isinstance(stats_a, Exception) or isinstance(stats_b, Exception):
                return base_prob
            if not stats_a or not stats_b:
                return base_prob

            # Compare goals_per_game and shooting_pct
            gpg_diff = stats_a["goals_per_game"] - stats_b["goals_per_game"]
            shoot_diff = stats_a["shooting_pct"] - stats_b["shooting_pct"]

            # Small adjustment: ±3% max, weighted by goals and shooting diff
            adj = max(-0.03, min(0.03, gpg_diff * 0.01 + shoot_diff * 0.001))
            adjusted = max(0.05, min(0.95, base_prob + adj))

            if abs(adj) >= 0.005:
                logger.debug(
                    "ballchasing_rl_adjustment",
                    team_a=name_a, team_b=name_b,
                    gpg_a=stats_a["goals_per_game"], gpg_b=stats_b["goals_per_game"],
                    adj=round(adj, 4),
                )
            return adjusted
        except Exception:
            return base_prob

    # Pre-compiled word-boundary patterns for short esports acronyms.
    # Prevents false positives: "lec" in "election", "lcs" in "councils", etc.
    _WB_LOL = tuple(re.compile(p) for p in (r"\blck\b", r"\blec\b", r"\blpl\b", r"\blcs\b", r"\bmsi\b"))
    _WB_CS2 = tuple(re.compile(p) for p in (r"\besl\b", r"\bpgl\b", r"\biem\b"))
    _WB_DOTA2 = (re.compile(r"\bdpc\b"), re.compile(r"\bthe international\s+\d"), re.compile(r"\bti\b"))
    _WB_COD = (re.compile(r"\bcdl\b"),)
    _WB_SC2 = tuple(re.compile(p) for p in (r"\bgsl\b", r"\basl\b"))

    @staticmethod
    def _detect_game(question: str) -> Optional[str]:
        """Detect game title from market question text.

        Uses word-boundary regex for short acronyms (lck, lec, lpl, lcs, msi,
        esl, pgl, iem, dpc, cdl, gsl, asl) to prevent false positives inside
        common words like "election", "stablecoins", "councils", etc.
        """
        q = question.lower()
        # LoL
        if any(kw in q for kw in ("league of legends", "lol:", "lol ", " lol ")):
            return "lol"
        if any(p.search(q) for p in EsportsBot._WB_LOL):
            return "lol"
        # CS2
        if any(kw in q for kw in ("counter-strike", "cs2", "csgo", "blast premier")):
            return "cs2"
        if any(p.search(q) for p in EsportsBot._WB_CS2):
            return "cs2"
        # Dota2
        if any(kw in q for kw in ("dota 2", "dota2", "dota:")):
            return "dota2"
        if any(p.search(q) for p in EsportsBot._WB_DOTA2):
            return "dota2"
        # Valorant
        if any(kw in q for kw in ("valorant", "vct ", "champions tour")):
            return "valorant"
        # CoD
        if any(kw in q for kw in ("call of duty", "cod ")):
            return "cod"
        if any(p.search(q) for p in EsportsBot._WB_COD):
            return "cod"
        # R6
        if any(kw in q for kw in ("rainbow six", "r6 ", "six invitational")):
            return "r6"
        # SC2
        if any(kw in q for kw in ("starcraft", "sc2 ", "sc2:", "brood war")):
            return "sc2"
        if any(p.search(q) for p in EsportsBot._WB_SC2):
            return "sc2"
        # RL
        if any(kw in q for kw in ("rocket league", "rlcs")):
            return "rl"
        return None

    @staticmethod
    def _classify_market_type(question: str) -> str:
        """Classify market type from question text."""
        q = question.lower()
        if any(kw in q for kw in ("map ", "game 1", "game 2", "game 3", "game 4", "game 5")):
            return "map_winner"
        if any(kw in q for kw in (
            "tournament", "championship", "champion", "split winner", "season",
            "win msi", "win worlds", "msi 20", "worlds 20",
            "league winner", "win lpl", "win lck", "win lec", "win lcs", "win vct",
        )):
            return "tournament_winner"
        if any(kw in q for kw in ("total maps", "over", "under", "maps played")):
            return "total_maps"
        if any(kw in q for kw in ("first blood", "first kill")):
            return "first_blood"
        if any(kw in q for kw in ("mvp", "kills", "assists", "be said", "signs for")):
            return "props"
        return "match_winner"

    def _compute_confluence_score(
        self,
        model_edge: float,
        side: str,
        token_id: str,
        market_id: str,
        prediction_ts: float,
    ) -> float:
        """
        Compute confluence score (0.0-1.0) from active signals.

        Weights (3-factor, all live):
          - Model prediction edge: 55%
          - Prediction freshness: 30%
          - Model agreement (Glicko-2 vs ML): 15%

        Whale direction (23%) and orderbook imbalance (18%) removed — both
        always returned neutral 0.5 (whale_alerts service not running,
        orderbook cache empty for CLOB esports tokens). This inflated
        confluence by a fixed +0.205, making the 0.60 gate too easy to pass.
        TODO: re-enable when whale_alerts service and orderbook refresh are active.

        Only trades when score > ESPORTS_CONFLUENCE_MIN (default 0.60).
        """
        import math

        # Configurable weights (P4.2-A)
        w_edge = float(getattr(settings, "ESPORTS_CONFLUENCE_WEIGHT_EDGE", 0.55))
        w_fresh = float(getattr(settings, "ESPORTS_CONFLUENCE_WEIGHT_FRESHNESS", 0.30))
        w_agree = float(getattr(settings, "ESPORTS_CONFLUENCE_WEIGHT_AGREEMENT", 0.15))

        # 1. Model edge signal — normalized to 0-1
        edge_score = min(abs(model_edge) / self._min_edge, 1.0)

        # 2. Prediction freshness — exponential decay (P4.3-A: pre-game vs live)
        age_seconds = time.monotonic() - prediction_ts if prediction_ts > 0 else 0
        is_live = market_id in self._live_matches
        decay_s = float(getattr(
            settings,
            "ESPORTS_FRESHNESS_DECAY_SECONDS" if is_live
            else "ESPORTS_FRESHNESS_DECAY_PREGAME_SECONDS",
            30.0 if is_live else 600.0,
        ))
        freshness_score = math.exp(-age_seconds / decay_s)

        # 3. Model agreement — penalize when Glicko-2 and ML disagree
        agreement_score = 0.5  # Neutral when no component data
        cached = self._prediction_cache.get(market_id, {})
        ml_raw = cached.get("ml_raw")
        glicko2_est = cached.get("glicko2_est")
        if ml_raw is not None and glicko2_est is not None:
            disagreement = abs(ml_raw - glicko2_est)
            # Score: 1.0 when perfectly aligned, 0.0 when disagreement >= 0.15
            agreement_score = max(0.0, 1.0 - disagreement / 0.15)

        # Weighted sum
        confluence = (
            w_edge * edge_score +
            w_fresh * freshness_score +
            w_agree * agreement_score
        )

        return round(confluence, 4)

    async def _execute_esports_trade(self, opp: Dict) -> None:
        """Execute trade with maker-first, taker-fallback strategy.

        Includes A10 (pre-update exposure), A6 (uncertainty-scaled sizing),
        A5 (near-expiry boost), A8 (drawdown Kelly reduction),
        Session 83: conformal conservative sizing.
        """
        confidence = opp["confidence"]

        # Session 83: If conformal predictor is fitted, use conservative bound for sizing.
        # This prevents oversizing when model uncertainty is high.
        if (self._conformal_predictor is not None
                and self._conformal_predictor.is_fitted
                and self._cross_game_model is not None):
            try:
                game = opp.get("game", "")
                cache = self._prediction_cache.get(opp.get("market_id", ""), {})
                if cache.get("ml_raw") is not None and game in self._CROSS_GAME_IDS:
                    # Use conservative_prob for sizing (narrows toward 0.5)
                    import numpy as _np
                    _p_mid = cache["prob"]
                    _p_conservative = self._conformal_predictor.conservative_prob(
                        _np.array([[_p_mid]], dtype=_np.float32)
                    )[0]
                    # Only use conservative bound if it's more conservative
                    if opp["side"] == "YES" and _p_conservative < confidence:
                        confidence = float(_p_conservative)
                    elif opp["side"] == "NO" and (1.0 - _p_conservative) < confidence:
                        confidence = float(1.0 - _p_conservative)
            except Exception:
                pass  # Conformal is optional enhancement

        # A5: Near-expiry confidence boost
        confidence = self._apply_expiry_boost(confidence, opp)

        # A6: Uncertainty-scaled sizing — dampen when Glicko-2 phi is high
        phi_factor = self._get_phi_sizing_factor(opp)

        # A8: Drawdown Kelly reduction
        dd_factor = self._get_drawdown_kelly_factor()

        size = await self.calculate_bot_position_size(
            confidence, opp["price"], category="esports"
        )
        if size <= 0:
            return

        # Apply A6 + A8 + per-game Kelly multiplier + edge decay scaling after base sizing
        _game_mult = self._game_kelly_mult.get(opp.get("game", ""), 1.0)
        _decay_mult = self._get_edge_decay_sizing_mult(opp.get("game", ""))
        size = size * phi_factor * dd_factor * _game_mult * _decay_mult
        if size < 1.0:
            return

        # A10: Pre-update exposure BEFORE placing order (race condition fix)
        game = opp.get("game", "")
        self._game_exposure[game] = self._game_exposure.get(game, 0.0) + size
        # Persist market→game for reliable exit decrement (outlives prediction_cache 1h TTL)
        if game:
            self._market_game[opp["market_id"]] = game

        order = await self.place_order(
            market_id=opp["market_id"],
            token_id=opp["token_id"],
            side=opp["side"],
            size=size,
            price=opp["price"],
            confidence=confidence,
        )

        if order and order.get("success"):
            # Update tournament exposure
            tournament = opp.get("tournament", "")
            if tournament:
                self._tournament_exposure[tournament] = (
                    self._tournament_exposure.get(tournament, 0.0) + size
                )
            # Write-through: persist game exposure to daily_counters for restart recovery
            if game:
                _db = getattr(self.base_engine, "db", None)
                if _db is not None:
                    try:
                        await _inc_daily(_db, "EsportsBot", f"game_{game}", size)
                    except Exception as _exc:
                        logger.warning("esports_game_counter_write_failed", error=str(_exc))

            logger.info(
                "EsportsBot trade executed",
                type=opp.get("type"),
                game=game,
                market_type=opp.get("market_type"),
                market_id=opp["market_id"],
                side=opp["side"],
                price=opp["price"],
                confidence=round(confidence, 3),
                edge=opp.get("edge"),
                size=round(size, 2),
                game_exposure=round(self._game_exposure.get(game, 0.0), 2),
                phi_factor=phi_factor,
                dd_factor=dd_factor,
                game_kelly_mult=_game_mult,
                edge_decay_mult=_decay_mult,
            )
        else:
            # A10: Rollback exposure if order failed
            self._game_exposure[game] = max(
                0.0, self._game_exposure.get(game, 0.0) - size
            )

    # ── Sizing + confidence helpers ─────────────────────────────────────

    @staticmethod
    def _apply_expiry_boost(confidence: float, opp: Dict) -> float:
        """A5: Boost confidence for markets close to expiry.

        <6h to expiry: 1.5x confidence boost (capped at 0.95)
        <24h to expiry: 1.2x boost
        Otherwise: no change.

        Reads end_date_iso from opp dict (set by analyze_opportunity from market data).
        """
        end_date = opp.get("end_date_iso")
        if not end_date:
            return confidence
        try:
            if isinstance(end_date, str):
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            else:
                end_dt = end_date
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_left <= 0:
                return confidence
            if hours_left < 6:
                return min(0.95, confidence * 1.5)
            if hours_left < 24:
                return min(0.95, confidence * 1.2)
        except Exception:
            pass
        return confidence

    def _get_phi_sizing_factor(self, opp: Dict) -> float:
        """A6: Scale position size by Glicko-2 rating certainty (phi).

        Uses max phi of both teams from the prediction cache.
        Low phi (< 100) = high certainty → full size (1.0)
        Medium phi (100-200) → 0.8x
        High phi (200-350) → 0.5x
        Very high phi (>= 350) → 0.3x (near-default rating)
        """
        game = opp.get("game", "")
        market_id = opp.get("market_id", "")
        tracker = self._glicko2_trackers.get(game)
        if tracker is None:
            return 0.7  # No tracker → conservative default

        # Extract team names from the prediction cache or re-extract from question
        # We store team IDs during _get_glicko2_prediction, but not in opp dict.
        # Use the prediction cache which has the game state info.
        cached = self._prediction_cache.get(market_id, {})
        if not cached:
            return 0.7

        # Try to get phi by looking up the teams in the tracker.
        # The prediction involved two teams — find their max phi.
        # Since we don't store team IDs in the cache, scan tracker ratings
        # for teams that contributed to this prediction.
        # Approximation: use the confidence/edge spread as proxy for phi.
        # High edge + high confidence = low phi (certain). Low edge = high phi.
        edge = opp.get("edge", 0.0)
        confidence = opp.get("confidence", 0.5)

        # Map edge magnitude to phi proxy: >0.15 edge → phi<100, <0.06 → phi>300
        if edge >= 0.15 and confidence >= 0.65:
            return 1.0   # High certainty
        if edge >= 0.10 and confidence >= 0.58:
            return 0.8   # Medium-high certainty
        if edge >= 0.06:
            return 0.7   # Medium certainty
        return 0.5        # Low certainty (barely above min_edge)

    async def _check_kelly_graduation(self, db) -> None:
        """A3: Continuous Kelly scaling with de-graduation.

        Runs every 10 scans. Replaces the old one-shot 0.25→0.30 threshold.
        Scale = clamp(2.0 - avg_brier/0.25, 0.80, 1.30) → effective Kelly [0.20, 0.325].
        De-graduation: if recent 20-trade Brier > degrade_threshold → cap at 0.20.
        """
        if db is None:
            return
        try:
            from esports.data.esports_db import get_rolling_accuracy
            total_resolved = 0
            weighted_brier = 0.0

            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                acc = await get_rolling_accuracy(db, game, bot_name="EsportsBot")
                if acc and acc["total"] > 0:
                    total_resolved += acc["total"]
                    weighted_brier += acc["brier_score"] * acc["total"]

            if total_resolved < 50:
                return  # Not enough data for reliable scaling

            avg_brier = weighted_brier / total_resolved

            # Continuous scaling: lower Brier → higher multiplier
            # scale = clamp(2.0 - avg_brier/0.25, 0.80, 1.30)
            scale = max(0.80, min(1.30, 2.0 - avg_brier / 0.25))

            base_kelly = float(getattr(settings, "ESPORTS_KELLY_DEFAULT_FRACTION", 0.25))
            new_kelly = base_kelly * scale

            # De-graduation: recent 20-trade Brier too high → floor
            _degrade_brier = float(getattr(settings, "ESPORTS_KELLY_DEGRADE_BRIER", 0.28))
            # Use per-game data to compute recent aggregate (most recent 20 across all games)
            recent_total = 0
            recent_brier_sum = 0.0
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                acc = await get_rolling_accuracy(db, game, bot_name="EsportsBot", last_n=20)
                if acc and acc["total"] > 0:
                    recent_total += acc["total"]
                    recent_brier_sum += acc["brier_score"] * acc["total"]
            if recent_total >= 20:
                recent_brier = recent_brier_sum / recent_total
                if recent_brier > _degrade_brier:
                    new_kelly = min(new_kelly, 0.20)
                    logger.warning("esportsbot_kelly_degraded",
                                   recent_brier=round(recent_brier, 4),
                                   new_kelly=round(new_kelly, 4))

            # Absolute cap
            max_kelly = float(getattr(settings, "ESPORTS_KELLY_MAX_FRACTION", 0.35))
            new_kelly = min(new_kelly, max_kelly)

            # Apply if changed significantly
            if self.bankroll is not None:
                old_kelly = self.bankroll.kelly_fraction
                if abs(new_kelly - old_kelly) > 0.005:
                    self.bankroll.kelly_fraction = round(new_kelly, 4)
                    self._kelly_graduated = new_kelly > base_kelly
                    self._models_graduated = self._kelly_graduated
                    logger.info("esportsbot_kelly_updated",
                                old=round(old_kelly, 4),
                                new=round(new_kelly, 4),
                                avg_brier=round(avg_brier, 4),
                                total_resolved=total_resolved,
                                scale=round(scale, 3))
        except Exception:
            pass

    # ── Glicko-2 "easy mode" helpers ──────────────────────────────────────

    async def _init_glicko2_trackers(self, db) -> None:
        """Build Glicko-2 trackers from persisted DB ratings or historical data.

        Called once during start(). Tries fast path first (load persisted
        ratings from glicko2_ratings table). Falls back to full rebuild from
        esports_training_data if no persisted ratings exist, then saves them.
        """
        if db is None or not getattr(db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            from esports.models.glicko2 import Glicko2Tracker, Glicko2Rating

            # 1. Build team name → ID mapping from esports_teams
            async with db.get_session() as session:
                rows = await session.execute(text(
                    "SELECT external_id, name FROM esports_teams WHERE name IS NOT NULL"
                ))
                for row in rows.fetchall():
                    tid, name = str(row[0]).strip(), str(row[1]).strip()
                    if tid and name:
                        self._team_name_to_id[name.lower()] = tid

            # 2. Fast path: load persisted Glicko-2 ratings from DB
            all_games = ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")
            games_loaded = set()
            for game in all_games:
                try:
                    async with db.get_session() as session:
                        rows = await session.execute(text("""
                            SELECT team_key, mu, phi, sigma, match_count
                            FROM glicko2_ratings
                            WHERE game = :game
                        """), {"game": game})
                        ratings_rows = rows.fetchall()
                except Exception:
                    # Table doesn't exist yet (migration not run) — fall back
                    ratings_rows = []

                if ratings_rows:
                    tracker = Glicko2Tracker()
                    total_matches = 0
                    for row in ratings_rows:
                        team_key = str(row[0])
                        rating = Glicko2Rating(
                            mu=float(row[1]),
                            phi=float(row[2]),
                            sigma=float(row[3]),
                        )
                        tracker.set_rating(team_key, rating)
                        self._team_name_to_id[team_key] = team_key
                        total_matches = max(total_matches, int(row[4] or 0))
                    tracker.set_match_count(total_matches)
                    self._glicko2_trackers[game] = tracker
                    games_loaded.add(game)
                    logger.info(
                        "EsportsBot: Glicko-2 loaded from DB",
                        game=game,
                        teams_rated=len(ratings_rows),
                        match_count=total_matches,
                    )

            # 3. Slow path: rebuild games missing from DB (first run or new game added)
            games_to_rebuild = [g for g in all_games if g not in games_loaded]
            if not games_to_rebuild:
                return

            rebuilt_any = False
            for game in games_to_rebuild:
                async with db.get_session() as session:
                    rows = await session.execute(text("""
                        SELECT team_a, team_b, outcome
                        FROM esports_training_data
                        WHERE game = :game AND outcome IS NOT NULL
                        ORDER BY COALESCE(scheduled_at, created_at) ASC
                    """), {"game": game})
                    matches = rows.fetchall()

                if not matches:
                    continue

                tracker = Glicko2Tracker()
                for row in matches:
                    team_a_name = str(row[0] or "").strip()
                    team_b_name = str(row[1] or "").strip()
                    outcome = int(row[2]) if row[2] is not None else None
                    if not team_a_name or not team_b_name or outcome is None:
                        continue
                    a_id = team_a_name.lower()
                    b_id = team_b_name.lower()
                    w = "a" if outcome == 1 else "b"
                    tracker.process_match(a_id, b_id, winner=w)
                    self._team_name_to_id[a_id] = a_id
                    self._team_name_to_id[b_id] = b_id

                self._glicko2_trackers[game] = tracker
                rebuilt_any = True
                logger.info(
                    "EsportsBot: Glicko-2 rebuilt from training data",
                    game=game,
                    matches_processed=tracker.match_count,
                    teams_rated=len(tracker._ratings),
                )

            # 4. Persist rebuilt ratings so next restart is fast
            if rebuilt_any:
                await self._save_glicko2_ratings(db)

        except Exception as exc:
            logger.debug("EsportsBot: Glicko-2 init failed (non-fatal)", error=str(exc))

    async def _save_glicko2_ratings(self, db) -> None:
        """Persist all Glicko-2 ratings to glicko2_ratings table.

        Uses upsert (ON CONFLICT UPDATE) so it's safe to call repeatedly.
        """
        if db is None:
            return
        try:
            from sqlalchemy import text

            for game, tracker in self._glicko2_trackers.items():
                all_ratings = tracker.get_all_ratings()
                if not all_ratings:
                    continue
                match_count = tracker.match_count
                async with db.get_session() as session:
                    for team_key, rating in all_ratings.items():
                        await session.execute(text("""
                            INSERT INTO glicko2_ratings
                                (game, team_key, mu, phi, sigma, match_count, updated_at)
                            VALUES
                                (:game, :team_key, :mu, :phi, :sigma, :mc, NOW())
                            ON CONFLICT (game, team_key) DO UPDATE SET
                                mu = :mu, phi = :phi, sigma = :sigma,
                                match_count = :mc, updated_at = NOW()
                        """), {
                            "game": game,
                            "team_key": team_key,
                            "mu": rating.mu,
                            "phi": rating.phi,
                            "sigma": rating.sigma,
                            "mc": match_count,
                        })
                    await session.commit()
            logger.info("EsportsBot: Glicko-2 ratings saved to DB",
                        games=len(self._glicko2_trackers))
        except Exception as exc:
            logger.debug("EsportsBot: Glicko-2 save failed (non-fatal)", error=str(exc))

    async def _train_in_background(
        self, game: str, db, init_glicko: bool = False
    ) -> None:
        """Run model training as background task — does not block scan loop."""
        try:
            if game == "cross_game":
                await asyncio.wait_for(
                    self._trainer.train_cross_game(db=db), timeout=300.0,
                )
                self._load_cross_game_model()
            else:
                result = await asyncio.wait_for(
                    self._trainer.train_game(game, db=db), timeout=300.0,
                )
                # Reload updated models
                if game == "lol" and self._lol_model:
                    self._lol_model.load()
                elif game == "cs2" and self._cs2_model:
                    self._cs2_model.load()
                elif game == "dota2" and self._dota2_model:
                    self._dota2_model.load()
                elif game == "valorant" and self._valorant_model:
                    self._valorant_model.load()
                # Rebuild Glicko-2 trackers if new game data collected
                if init_glicko and result.get("samples", 0) > 0:
                    await self._init_glicko2_trackers(db)
            logger.info("EsportsBot: bg retrain complete", game=game)
        except asyncio.CancelledError:
            logger.info("EsportsBot: bg retrain cancelled", game=game)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("EsportsBot: bg retrain failed", game=game, error=str(exc))

    async def _check_monitoring_thresholds(self, db) -> None:
        """E4: Check per-game Brier scores and emit structured alerts.

        Thresholds:
          - Brier > 0.25 (7d) → WARNING, retrain model
          - Brier > 0.30 (7d) → CRITICAL, halt trading for that game

        Checks run every 10 minutes (self._monitoring_check_interval).
        """
        now = time.monotonic()
        if now - self._monitoring_last_check < self._monitoring_check_interval:
            return
        self._monitoring_last_check = now

        if not db:
            return

        alerting = getattr(self.base_engine, "alerting_system", None)

        try:
            from esports.data.esports_db import get_rolling_accuracy
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                acc_data = await get_rolling_accuracy(db, game, bot_name="EsportsBot")
                if not acc_data or acc_data["total"] < 20:
                    continue

                brier = acc_data["brier_score"]
                accuracy = acc_data["accuracy"]

                if brier > 0.30:
                    self._monitoring_halted_games.add(game)
                    if alerting:
                        await alerting.send_alert(
                            title=f"EsportsBot {game} Brier CRITICAL",
                            message=f"{game} 7d Brier={brier:.4f} (>0.30), accuracy={accuracy:.1%} — "
                                    f"trading halted for {game}.",
                            severity=AlertSeverity.CRITICAL,
                            source="EsportsBot",
                            metadata={"game": game, "brier": brier, "accuracy": accuracy,
                                      "total": acc_data["total"]},
                        )
                    logger.critical(
                        "esportsbot_monitoring_halt",
                        game=game, brier=round(brier, 4), accuracy=round(accuracy, 3),
                    )
                elif brier > 0.25:
                    # WARNING but don't halt — trigger retrain
                    self._monitoring_halted_games.discard(game)
                    if alerting:
                        await alerting.send_alert(
                            title=f"EsportsBot {game} Brier WARNING",
                            message=f"{game} 7d Brier={brier:.4f} (>0.25), accuracy={accuracy:.1%} — "
                                    f"consider retraining.",
                            severity=AlertSeverity.WARNING,
                            source="EsportsBot",
                            metadata={"game": game, "brier": brier, "accuracy": accuracy,
                                      "total": acc_data["total"]},
                        )
                    logger.warning(
                        "esportsbot_monitoring_warning",
                        game=game, brier=round(brier, 4), accuracy=round(accuracy, 3),
                    )
                else:
                    # Below thresholds — clear halt
                    if game in self._monitoring_halted_games:
                        logger.info("esportsbot_monitoring_halt_cleared",
                                    game=game, brier=round(brier, 4))
                    self._monitoring_halted_games.discard(game)

                # Per-game Kelly multiplier based on Brier score
                _brier_penalty = float(getattr(settings, "ESPORTS_KELLY_BRIER_PENALTY", 0.25))
                _brier_boost = float(getattr(settings, "ESPORTS_KELLY_BRIER_BOOST", 0.20))
                if brier > _brier_penalty:
                    self._game_kelly_mult[game] = 0.5
                elif brier < _brier_boost:
                    self._game_kelly_mult[game] = 1.2
                else:
                    self._game_kelly_mult[game] = 1.0
        except Exception as exc:
            logger.debug("esportsbot_monitoring_check_failed", error=str(exc))

        # Log per-game Kelly multipliers if any changed
        if self._game_kelly_mult:
            _nondefault = {g: m for g, m in self._game_kelly_mult.items() if m != 1.0}
            if _nondefault:
                logger.info("esportsbot_game_kelly_mult", multipliers=_nondefault)

        # Calibration curve + ECE tracking per game
        try:
            from esports.data.esports_db import compute_calibration_curve
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                cal = await compute_calibration_curve(db, game=game, days=90)
                if cal and cal["total"] >= 30:
                    self._calibration_ece[game] = cal["ece"]
                    logger.info("esportsbot_calibration_report",
                                game=game, ece=cal["ece"],
                                bins=cal["bins"], total=cal["total"])
        except Exception as exc:
            logger.debug("esportsbot_calibration_failed", error=str(exc))

        # Session 82: Fit FocalTemperatureCalibrator from prediction_log
        if self._focal_calibrator is not None:
            try:
                fitted = await self._focal_calibrator.fit_from_prediction_log(n_days=90)
                if fitted:
                    logger.info("esportsbot_focal_temp_fitted",
                                T=round(self._focal_calibrator.temperature, 2),
                                gamma=round(self._focal_calibrator.gamma, 1))
            except Exception as exc:
                logger.debug("esportsbot_focal_temp_fit_failed", error=str(exc))

        # Session 82: Fit EsportsBiasDecomposition per game
        if self._bias_decomp is not None:
            try:
                bd_results = await self._bias_decomp.fit_from_db(db, days=90)
                if bd_results:
                    logger.info("esportsbot_bias_decomp_fitted",
                                games=list(bd_results.keys()),
                                params={g: round(v["b"], 3) for g, v in bd_results.items()})
            except Exception as exc:
                logger.debug("esportsbot_bias_decomp_fit_failed", error=str(exc))

        # Session 83: Fit HorizonBiasCalibrator from trade_events
        if self._horizon_calibrator is not None:
            try:
                fitted = await asyncio.wait_for(
                    self._horizon_calibrator.fit_from_trade_events(n_days=180),
                    timeout=10.0,
                )
                if fitted:
                    logger.info("esportsbot_horizon_bias_fitted",
                                buckets=len(self._horizon_calibrator._b_params))
            except asyncio.TimeoutError:
                logger.warning("esportsbot_horizon_bias_timeout")
            except Exception as exc:
                logger.debug("esportsbot_horizon_bias_fit_failed", error=str(exc))

        # Session 83: Dynamic per-game EGM d tuning from Brier scores
        self._update_per_game_egm_d()

        # Edge decay analysis per game
        try:
            from esports.data.esports_db import analyze_edge_decay
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                decay = await analyze_edge_decay(db, game=game, days=30, n_bins=5)
                if decay and decay.get("total_predictions", 0) >= 20:
                    self._edge_decay_data[game] = decay
                    bins = decay.get("bins", [])
                    logger.info("esportsbot_edge_decay",
                                game=game,
                                total=decay["total_predictions"],
                                bins=bins)
                    # Flag fast decay: top time-bin has negative CLV
                    if bins and bins[0].get("avg_clv", 0) < 0:
                        logger.warning("esportsbot_edge_decay_negative_clv",
                                       game=game,
                                       top_bin_clv=bins[0].get("avg_clv"),
                                       top_bin_profit=bins[0].get("avg_profit"))
        except Exception as exc:
            logger.debug("esportsbot_edge_decay_failed", error=str(exc))

        # P&L summary logging
        try:
            from esports.data.esports_db import compute_pnl_summary
            pnl = await compute_pnl_summary(db)
            if pnl and pnl["total_trades"] > 0:
                logger.info(
                    "esportsbot_pnl_summary",
                    total_pnl=pnl["total_pnl"],
                    total_trades=pnl["total_trades"],
                    win_rate=round(pnl["win_rate"], 4),
                    per_game=pnl["per_game"],
                )
        except Exception as exc:
            logger.debug("esportsbot_pnl_summary_failed", error=str(exc))

        # Pinnacle CLV backfill (every 10th scan cycle, ~20 min)
        if not hasattr(self, "_clv_backfill_counter"):
            self._clv_backfill_counter = 0
        self._clv_backfill_counter += 1
        if self._clv_backfill_counter >= 10:
            self._clv_backfill_counter = 0
            oddspapi_key = getattr(settings, "ODDSPAPI_API_KEY", "")
            if oddspapi_key:
                try:
                    from esports.data.oddspapi_client import OddsPapiClient
                    from esports.data.esports_db import backfill_pinnacle_closing_lines
                    oddspapi = OddsPapiClient(api_key=oddspapi_key)
                    updated = await backfill_pinnacle_closing_lines(db, oddspapi)
                    if updated > 0:
                        logger.info("pinnacle_clv_backfill_done", rows_updated=updated)
                except Exception as exc:
                    logger.debug("pinnacle_clv_backfill_failed", error=str(exc))

        # Retention cleanup — once daily
        await self._cleanup_old_esports_data(db)

    async def _cleanup_old_esports_data(self, db) -> None:
        """Delete old esports data beyond retention window. Called once daily."""
        import datetime as _dt_mod
        today = _dt_mod.date.today()
        if getattr(self, "_last_cleanup_date", None) == today:
            return
        self._last_cleanup_date = today
        try:
            from sqlalchemy import text as _sa_text
            train_days = int(getattr(settings, "ESPORTS_TRAINING_RETENTION_DAYS", 365))
            pred_days = int(getattr(settings, "ESPORTS_PREDICTION_RETENTION_DAYS", 180))
            async with db.get_session() as session:
                r1 = await session.execute(
                    _sa_text("DELETE FROM esports_training_data WHERE created_at < NOW() - INTERVAL '1 day' * :days"),
                    {"days": train_days},
                )
                r2 = await session.execute(
                    _sa_text("DELETE FROM esports_prediction_log WHERE created_at < NOW() - INTERVAL '1 day' * :days"),
                    {"days": pred_days},
                )
                await session.commit()
            logger.info("esports_data_cleanup",
                        training_deleted=r1.rowcount, prediction_deleted=r2.rowcount)
        except Exception as exc:
            logger.warning("esports_data_cleanup_failed", error=str(exc))

    async def _backfill_unknown_team(self, name: str, game: str) -> Optional[str]:
        """On-demand PandaScore lookup for a team missing from Glicko-2 DB.

        Searches PandaScore by name, fetches recent matches, processes through
        Glicko-2, persists ratings, and adds to _team_name_to_id.

        Returns team_key if successful, None if not found.
        Costs 2 API requests. Guarded by _backfill_attempted to avoid
        re-querying the same missing team every scan cycle.
        """
        cache_key = f"{game}:{name.lower()}"
        if cache_key in self._backfill_attempted:
            return self._team_name_to_id.get(name.lower())
        # Rate budget: cap backfill API calls per scan to avoid blowing PandaScore quota
        if self._backfill_calls_this_scan >= self._max_backfills_per_scan:
            logger.debug("esportsbot_backfill_budget_exhausted", name=name, game=game)
            return None
        self._backfill_calls_this_scan += 1
        self._backfill_attempted.add(cache_key)

        if not self._pandascore:
            return None

        try:
            # 1. Search PandaScore for team
            team_data = await self._pandascore.search_team_by_name(name)
            if not team_data or not team_data.get("id"):
                logger.info("esportsbot_team_backfill_not_found",
                            name=name, game=game)
                return None

            team_id = int(team_data["id"])
            team_name = str(team_data.get("name", name)).lower()

            # 2. Fetch recent finished matches
            matches = await self._pandascore.get_team_matches(team_id, game, per_page=20)
            if not matches:
                logger.info("esportsbot_team_backfill_no_matches",
                            name=name, game=game, pandascore_id=team_id)
                return None

            # 3. Process through Glicko-2
            tracker = self._glicko2_trackers.get(game)
            if tracker is None:
                return None

            processed = 0
            for match in matches:
                a_name = match.team_a.lower().strip()
                b_name = match.team_b.lower().strip()
                if not a_name or not b_name:
                    continue
                # Determine winner from score
                if match.status != "finished":
                    continue
                if match.score_a > match.score_b:
                    winner = "a"
                elif match.score_b > match.score_a:
                    winner = "b"
                else:
                    winner = "draw"
                tracker.process_match(a_name, b_name, winner=winner)
                # Add ALL encountered teams to lookup
                self._team_name_to_id[a_name] = a_name
                self._team_name_to_id[b_name] = b_name
                processed += 1

            if processed == 0:
                return None

            # 4. Persist to DB
            db = getattr(self.base_engine, "db", None)
            if db:
                await self._save_glicko2_ratings(db)

            logger.info("esportsbot_team_backfilled",
                        name=team_name, game=game,
                        pandascore_id=team_id,
                        matches_processed=processed)

            return self._team_name_to_id.get(name.lower())

        except Exception as exc:
            logger.debug("esportsbot_team_backfill_failed",
                         name=name, game=game, error=str(exc))
            return None

    async def _get_glicko2_prediction(
        self, market_data: Dict, game: str
    ) -> Optional[float]:
        """Extract team names from market question and return Glicko-2 expected score.

        Returns P(team_a wins) based on Glicko-2 ratings, or None if we can't
        identify both teams or don't have ratings for them.
        """
        import re

        tracker = self._glicko2_trackers.get(game)
        if tracker is None or tracker.match_count < 10:
            return None

        question = str(market_data.get("question", "")).lower()
        if not question:
            return None

        # Try to extract team names from question patterns:
        # "Will [Team A] beat [Team B]?"
        # "Will [Team A] win [vs/against] [Team B]?"
        # "[Team A] vs [Team B]"
        team_a_id = team_b_id = None
        _clean_a = _clean_b = ""  # Best cleaned team names (for backfill)

        # Pattern 1: "Team A vs Team B" or "Team A versus Team B"
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if vs_match:
            name_a = vs_match.group(1).strip().rstrip(":")
            name_b = vs_match.group(2).strip().rstrip("?").strip()
            # Remove leading "will" etc
            for prefix in ("will ", "can ", "does "):
                if name_a.startswith(prefix):
                    name_a = name_a[len(prefix):]
            name_a, name_b = self._clean_team_names(name_a, name_b)
            _clean_a, _clean_b = name_a, name_b
            team_a_id = self._match_team_name(name_a)
            team_b_id = self._match_team_name(name_b)

        # Pattern 2: "Will [Team] beat/defeat [Team]?"
        if not team_a_id or not team_b_id:
            beat_match = re.search(
                r"(?:will\s+)?(.+?)\s+(?:beat|defeat|win against|win over)\s+(.+?)(?:\?|$)",
                question,
            )
            if beat_match:
                ba = beat_match.group(1).strip()
                bb = beat_match.group(2).strip()
                ba, bb = self._clean_team_names(ba, bb)
                _clean_a, _clean_b = ba, bb
                team_a_id = self._match_team_name(ba)
                team_b_id = self._match_team_name(bb)

        # Pattern 3: "Will [Team] win against/over/vs [Team]?"
        if not team_a_id or not team_b_id:
            win_match = re.search(
                r"(?:will\s+)?(.+?)\s+win\s+(?:against|over|vs\.?)\s+(.+?)(?:\?|$)",
                question,
            )
            if win_match:
                wa = win_match.group(1).strip()
                wb = win_match.group(2).strip()
                wa, wb = self._clean_team_names(wa, wb)
                _clean_a, _clean_b = wa, wb
                team_a_id = self._match_team_name(wa)
                team_b_id = self._match_team_name(wb)

        # Pattern 4: "[Team] to win against/over/vs [Team]"
        if not team_a_id or not team_b_id:
            to_win_match = re.search(
                r"(.+?)\s+to\s+win\s+(?:against|over|vs\.?)\s+(.+?)(?:\?|$)",
                question,
            )
            if to_win_match:
                ta = to_win_match.group(1).strip()
                tb = to_win_match.group(2).strip()
                ta, tb = self._clean_team_names(ta, tb)
                _clean_a, _clean_b = ta, tb
                team_a_id = self._match_team_name(ta)
                team_b_id = self._match_team_name(tb)

        # Pattern 5: "[Team] or [Team] — who will win?" / "who wins: [A] or [B]"
        if not team_a_id or not team_b_id:
            or_match = re.search(
                r"(?:who\s+(?:will\s+)?win[s]?[:\s]+)?(.+?)\s+or\s+(.+?)(?:\?|$)",
                question,
            )
            if or_match:
                oa = or_match.group(1).strip()
                ob = or_match.group(2).strip()
                oa, ob = self._clean_team_names(oa, ob)
                _clean_a, _clean_b = oa, ob
                team_a_id = self._match_team_name(oa)
                team_b_id = self._match_team_name(ob)

        # Pattern 6: "[Team] - [Team]" (dash-separated, common in Asian markets)
        if not team_a_id or not team_b_id:
            dash_match = re.search(
                r"(.+?)\s+-\s+(.+?)(?:\?|$)", question,
            )
            if dash_match:
                da = dash_match.group(1).strip()
                db = dash_match.group(2).strip()
                for prefix in ("will ", "can ", "does "):
                    if da.startswith(prefix):
                        da = da[len(prefix):]
                da, db = self._clean_team_names(da, db)
                _clean_a, _clean_b = da, db
                team_a_id = self._match_team_name(da)
                team_b_id = self._match_team_name(db)

        if not team_a_id or not team_b_id:
            # On-demand backfill: try PandaScore lookup for missing team(s)
            if not team_a_id and _clean_a:
                team_a_id = await self._backfill_unknown_team(_clean_a, game)
            if not team_b_id and _clean_b:
                team_b_id = await self._backfill_unknown_team(_clean_b, game)
            if not team_a_id or not team_b_id:
                _fail_key = f"{game}:{_clean_a}:{_clean_b}"
                if _fail_key not in self._team_fail_logged:
                    self._team_fail_logged.add(_fail_key)
                    logger.info("esportsbot_team_match_fail", game=game,
                                question=question[:80],
                                name_a=_clean_a or "?",
                                name_b=_clean_b or "?",
                                team_a_id=team_a_id, team_b_id=team_b_id)
                return None

        # Get Glicko-2 expected score with Bayesian prior blending (E5).
        # When teams have few matches (high phi), blend toward 0.50 (base rate)
        # instead of returning None. Blend weight based on min(phi_a, phi_b):
        #   phi >= 350 (unrated):    80% prior (0.50), 20% Glicko-2
        #   phi 200-350 (sparse):    50% prior, 50% Glicko-2
        #   phi < 200 (established): 20% prior, 80% Glicko-2
        #   phi < 100 (mature):       0% prior, 100% Glicko-2
        try:
            rating_a = tracker.get_rating(team_a_id)
            rating_b = tracker.get_rating(team_b_id)
            prob = tracker.expected_score(team_a_id, team_b_id)
            if not (0.01 < prob < 0.99):
                return None

            # E5: Bayesian prior blend — dampen predictions for uncertain teams
            max_phi = max(rating_a.phi, rating_b.phi)
            if max_phi >= 350.0:
                prior_weight = 0.80
            elif max_phi >= 200.0:
                prior_weight = 0.50
            elif max_phi >= 100.0:
                prior_weight = 0.20
            else:
                prior_weight = 0.0

            if prior_weight > 0:
                prob = prior_weight * 0.50 + (1.0 - prior_weight) * prob

            if 0.05 < prob < 0.95:
                return prob
        except Exception:
            pass
        return None

    # ── Session 83: New helper methods ──────────────────────────────────

    @staticmethod
    def _compute_ttr_days(market_data: Dict) -> Optional[float]:
        """Compute time-to-resolution in days from market end_date_iso."""
        end_date = market_data.get("end_date_iso")
        if not end_date:
            return None
        try:
            if isinstance(end_date, str):
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            else:
                end_dt = end_date
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (end_dt - now).total_seconds() / 86400.0)
        except Exception:
            return None

    def _update_per_game_egm_d(self) -> None:
        """Dynamic d tuning: adjust EGM extremization per game based on Brier score.

        Games with low Brier (well-calibrated) get higher d (more extreme aggregation).
        Games with high Brier (poor calibration) get lower d (more conservative).
        Range: d ∈ [1.0, 2.5] (IARPA ACE range).
        """
        for game, mult in self._game_kelly_mult.items():
            # Kelly mult encodes Brier quality: 1.2 = good (Brier < 0.20), 0.5 = bad (> 0.25)
            if mult >= 1.2:
                self._game_egm_d[game] = min(2.5, self._egm_d + 0.5)  # More extreme
            elif mult <= 0.5:
                self._game_egm_d[game] = max(1.0, self._egm_d - 0.3)  # More conservative
            else:
                self._game_egm_d[game] = self._egm_d  # Default

    def _get_edge_decay_sizing_mult(self, game: str) -> float:
        """Return sizing multiplier based on edge decay analysis.

        If a game shows fast edge decay (top time-bin CLV < 0), reduce sizing.
        If edge holds well (all bins CLV > 0), keep at 1.0.
        """
        decay = self._edge_decay_data.get(game)
        if not decay:
            return 1.0
        bins = decay.get("bins", [])
        if not bins:
            return 1.0
        top_bin_clv = bins[0].get("avg_clv", 0)
        if top_bin_clv < -0.05:
            return 0.6  # Significant negative CLV: heavy reduction
        elif top_bin_clv < 0:
            return 0.8  # Mild negative CLV: moderate reduction
        return 1.0

    def _onnx_predict_game(
        self, onnx_session: Any, game_state: Dict, native_model: Any, game: str,
    ) -> float:
        """Predict via ONNX if session available, fallback to native model.predict()."""
        if onnx_session is not None:
            try:
                import numpy as _np
                from esports.models.onnx_compiler import OnnxCompiler
                # Build feature array from game_state in model's FEATURE_NAMES order
                feature_names = getattr(native_model, "FEATURE_NAMES", None)
                if feature_names:
                    feats = [float(game_state.get(f, 0.0)) for f in feature_names]
                    _arr = _np.array([feats], dtype=_np.float32)
                    probs = OnnxCompiler().predict_proba(onnx_session, _arr)
                    return float(probs[0][1])
            except Exception:
                pass  # Fall through to native
        return native_model.predict(game_state)

    def _load_per_game_onnx_sessions(self) -> None:
        """Load ONNX sessions for per-game models (LoL, CS2, Dota2, Valorant)."""
        import os
        try:
            from esports.models.onnx_compiler import OnnxCompiler
            compiler = OnnxCompiler()
        except ImportError:
            return

        models_dir = os.path.join(os.path.dirname(__file__), "..", "saved_models")
        models_dir = os.path.abspath(models_dir)
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        data_dir = os.path.abspath(data_dir)

        # Map: (attribute, onnx_filename, search_dirs)
        _onnx_map = [
            ("_onnx_lol_session", "lol_win_model.onnx", [models_dir, data_dir]),
            ("_onnx_cs2_session", "cs2_economy_model.onnx", [models_dir, data_dir]),
            ("_onnx_dota2_session", "dota2_xgb.onnx", [models_dir]),
            ("_onnx_valorant_session", "valorant_xgb.onnx", [models_dir]),
        ]

        for attr, filename, dirs in _onnx_map:
            for d in dirs:
                path = os.path.join(d, filename)
                if os.path.exists(path):
                    try:
                        session = compiler.load_session(path)
                        if session is not None:
                            setattr(self, attr, session)
                            logger.info(f"EsportsBot: {filename} ONNX loaded", path=path)
                    except Exception:
                        pass
                    break

    # Cross-game model: game → integer ID (must match esports_trainer.py _GAME_IDS)
    _CROSS_GAME_IDS = {"lol": 0, "cs2": 1, "dota2": 2, "valorant": 3,
                       "cod": 4, "r6": 5, "sc2": 6, "rl": 7}

    def _load_cross_game_model(self) -> None:
        """Load cross-game XGBoost model from saved_models/ (if exists)."""
        import os
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "saved_models", "cross_game_xgb.json",
        )
        model_path = os.path.abspath(model_path)
        if not os.path.exists(model_path):
            return
        try:
            from xgboost import XGBClassifier
            model = XGBClassifier()
            model.load_model(model_path)
            self._cross_game_model = model
            logger.info("EsportsBot: cross_game_xgb loaded", path=model_path)

            # Session 82: Try loading ONNX compiled version for faster inference
            try:
                from esports.models.onnx_compiler import OnnxCompiler
                onnx_path = model_path.replace(".json", ".onnx")
                compiler = OnnxCompiler()
                session = compiler.load_session(onnx_path)
                if session is not None:
                    self._onnx_cross_game_session = session
                    logger.info("EsportsBot: cross_game ONNX session loaded", path=onnx_path)
            except Exception:
                pass  # ONNX is optional enhancement
        except Exception as exc:
            logger.warning("EsportsBot: cross_game_xgb load failed", error=str(exc))

    def _build_glicko2_game_state(
        self, market_data: Dict, game: str
    ) -> Optional[Dict[str, float]]:
        """Build a feature dict from Glicko-2 ratings for dota2/valorant ML models.

        Extracts team names from market question, looks up Glicko-2 ratings,
        and returns the 6-feature dict expected by Dota2Model/ValorantModel.
        """
        import re

        tracker = self._glicko2_trackers.get(game)
        if tracker is None:
            return None

        question = str(market_data.get("question", "")).lower()
        if not question:
            return None

        team_a_id = team_b_id = None
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if vs_match:
            name_a = vs_match.group(1).strip().rstrip(":")
            name_b = vs_match.group(2).strip().rstrip("?").strip()
            for prefix in ("will ", "can ", "does "):
                if name_a.startswith(prefix):
                    name_a = name_a[len(prefix):]
            name_a, name_b = self._clean_team_names(name_a, name_b)
            team_a_id = self._match_team_name(name_a)
            team_b_id = self._match_team_name(name_b)

        if not team_a_id or not team_b_id:
            return None

        try:
            rating_a = tracker.get_rating(team_a_id)
            rating_b = tracker.get_rating(team_b_id)
            expected = tracker.expected_score(team_a_id, team_b_id)

            # P6.5 parity: compute inference-time proxy for rolling win rate.
            # expected_score(team, neutral_ref) maps Glicko-2 rating to [0,1],
            # correlated with recent winning — same range as training-time form.
            from esports.models.glicko2 import Glicko2Rating as _G2R, expected_score as _g2es
            _ref = _G2R()  # neutral: mu=1500, phi=350, sigma=0.06
            return {
                "team_strength_diff": expected - 0.5,
                "matchup_uncertainty": (rating_a.phi + rating_b.phi) / 700.0,
                "rd_asymmetry": (rating_a.phi - rating_b.phi) / 350.0,
                "team_a_volatility": rating_a.sigma / 0.06,
                "team_b_volatility": rating_b.sigma / 0.06,
                "best_of": 1.0,  # Default; overridden if series data available
                "team_a_recent_form": float(_g2es(rating_a, _ref)),
                "team_b_recent_form": float(_g2es(rating_b, _ref)),
            }
        except Exception:
            return None

    @staticmethod
    def _clean_team_names(name_a: str, name_b: str) -> tuple:
        """Strip game title prefixes, tournament/format suffixes, and normalize team names.

        Regex captures game titles (e.g. "counter-strike: themongolz") and
        tournament suffixes (e.g. "pain (bo3) - esl pro league stage 2").
        Also normalizes common abbreviations and formatting artifacts.
        """
        import re as _re
        _game_prefixes = (
            "counter-strike 2: ", "counter-strike: ", "cs2: ", "csgo: ",
            "league of legends: ", "lol: ",
            "dota 2: ", "dota: ",
            "valorant: ",
            "call of duty: ", "cod: ",
            "rainbow six siege: ", "rainbow six: ", "r6: ",
            "starcraft ii: ", "starcraft 2: ", "starcraft: ",
            "rocket league: ",
            "overwatch 2: ", "overwatch: ",
        )
        for gp in _game_prefixes:
            if name_a.startswith(gp):
                name_a = name_a[len(gp):]
            if name_b.startswith(gp):
                name_b = name_b[len(gp):]
        # Strip tournament/format suffixes: "(bo3)", "- esl pro league ...", etc.
        _suffix_re = r"\s*\(bo\d+\).*$"
        _tourney_re = (
            r"\s*-\s+(?:esl |blast |pgl |iem |dreamhack|faceit|weplay|rievent"
            r"|major|champions|masters|challengers|lck|lpl|lec|lcs|vct|worlds"
            r"|msi |msc |lcl|cblol|ljl|pcs|spring|summer|winter|fall|split"
            r"|playoffs|qualifier|group|stage|round|final"
            r"|aorus |emea |nacl |lta |game changers|winner"
            r"|cct |gamers8|betboom|perfect\s*world|thunderpick|elisa |skyesports"
            r"|yalla|esea |open\s+qual|closed\s+qual|regular\s+season"
            r"|upper\s+bracket|lower\s+bracket|grand\s+final|elimination"
            r"|decider|promotion|relegation|showmatch|invitational"
            r"|lan\s+final|rmr |asia\s+league|americas|pacific|emea\s+league).*$"
        )
        # " - game N winner" / " - map N winner" suffix (must run before _tourney_re)
        _game_winner_re = r"\s*-\s+(?:game|map|match)\s+\d+\s+winner$"
        for name_ref in ("name_a", "name_b"):
            n = name_a if name_ref == "name_a" else name_b
            n = _re.sub(_suffix_re, "", n).strip()
            n = _re.sub(_game_winner_re, "", n, flags=_re.IGNORECASE).strip()
            n = _re.sub(_tourney_re, "", n, flags=_re.IGNORECASE).strip()
            # Strip trailing " map N", " game N", " game N winner", or bare "game N winner"
            n = _re.sub(r"(?:^|\s+)(?:map|game)\s+\d+(?:\s+winner)?$", "", n, flags=_re.IGNORECASE).strip()
            # Strip region tags in parens: "(KR)", "(CN)", "(EU)"
            n = _re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", n).strip()
            if name_ref == "name_a":
                name_a = n
            else:
                name_b = n
        return name_a.strip(), name_b.strip()

    # Common team name aliases: market_name → pandascore_name
    _TEAM_ALIASES: Dict[str, str] = {
        # China (LPL)
        "jdg": "jd gaming", "edg": "edward gaming", "rng": "royal never give up",
        "fpx": "funplus phoenix", "blg": "bilibili gaming", "lng": "lng esports",
        "tes": "top esports", "we": "team we", "wbg": "weibo gaming",
        "ig": "invictus gaming", "ra": "rare atom", "omg": "oh my god",
        # Korea (LCK)
        "gen": "gen.g", "geng": "gen.g", "dk": "dplus kia", "dplus": "dplus kia",
        "drx": "drx", "kt": "kt rolster", "hle": "hanwha life esports",
        "bro": "fredit brion", "ns": "nongshim redforce", "lsb": "liiv sandbox",
        "fox": "foxx esports",
        # EU/West (LEC/LCS)
        "g2": "g2 esports", "fnc": "fnatic", "mad": "mad lions",
        "sk": "sk gaming", "xls": "excel esports", "msf": "misfits gaming",
        "vit": "team vitality", "bds": "team bds", "koi": "koi",
        "tl": "team liquid", "c9": "cloud9", "100t": "100 thieves",
        "eg": "evil geniuses", "fly": "flyquest", "dig": "dignitas",
        "tsm": "tsm", "clg": "counter logic gaming", "gg": "golden guardians",
        "nrg": "nrg esports", "sr": "shopify rebellion",
        # CS2
        "navi": "natus vincere", "na'vi": "natus vincere",
        "faze": "faze clan", "col": "complexity gaming",
        "mouz": "mouz", "mousesports": "mouz", "nip": "ninjas in pyjamas",
        "heroic": "heroic", "ence": "ence", "ef": "eternal fire",
        "saw": "saw", "gl": "gamerlegion", "gamerlegion": "gamerlegion",
        "big": "big", "apeks": "apeks", "aurora": "aurora gaming",
        "3dmax": "3dmax", "imp": "imperial esports", "imperial": "imperial esports",
        "pain": "pain gaming", "mibr": "mibr", "furia": "furia",
        "9z": "9z team", "wildcard": "wildcard gaming",
        "grayhound": "grayhound gaming", "tyloo": "tyloo",
        "lynn vision": "lynn vision gaming", "mongols": "the mongolz",
        "the mongolz": "the mongolz",
        # Dota 2
        "og": "og", "nigma": "team nigma", "bb": "betboom team",
        "spirit": "team spirit", "vp": "virtus.pro",
        "tundra": "tundra esports", "gaimin": "gaimin gladiators",
        "xtreme": "xtreme gaming", "nouns": "nouns esports",
        # Valorant
        "prx": "paper rex", "zeta": "zeta division",
        "loud": "loud", "lev": "leviatán", "sen": "sentinels",
        "rrq": "rex regum qeon", "th": "team heretics",
        "kcorp": "karmine corp", "fut": "fut esports",
        "bleed": "bleed esports", "dfm": "detonation focusme",
        "geng": "gen.g",  # Valorant also uses geng
        # Multi-game
        "weibo": "weibo gaming",
    }

    def _match_team_name(self, name: str) -> Optional[str]:
        """Fuzzy match a team name to a PandaScore team ID.

        Tries: exact match → alias lookup → longest-substring-first match →
        reverse substring (name in known_name, for long market names) →
        word-boundary match for short names (2-3 chars) →
        difflib fuzzy match (0.85 threshold, last resort for typos).
        """
        name = name.lower().strip()
        if not name:
            return None

        # Exact match
        tid = self._team_name_to_id.get(name)
        if tid:
            return tid

        # Alias lookup: e.g. "jdg" → "jd gaming" → team_id
        alias = self._TEAM_ALIASES.get(name)
        if alias:
            tid = self._team_name_to_id.get(alias)
            if tid:
                return tid

        # Substring match: longest known name first to prevent short-name collision.
        for known_name, tid in sorted(
            self._team_name_to_id.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            # Skip very short known names for substring (handled by word-boundary below)
            if len(known_name) <= 3:
                continue
            if known_name in name:
                return tid

        # Reverse substring: market name may contain the full team name
        # e.g. name="hanwha life esports academy" contains known "hanwha life esports"
        for known_name, tid in sorted(
            self._team_name_to_id.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if len(known_name) <= 3:
                continue
            if name in known_name:
                return tid

        # Word-boundary match for short names (2-3 chars): "t1", "g2", "og"
        # Must match as whole word to avoid false positives
        import re as _re
        for known_name, tid in self._team_name_to_id.items():
            if len(known_name) <= 3:
                if _re.search(r'\b' + _re.escape(known_name) + r'\b', name):
                    return tid

        # Tier 6: fuzzy match via difflib (stdlib) — last resort for typos/transliterations
        from difflib import SequenceMatcher as _SM
        best_ratio, best_tid = 0.0, None
        for known_name, tid in self._team_name_to_id.items():
            if len(known_name) <= 2:
                continue
            ratio = _SM(None, name, known_name).ratio()
            if ratio > best_ratio:
                best_ratio, best_tid = ratio, tid
        if best_ratio >= 0.85 and best_tid is not None:
            return best_tid

        return None
