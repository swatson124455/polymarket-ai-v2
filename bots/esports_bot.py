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
        self._pandascore = None      # Lazy init in start()
        self._patch_drift = None     # PatchDriftDetector instance
        self._market_scanner = None  # EsportsMarketScanner instance
        self._lol_model = None       # LoLWinModel
        self._cs2_model = None       # CS2EconomyModel
        self._trainer = None         # EsportsModelTrainer

        # Live match tracking
        self._live_matches: Dict[str, Dict] = {}  # match_id → PandaScore match data
        self._last_live_refresh = 0.0

        # Prediction cache for WS reactive path
        self._prediction_cache: Dict[str, Dict] = {}  # market_id → {prob, ts, game}

        # Latency tracker: WS price move time vs last PandaScore refresh (bounded)
        self._latency_samples: List[float] = []  # seconds since last PandaScore refresh
        self._max_latency_samples = 100

        # Settings
        self._min_edge = float(getattr(settings, "ESPORTS_MIN_EDGE", 0.08))
        self._min_confidence = float(getattr(settings, "ESPORTS_MIN_CONFIDENCE", 0.55))
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
        logger.info(
            "EsportsBot: initialized",
            pandascore=True,
            riot_api=bool(riot_key),
            lol_model_trained=lol_trained,
            cs2_model_trained=cs2_trained,
        )

        await super().start()

    async def stop(self) -> None:
        """Clean up HTTP clients."""
        if self._pandascore:
            await self._pandascore.close()
        await super().stop()

    async def on_price_update(self, event: dict) -> None:
        """
        React to WS price updates — cross-reference cached prediction vs new price.

        Only trades when:
          1. We have a cached prediction for this market (populated during scan)
          2. Price move exceeds significance threshold
          3. Cooldown per market has elapsed
          4. Confluence score passes threshold
          5. Model is graduated (>55% accuracy)
        """
        await super().on_price_update(event)
        if not self.running:
            return

        market_id = event.get("market_id", "")
        token_id = event.get("token_id", "")
        new_price = float(event.get("price", 0))
        if not market_id or new_price <= 0:
            return

        # Track latency: time since last PandaScore refresh
        if self._last_live_refresh > 0:
            latency = time.monotonic() - self._last_live_refresh
            if len(self._latency_samples) >= self._max_latency_samples:
                self._latency_samples.pop(0)
            self._latency_samples.append(latency)

        # Only react to markets we've already analyzed
        cached = self._prediction_cache.get(market_id)
        if not cached:
            return

        # Significance threshold
        threshold = float(getattr(settings, "ESPORTS_WS_PRICE_CHANGE_PCT", 0.01))
        if not hasattr(self, "_ws_prev_prices"):
            self._ws_prev_prices: dict = {}
        old_price = self._ws_prev_prices.get(market_id)
        self._ws_prev_prices[market_id] = new_price
        if old_price is None or abs(new_price - old_price) / max(old_price, 0.01) < threshold:
            return

        # Cooldown
        now = time.monotonic()
        if not hasattr(self, "_ws_cooldowns"):
            self._ws_cooldowns: dict = {}
        cooldown = int(getattr(settings, "ESPORTS_WS_COOLDOWN_SECONDS", 10))
        if now - self._ws_cooldowns.get(market_id, 0) < cooldown:
            return
        self._ws_cooldowns[market_id] = now

        # Recalculate edge with new price
        model_prob = cached["prob"]
        game = cached.get("game", "")
        edge = model_prob - new_price

        if abs(edge) < self._min_edge:
            return

        side = "YES" if edge > 0 else "NO"
        trade_price = new_price if side == "YES" else (1.0 - new_price)

        # Confluence gate
        confluence = self._compute_confluence_score(
            model_edge=edge,
            side=side,
            token_id=token_id,
            market_id=market_id,
            prediction_ts=cached.get("ts", 0),
        )
        confluence_min = float(getattr(settings, "ESPORTS_CONFLUENCE_MIN", 0.60))
        if confluence < confluence_min:
            return

        # Position check
        og = getattr(self.base_engine, "order_gateway", None)
        if og is not None and og.has_open_position(self.bot_name, str(market_id)):
            return

        try:
            confidence = model_prob if side == "YES" else (1.0 - model_prob)
            opp = {
                "type": "esports_ws_reactive",
                "market_id": market_id,
                "token_id": token_id,
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
                price_move=f"{old_price:.4f}→{new_price:.4f}",
                edge=round(abs(edge), 4),
                confluence=confluence,
            )
            await self._execute_esports_trade(opp)
        except Exception as exc:
            logger.debug("EsportsBot WS reactive failed", error=str(exc))

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

        # Step 3: Get esports markets from Polymarket
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
                        # Cache prediction for WS reactive path
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
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
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                        }
                        return prob
            except Exception:
                pass

        # Fallback: use base prediction engine
        try:
            prediction = await self.base_engine.get_predictions(
                market_id=market_id,
                token_id=token_id,
                price=price,
                correlation_id=getattr(self, "_current_correlation_id", None),
            )
            if prediction:
                pred_value = prediction.get("prediction")
                if pred_value is not None:
                    return float(pred_value)
        except Exception as exc:
            logger.debug("EsportsBot: prediction engine failed", error=str(exc))

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
          - Model prediction edge: 40%
          - Whale direction alignment: 25%
          - Orderbook imbalance: 20%
          - Prediction freshness: 15%

        Only trades when score > ESPORTS_CONFLUENCE_MIN (default 0.60).
        """
        import math

        # 1. Model edge signal (40%) — normalized to 0-1
        edge_score = min(abs(model_edge) / self._min_edge, 1.0)

        # 2. Whale signal (25%) — check if whale trades align with our side
        whale_score = 0.5  # Neutral when no whale data
        try:
            whale_queue = getattr(self, "_whale_priority_queue", None)
            whale_markets = getattr(self, "_whale_priority_markets", set())
            if market_id in whale_markets:
                # Whale is active on this market — alignment boost
                whale_score = 0.8
        except Exception:
            pass

        # 3. Orderbook imbalance (20%)
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

        # 4. Prediction freshness (15%) — exponential decay
        age_seconds = time.monotonic() - prediction_ts if prediction_ts > 0 else 0
        freshness_score = math.exp(-age_seconds / 120.0)  # 0s=1.0, 60s=0.61, 120s=0.37

        # Weighted sum
        confluence = (
            0.40 * edge_score +
            0.25 * whale_score +
            0.20 * ob_score +
            0.15 * freshness_score
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
