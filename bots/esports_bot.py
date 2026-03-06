"""
EsportsBot — Pre-game + live in-play esports trading bot.

All 4 game titles: LoL, CS2, Dota 2, Valorant.
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
from config.settings import settings

logger = get_logger()


class EsportsBot(BaseBot):
    """
    Pre-game + live in-play esports trading bot.

    Covers match_winner, map_winner, tournament_winner, total_maps
    across LoL, CS2, Dota 2, and Valorant on Polymarket.
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
        self._trainer = None         # EsportsModelTrainer

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

        # Latency tracker: WS price move time vs last PandaScore refresh (bounded)
        self._latency_samples: List[float] = []  # seconds since last PandaScore refresh
        self._max_latency_samples = 100

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
            from esports.data.riot_api_client import RiotAPIClient
            riot_client = RiotAPIClient(api_key=riot_key)
            await riot_client.init()

        self._patch_drift = PatchDriftDetector(riot_client=riot_client)

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

        # Initialize trainer for periodic retraining
        try:
            from esports.models.esports_trainer import EsportsModelTrainer
            self._trainer = EsportsModelTrainer(pandascore_client=self._pandascore)
        except Exception:
            logger.debug("EsportsBot: trainer not available")

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

        # Step 0: Auto-retrain models if interval elapsed
        if self._trainer and self._trainer.needs_retrain("lol"):
            try:
                result = await asyncio.wait_for(
                    self._trainer.train_game("lol", db=db), timeout=300.0,
                )
                if result.get("graduated"):
                    # Reload trained model
                    if self._lol_model:
                        self._lol_model.load()
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsBot: LoL retrain failed", error=str(exc))

        if self._trainer and self._trainer.needs_retrain("cs2"):
            try:
                result = await asyncio.wait_for(
                    self._trainer.train_game("cs2", db=db), timeout=300.0,
                )
                if result.get("graduated"):
                    if self._cs2_model:
                        self._cs2_model.load()
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsBot: CS2 retrain failed", error=str(exc))

        # Step 0b: Check rolling accuracy — auto-disable if below threshold
        min_acc = float(getattr(settings, "ESPORTS_MIN_ACCURACY_TO_TRADE", 0.52))
        try:
            from esports.data.esports_db import get_rolling_accuracy
            for game in ("lol", "cs2"):
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
        except Exception:
            pass

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
        for market in esports_markets:
            try:
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

        if confidence < self._min_confidence:
            return None

        # Log prediction for accuracy tracking
        db = getattr(self.base_engine, "db", None)
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
        except Exception:
            pass

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
                    prob = await asyncio.to_thread(
                        self._lol_model.predict, game_state
                    )
                    if 0.0 < prob < 1.0:
                        # Derive Glicko-2 expected from team_strength_diff for agreement tracking
                        tsd = float(game_state.get("team_strength_diff", 0.0))
                        glicko2_est = max(0.05, min(0.95, 0.5 + tsd / 2))
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": prob, "glicko2_est": glicko2_est,
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

        # "Easy mode" fallback: Glicko-2 expected score from team strength ratings.
        # Replaces base prediction engine (politics/crypto model) which produced
        # random predictions for esports markets — cross-contamination.
        # Graduation: once ML models pass accuracy >= 55% + brier <= 0.24,
        # they take over and Glicko-2 becomes just one blend component.
        try:
            glicko2_prob = self._get_glicko2_prediction(market_data, game)
            if glicko2_prob is not None:
                self._prediction_cache[market_id] = {
                    "prob": glicko2_prob, "ts": time.monotonic(), "game": game,
                    "ml_raw": None, "glicko2_est": glicko2_prob,
                }
                return glicko2_prob
        except Exception:
            pass

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
    def _detect_game(question: str) -> Optional[str]:
        """Detect game title from market question text."""
        q = question.lower()
        if any(kw in q for kw in ("league of legends", "lol ", "lck", "lec", "lpl", "lcs", "worlds", "msi")):
            return "lol"
        if any(kw in q for kw in ("counter-strike", "cs2", "csgo", "blast premier", "esl", "pgl", "iem")):
            return "cs2"
        if any(kw in q for kw in ("dota", "the international", " ti ", "dpc")):
            return "dota2"
        if any(kw in q for kw in ("valorant", "vct", "champions tour")):
            return "valorant"
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
        Compute confluence score (0.0-1.0) from multiple signals.

        Weights:
          - Model prediction edge: 37%
          - Whale direction alignment: 23%
          - Orderbook imbalance: 18%
          - Prediction freshness: 14%
          - Model agreement (Glicko-2 vs ML): 8%

        Only trades when score > ESPORTS_CONFLUENCE_MIN (default 0.60).
        """
        import math

        # 1. Model edge signal (37%) — normalized to 0-1
        edge_score = min(abs(model_edge) / self._min_edge, 1.0)

        # 2. Whale signal (23%) — check if whale trades align with our side
        whale_score = 0.5  # Neutral when no whale data
        try:
            whale_queue = getattr(self, "_whale_priority_queue", None)
            whale_markets = getattr(self, "_whale_priority_markets", set())
            if market_id in whale_markets:
                # Whale is active on this market — alignment boost
                whale_score = 0.8
        except Exception:
            pass

        # 3. Orderbook imbalance (18%)
        ob_score = 0.5  # Neutral
        try:
            ob_tracker = getattr(self.base_engine, "orderbook_tracker", None)
            if ob_tracker:
                signal = ob_tracker.get_imbalance_signal(token_id)
                if signal:
                    direction = signal.get("direction", "")
                    strength = float(signal.get("strength", 0.0))
                    # YES side wants bullish, NO side wants bearish
                    if (side == "YES" and direction == "bullish") or \
                       (side == "NO" and direction == "bearish"):
                        ob_score = 0.5 + 0.5 * strength
                    elif direction:
                        ob_score = 0.5 - 0.5 * strength
        except Exception:
            pass

        # 4. Prediction freshness (14%) — exponential decay
        age_seconds = time.monotonic() - prediction_ts if prediction_ts > 0 else 0
        freshness_score = math.exp(-age_seconds / 120.0)  # 0s=1.0, 60s=0.61, 120s=0.37

        # 5. Model agreement (8%) — penalize when Glicko-2 and ML disagree
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
            0.37 * edge_score +
            0.23 * whale_score +
            0.18 * ob_score +
            0.14 * freshness_score +
            0.08 * agreement_score
        )

        return round(confluence, 4)

    async def _execute_esports_trade(self, opp: Dict) -> None:
        """Execute trade with maker-first, taker-fallback strategy."""
        size = await self.calculate_bot_position_size(opp["confidence"], opp["price"])
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
            logger.info(
                "EsportsBot trade executed",
                type=opp.get("type"),
                game=opp.get("game"),
                market_type=opp.get("market_type"),
                market_id=opp["market_id"],
                side=opp["side"],
                price=opp["price"],
                confidence=round(opp["confidence"], 3),
                edge=opp.get("edge"),
                size=round(size, 2),
            )

    # ── Glicko-2 "easy mode" helpers ──────────────────────────────────────

    async def _init_glicko2_trackers(self, db) -> None:
        """Build Glicko-2 trackers and team name→ID mapping from DB training data.

        Called once during start(). Rebuilds Glicko-2 ratings from historical
        match results in esports_training_data table, and team name→ID mapping
        from esports_teams table. This gives the bot a real prediction source
        (63% accuracy per EsportsBench) while ML models are training.
        """
        if db is None or not getattr(db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            from esports.models.glicko2 import Glicko2Tracker

            # 1. Build team name → ID mapping from esports_teams
            async with db.get_session() as session:
                rows = await session.execute(text(
                    "SELECT external_id, name FROM esports_teams WHERE name IS NOT NULL"
                ))
                for row in rows.fetchall():
                    tid, name = str(row[0]).strip(), str(row[1]).strip()
                    if tid and name:
                        self._team_name_to_id[name.lower()] = tid

            # 2. Build Glicko-2 trackers from training data match results.
            # Schema: team_a/team_b are names (not IDs), outcome is smallint
            # (0=team_a wins, 1=team_b wins), no match_date — use scheduled_at.
            for game in ("lol", "cs2"):
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
                    # Use lowercased team names as IDs for Glicko-2 tracking
                    a_id = team_a_name.lower()
                    b_id = team_b_name.lower()
                    # outcome=0 means team_a wins, outcome=1 means team_b wins
                    w = "a" if outcome == 0 else "b"
                    tracker.process_match(a_id, b_id, winner=w)
                    # Populate name→id mapping (name IS the id here)
                    self._team_name_to_id[a_id] = a_id
                    self._team_name_to_id[b_id] = b_id

                self._glicko2_trackers[game] = tracker
                logger.info(
                    "EsportsBot: Glicko-2 tracker initialized",
                    game=game,
                    matches_processed=tracker.match_count,
                    teams_rated=len(tracker._ratings),
                )
        except Exception as exc:
            logger.debug("EsportsBot: Glicko-2 init failed (non-fatal)", error=str(exc))

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
            team_a_id = self._match_team_name(name_a)
            team_b_id = self._match_team_name(name_b)

        # Pattern 2: "Will [Team] beat/defeat [Team]?"
        if not team_a_id or not team_b_id:
            beat_match = re.search(
                r"(?:will\s+)?(.+?)\s+(?:beat|defeat|win against|win over)\s+(.+?)(?:\?|$)",
                question,
            )
            if beat_match:
                team_a_id = self._match_team_name(beat_match.group(1).strip())
                team_b_id = self._match_team_name(beat_match.group(2).strip())

        if not team_a_id or not team_b_id:
            return None

        # Get Glicko-2 expected score
        try:
            rating_a = tracker.get_rating(team_a_id)
            rating_b = tracker.get_rating(team_b_id)
            # Only predict if both teams have been rated (phi < default 350)
            if rating_a.phi >= 350.0 or rating_b.phi >= 350.0:
                return None
            prob = tracker.expected_score(team_a_id, team_b_id)
            if 0.05 < prob < 0.95:
                return prob
        except Exception:
            pass
        return None

    def _match_team_name(self, name: str) -> Optional[str]:
        """Fuzzy match a team name to a PandaScore team ID.

        Tries exact match first, then substring match for common abbreviations
        like "T1", "G2", "NAVI" that may appear differently in market questions.
        """
        name = name.lower().strip()
        if not name:
            return None

        # Exact match
        tid = self._team_name_to_id.get(name)
        if tid:
            return tid

        # Substring match: check if any known team name is contained in the query name
        for known_name, tid in self._team_name_to_id.items():
            if known_name in name or name in known_name:
                return tid

        return None
