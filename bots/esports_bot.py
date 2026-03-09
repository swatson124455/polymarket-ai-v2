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
import time
from typing import Any, Dict, List, Optional

from structlog import get_logger

from bots.base_bot import BaseBot
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

        # Prediction cache for WS reactive path
        self._prediction_cache: Dict[str, Dict] = {}  # market_id → {prob, ts, game}

        # Token map: market_id → {"yes": yes_token_id, "no": no_token_id}
        # Populated during scan, used by WS path to identify YES vs NO token prices.
        self._market_token_map: Dict[str, Dict[str, str]] = {}

        # Pending trades: market_ids currently being executed (race condition guard)
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

        # Settings
        # "Easy mode": relaxed thresholds until models graduate, then tighten.
        # Graduation = accuracy >= 55% + brier <= 0.24 on holdout.
        self._models_graduated = False
        self._min_edge = float(getattr(settings, "ESPORTS_MIN_EDGE", 0.05))  # 5% easy mode
        self._min_confidence = float(getattr(settings, "ESPORTS_MIN_CONFIDENCE", 0.52))  # easy mode
        self._max_edge = float(getattr(settings, "ESPORTS_MAX_EDGE", 0.20))  # 20% sanity cap
        self._maker_timeout = float(
            getattr(settings, "ESPORTS_MAKER_FALLBACK_TIMEOUT_S", 3.0)
        )

    def _get_scan_interval_seconds(self) -> float:
        """10s during live matches, 120s otherwise."""
        if self._live_matches:
            return float(getattr(settings, "SCAN_INTERVAL_ESPORTS_LIVE", 10))
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

    async def scan_and_trade(self) -> None:
        """
        Main scan loop body.

        1. Check patch observation mode
        2. Refresh live match data
        3. Get esports markets from Polymarket
        4. Analyze each market for edge
        """
        db = getattr(self.base_engine, "db", None)

        # E4: Monitoring thresholds — check per-game Brier and emit alerts
        await self._check_monitoring_thresholds(db)

        # Step 0: Auto-retrain models in background (non-blocking)
        # Clean up completed background training tasks
        for game_key in list(self._bg_train_tasks):
            task = self._bg_train_tasks[game_key]
            if task.done():
                del self._bg_train_tasks[game_key]

        for _retrain_game in ("lol", "cs2"):
            if (self._trainer
                    and self._trainer.needs_retrain(_retrain_game)
                    and _retrain_game not in self._bg_train_tasks):
                self._bg_train_tasks[_retrain_game] = asyncio.create_task(
                    self._train_in_background(_retrain_game, db),
                    name=f"retrain_{_retrain_game}",
                )

        # E7: Cross-game XGBoost retrain (pools all 8 games)
        if (self._trainer
                and self._trainer.needs_retrain("cross_game")
                and "cross_game" not in self._bg_train_tasks):
            self._bg_train_tasks["cross_game"] = asyncio.create_task(
                self._train_in_background("cross_game", db),
                name="retrain_cross_game",
            )

        # Step 0a: Collect historical data for games missing Glicko-2 trackers (one-shot)
        for _game in ("dota2", "valorant", "cod", "r6", "sc2", "rl"):
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

        # Step 1: Patch drift check — skip live trading during observation mode
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

        # Step 2: Refresh live match data from PandaScore
        await self._refresh_live_matches()

        # Step 3: Get esports markets via dedicated EsportsMarketService.
        # Bypasses broken Gamma API path (get_markets returns 0 esports).
        # Queries DB directly for category='esports', no liquidity filter.
        if self._market_service:
            esports_markets = await self._market_service.get_tradeable_esports_markets()
        else:
            # Fallback if market service failed to init (shouldn't happen)
            markets = await self.base_engine.get_markets(active=True, limit=200)
            esports_markets = self.base_engine.filter_markets_for_trading(
                markets, categories=["esports"]
            )
        self._last_scan_markets = len(esports_markets) if esports_markets else 0
        if not esports_markets:
            return

        # Step 4: Analyze each market
        _opps = 0
        _trades = 0
        og = getattr(self.base_engine, "order_gateway", None)
        for market in esports_markets:
            try:
                # Skip markets where we already have an open position
                mid = str(market.get("id", ""))
                if og and mid and og.has_open_position(self.bot_name, mid):
                    continue
                opp = await self.analyze_opportunity(market)
                if opp:
                    _opps += 1
                    await self._execute_esports_trade(opp)
                    _trades += 1
            except Exception as exc:
                logger.debug("EsportsBot scan error: %s", exc)
        self._last_scan_opportunities = _opps
        self._last_scan_trades = _trades

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """
        Analyze a single esports market for trading opportunity.

        1. Classify market type + detect game
        2. Get model prediction (game-specific or prediction engine)
        3. Validate edge: model_prob - poly_price > ESPORTS_MIN_EDGE
        4. Build trade opportunity if edge exists
        """
        market_id = str(market_data.get("id", ""))
        if not market_id:
            return None

        tokens = market_data.get("tokens", [])
        if not tokens:
            return None

        token = tokens[0]
        price_raw = token.get("outcomePrice") or token.get("price")
        price = self.validate_price(price_raw, market_id)
        if price is None:
            return None

        token_id = token.get("tokenId") or token.get("token_id")
        if not token_id:
            return None

        question = (market_data.get("question") or "").lower()

        # Detect game title
        game = self._detect_game(question)
        if not game:
            return None

        # E4: Check monitoring-halted games
        if game in self._monitoring_halted_games:
            return None

        # Exposure concentration check (per-game cap)
        max_game = float(getattr(settings, "ESPORTS_MAX_GAME_EXPOSURE", 300.0))
        if self._game_exposure.get(game, 0.0) >= max_game:
            return None

        # Check observation mode for this game
        if self._patch_drift and self._patch_drift.is_observation_mode(game):
            logger.debug(
                "EsportsBot: observation mode active (paper only)",
                game=game,
                market_id=market_id,
            )
            return None

        # Check if halted
        if self._patch_drift and self._patch_drift.is_halted(game):
            return None

        market_type = self._classify_market_type(question)

        # Get model prediction
        model_prob = await self._get_model_prediction(
            game, market_type, market_id, token_id, price, market_data
        )
        if model_prob is None:
            return None

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
            return None

        # Edge sanity cap — reject unrealistically large edges
        if edge > self._max_edge:
            logger.debug(
                "EsportsBot: edge exceeds sanity cap",
                market_id=market_id,
                edge=round(edge, 4),
                max_edge=self._max_edge,
            )
            return None

        # Tournament phase confidence boost (regime detection for esports)
        confidence *= self._get_tournament_phase_mult(market_data)

        if confidence < self._min_confidence:
            return None

        # Log prediction for accuracy tracking (dedup: skip if unchanged within 10 min)
        db = getattr(self.base_engine, "db", None)
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
            return None

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
            except Exception:
                pass

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
            except Exception:
                pass

        # Dota2/Valorant: use ML model with Glicko-2 features (pre-match only)
        if game == "dota2" and self._dota2_model and self._dota2_model.is_trained:
            try:
                glicko2_prob = self._get_glicko2_prediction(market_data, game)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._dota2_model.predict(game_state)
                        # Blend ML with Glicko-2: 60% ML, 40% Glicko-2
                        prob = 0.6 * prob + 0.4 * glicko2_prob
                        prob = max(0.05, min(0.95, prob))
                        # OpenDota form adjustment (small ±3% based on recent form)
                        prob = await self._opendota_form_adjustment(market_data, prob)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._dota2_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                        }
                        return prob
            except Exception:
                pass

        if game == "valorant" and self._valorant_model and self._valorant_model.is_trained:
            try:
                glicko2_prob = self._get_glicko2_prediction(market_data, game)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._valorant_model.predict(game_state)
                        prob = 0.6 * prob + 0.4 * glicko2_prob
                        prob = max(0.05, min(0.95, prob))
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._valorant_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                        }
                        return prob
            except Exception:
                pass

        # "Easy mode" fallback: Glicko-2 expected score from team strength ratings.
        # Replaces base prediction engine (politics/crypto model) which produced
        # random predictions for esports markets — cross-contamination.
        # Graduation: once ML models pass accuracy >= 55% + brier <= 0.24,
        # they take over and Glicko-2 becomes just one blend component.
        try:
            glicko2_prob = self._get_glicko2_prediction(market_data, game)
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
                            float(self._CROSS_GAME_IDS[game]),
                            game_state.get("best_of", 1.0),
                        ]
                        xgb_prob = float(
                            self._cross_game_model.predict_proba(
                                _np.array([feats], dtype=_np.float32)
                            )[0][1]
                        )
                        # Blend: 40% XGB + 60% Glicko-2 (Glicko-2 anchored)
                        xgb_raw = xgb_prob
                        glicko2_prob = 0.6 * glicko2_prob + 0.4 * xgb_prob
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

        try:
            live = await asyncio.wait_for(
                self._pandascore.get_live_matches(), timeout=10.0
            )
            new_live = {}
            for match in (live or []):
                mid = str(match.get("id", ""))
                if mid:
                    new_live[mid] = match
            self._live_matches = new_live
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("EsportsBot: live match refresh failed", error=str(exc))

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
    def _get_tournament_phase_mult(market_data: Dict) -> float:
        """Tournament phase confidence multiplier (esports regime detection).

        Group stage: higher variance, more upsets → 0.85× confidence.
        Bracket/playoffs: reduced field, better accuracy → 1.0× baseline.
        Grand finals: highest accuracy window → 1.15× boost.

        Falls back to 1.0 if phase not detected.
        """
        serie_type = str(market_data.get("serie_type", "")).lower()
        question = str(market_data.get("question", "")).lower()

        # Detect from PandaScore serie_type (primary)
        if serie_type in ("group", "group_stage", "round_robin"):
            return 0.85
        if serie_type in ("grand_final", "grand_finals", "final", "finals"):
            return 1.15
        if serie_type in ("bracket", "playoff", "quarterfinal", "semifinal"):
            return 1.0

        # Fallback: detect from market question text
        if any(kw in question for kw in ("group stage", "group play", "round robin")):
            return 0.85
        if any(kw in question for kw in ("grand final", "championship", "finals")):
            return 1.15

        return 1.0

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

    @staticmethod
    def _detect_game(question: str) -> Optional[str]:
        """Detect game title from market question text."""
        q = question.lower()
        if any(kw in q for kw in ("league of legends", "lol ", "lck", "lec", "lpl", "lcs", "worlds", "msi")):
            return "lol"
        if any(kw in q for kw in ("counter-strike", "cs2", "csgo", "blast premier", "esl ", "pgl ", "iem ")):
            return "cs2"
        if any(kw in q for kw in ("dota", "the international", " ti ", "dpc")):
            return "dota2"
        if any(kw in q for kw in ("valorant", "vct", "champions tour")):
            return "valorant"
        if any(kw in q for kw in ("call of duty", "cod ")):
            return "cod"
        if any(kw in q for kw in ("rainbow six", "r6 ", "six invitational")):
            return "r6"
        if any(kw in q for kw in ("starcraft", "sc2 ", "sc2:", "brood war")):
            return "sc2"
        if any(kw in q for kw in ("rocket league", "rlcs")):
            return "rl"
        return None

    @staticmethod
    def _classify_market_type(question: str) -> str:
        """Classify market type from question text."""
        q = question.lower()
        if any(kw in q for kw in ("map ", "game 1", "game 2", "game 3", "game 4", "game 5")):
            return "map_winner"
        if any(kw in q for kw in ("tournament", "championship", "champion", "split winner", "season")):
            return "tournament_winner"
        if any(kw in q for kw in ("total maps", "over", "under", "maps played")):
            return "total_maps"
        if any(kw in q for kw in ("first blood", "first kill")):
            return "first_blood"
        if any(kw in q for kw in ("mvp", "kills", "assists")):
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
            120.0 if is_live else 600.0,
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
        """Execute trade with maker-first, taker-fallback strategy."""
        size = await self.calculate_bot_position_size(opp["confidence"], opp["price"], category="esports")
        if size <= 0:
            return

        order = await self.place_order(
            market_id=opp["market_id"],
            token_id=opp["token_id"],
            side=opp["side"],
            size=size,
            price=opp["price"],
            confidence=opp["confidence"],
        )

        if order and order.get("success"):
            # Update exposure tracking
            game = opp.get("game", "")
            self._game_exposure[game] = self._game_exposure.get(game, 0.0) + size
            tournament = opp.get("tournament", "")
            if tournament:
                self._tournament_exposure[tournament] = (
                    self._tournament_exposure.get(tournament, 0.0) + size
                )

            logger.info(
                "EsportsBot trade executed",
                type=opp.get("type"),
                game=game,
                market_type=opp.get("market_type"),
                market_id=opp["market_id"],
                side=opp["side"],
                price=opp["price"],
                confidence=round(opp["confidence"], 3),
                edge=opp.get("edge"),
                size=round(size, 2),
                game_exposure=round(self._game_exposure.get(game, 0.0), 2),
            )

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
        except Exception as exc:
            logger.debug("esportsbot_monitoring_check_failed", error=str(exc))

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

    def _get_glicko2_prediction(
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
                team_a_id = self._match_team_name(ba)
                team_b_id = self._match_team_name(bb)

        if not team_a_id or not team_b_id:
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

            return {
                "team_strength_diff": expected - 0.5,
                "matchup_uncertainty": (rating_a.phi + rating_b.phi) / 700.0,
                "rd_asymmetry": (rating_a.phi - rating_b.phi) / 350.0,
                "team_a_volatility": rating_a.sigma / 0.06,
                "team_b_volatility": rating_b.sigma / 0.06,
                "best_of": 1.0,  # Default; overridden if series data available
            }
        except Exception:
            return None

    @staticmethod
    def _clean_team_names(name_a: str, name_b: str) -> tuple:
        """Strip game title prefixes and tournament/format suffixes from extracted team names.

        Regex captures game titles (e.g. "counter-strike: themongolz") and
        tournament suffixes (e.g. "pain (bo3) - esl pro league stage 2").
        This method cleans both before fuzzy matching.
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
        )
        for gp in _game_prefixes:
            if name_a.startswith(gp):
                name_a = name_a[len(gp):]
            if name_b.startswith(gp):
                name_b = name_b[len(gp):]
        # Strip tournament/format suffixes: "(bo3)", "- esl pro league ...", etc.
        name_b = _re.sub(r"\s*\(bo\d+\).*$", "", name_b).strip()
        name_b = _re.sub(
            r"\s*-\s+(?:esl |blast |pgl |iem |dreamhack|faceit|weplay|rievent).*$",
            "", name_b,
        ).strip()
        # Same cleanup for name_a (less common but possible)
        name_a = _re.sub(r"\s*\(bo\d+\).*$", "", name_a).strip()
        return name_a.strip(), name_b.strip()

    def _match_team_name(self, name: str) -> Optional[str]:
        """Fuzzy match a team name to a PandaScore team ID.

        Tries exact match first, then longest-substring-first match to prevent
        short team names (e.g. "t1", "g2") from colliding inside longer strings.
        """
        name = name.lower().strip()
        if not name:
            return None

        # Exact match
        tid = self._team_name_to_id.get(name)
        if tid:
            return tid

        # Substring match: longest known name first to prevent short-name collision.
        # Only check known_name in name (not bidirectional) to avoid "t1" matching "fnatic".
        for known_name, tid in sorted(
            self._team_name_to_id.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if known_name in name:
                return tid

        return None
