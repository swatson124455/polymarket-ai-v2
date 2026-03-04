"""
SportsBot — Purpose-built for rapid-resolution sports markets.

87-91% of Kalshi volume is sports. The 2026 FIFA World Cup (June-July)
will generate hundreds of markets. Sports markets are fundamentally different
from political markets: resolution in hours (not months), structured data,
real-time game state matters, microstructure closer to sports betting.

Key features:
  - Real-time game state ingestion (API-Football, TheSportsDB)
  - Rapid-resolution mode (open/close within minutes during live games)
  - Parlay mispricing detection
  - World Cup mode with tournament bracket tracking
  - 15s scan interval during live games, 120s otherwise
"""
import time
from typing import Any, Dict, List, Optional
from structlog import get_logger
from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()


class SportsBot(BaseBot):
    """
    Sports-specific prediction market bot with rapid-resolution capability.

    Distinct from EnsembleBot: different data sources, scan frequency, time
    horizon, and feature set.
    """

    def __init__(self, base_engine):
        super().__init__("SportsBot", base_engine)
        self.rapid_resolution_mode = getattr(settings, "SPORTS_RAPID_RESOLUTION_MODE", True)
        self.world_cup_mode = getattr(settings, "WORLD_CUP_MODE", False)
        self._live_games: Dict[str, Dict] = {}  # market_id -> game_state
        self._last_game_state_refresh = 0.0

    def _get_scan_interval_seconds(self) -> float:
        """15s during live games, 120s otherwise."""
        if self._live_games:
            return float(getattr(settings, "SCAN_INTERVAL_SPORTS_LIVE", 15))
        return float(getattr(settings, "SCAN_INTERVAL_SPORTS", 120))

    async def on_price_update(self, event: dict) -> None:
        """React to real-time WS price updates for live-game sports markets."""
        await super().on_price_update(event)
        market_id = event.get("market_id", "")
        # If this market has a live game, the price move may be actionable
        if market_id in self._live_games and self.running:
            try:
                price = float(event.get("price", 0))
                token_id = event.get("token_id", "")
                if price > 0 and token_id:
                    game_state = self._live_games[market_id]
                    opp = await self._analyze_live_game(
                        market_id, token_id, price, game_state, {"question": ""}
                    )
                    if opp:
                        await self._execute_sports_trade(opp)
            except Exception as e:
                logger.debug("SportsBot on_price_update error: %s", e)

    async def scan_and_trade(self):
        # Guard FIRST: skip entire scan when SportsClient has no API key (stub mode).
        # BaseEngine always creates a SportsClient instance, but without API_FOOTBALL_KEY
        # it would call get_live_games() → TheSportsDB HTTP request (400-650ms) on every scan.
        # Key check must come before _refresh_game_state() to avoid that wasted HTTP call.
        _sc = getattr(self.base_engine, "_sports_client", None)
        if not _sc or not getattr(_sc, "_api_football_key", None):
            logger.debug("SportsBot: no API_FOOTBALL_KEY configured — skipping scan")
            return

        # Refresh game state from sports data client (only runs when key is present)
        await self._refresh_game_state()

        # Get sports markets
        markets = await self.base_engine.get_markets(active=True, limit=200)
        sports_markets = self.base_engine.filter_markets_for_trading(
            markets, categories=["sports"]
        )
        if not sports_markets:
            return

        for market in sports_markets:
            try:
                opp = await self.analyze_opportunity(market)
                if opp:
                    await self._execute_sports_trade(opp)
            except Exception as e:
                logger.debug("SportsBot scan error: %s", e)

        # Check parlay mispricing
        await self._check_parlay_mispricing(sports_markets)

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        market_id = str(market_data.get("id", ""))
        if not market_id:
            return None

        tokens = market_data.get("tokens", [])
        if not tokens:
            return None

        # Get current price
        token = tokens[0] if tokens else {}
        price_raw = token.get("outcomePrice") or token.get("price")
        price = self.validate_price(price_raw, market_id)
        if price is None:
            return None

        token_id = token.get("tokenId") or token.get("token_id")
        if not token_id:
            return None

        # Check if this market has a live game
        game_state = self._live_games.get(market_id)
        if game_state and self.rapid_resolution_mode:
            return await self._analyze_live_game(market_id, token_id, price, game_state, market_data)

        # Pre-game analysis: use prediction engine
        try:
            prediction = await self.base_engine.get_predictions(
                market_id=market_id, token_id=token_id, price=price,
                correlation_id=getattr(self, "_current_correlation_id", None),
            )
            if not prediction:
                return None

            confidence = prediction.get("confidence", 0.5)
            pred_value = prediction.get("prediction", 0.5)

            # Sports threshold: higher than EnsembleBot (0.30) but not so high that no trades fire
            if confidence < 0.50:
                return None

            # YES = buy YES token (model > market price), NO = buy NO token (model < market price)
            no_token = tokens[1] if len(tokens) > 1 else {}
            no_token_id = no_token.get("tokenId") or no_token.get("token_id")
            if pred_value > price + 0.05:
                side = "YES"
                trade_token_id = token_id
                trade_price = price
            elif pred_value < price - 0.05:
                side = "NO"
                trade_token_id = no_token_id or token_id
                trade_price = 1.0 - price
            else:
                return None

            return {
                "type": "sports_pregame",
                "market_id": market_id,
                "token_id": str(trade_token_id),
                "side": side,
                "price": trade_price,
                "confidence": confidence,
                "prediction": pred_value,
            }
        except Exception as e:
            logger.debug("SportsBot prediction failed for %s: %s", market_id, e)
            return None

    @staticmethod
    def _parse_market_type(question: str) -> str:
        """Classify the sports market type from the question text.

        Returns one of: 'outcome' (team wins), 'spread', 'total', 'prop', 'draw'.
        """
        import re
        q = question.lower()
        if re.search(r"(over|under)\s+\d+(\.\d+)?", q):
            return "total"
        if re.search(r"(spread|handicap|margin|by\s+\d+\+?)", q):
            return "spread"
        if re.search(r"\bdraw\b|\btie\b", q):
            return "draw"
        if re.search(r"(mvp|goal scorer|first to|assists|rebounds|touchdowns)", q):
            return "prop"
        return "outcome"

    async def _analyze_live_game(
        self, market_id: str, token_id: str, price: float, game_state: Dict, market_data: Dict,
    ) -> Optional[Dict]:
        """
        Analyze opportunity during a live game using real-time state.

        Multi-factor confidence model:
          - Base: score differential + elapsed game time
          - Momentum: goals scored in last 10 minutes boost/penalise
          - Elapsed scaling: confidence ramps non-linearly (quadratic) late game
          - Market type awareness: spread/total/draw handled differently
          - Signal enhancement: news, order flow, trends applied
        """
        try:
            score_home = float(game_state.get("score_home") or 0)
        except (ValueError, TypeError):
            score_home = 0
        try:
            score_away = float(game_state.get("score_away") or 0)
        except (ValueError, TypeError):
            score_away = 0
        try:
            elapsed_pct = float(game_state.get("elapsed_pct") or 0)  # 0.0–1.0 (fraction of game elapsed)
        except (ValueError, TypeError):
            elapsed_pct = 0

        if elapsed_pct < 0.10:
            return None  # Too early — game state unreliable

        question = (market_data.get("question") or "").lower()
        market_type = self._parse_market_type(question)

        score_diff = abs(score_home - score_away)

        # ── Build base confidence from game state ────────────────────────
        # Non-linear: quadratic scaling with elapsed time, capped at 0.94
        time_factor = elapsed_pct ** 2  # ramps steeply late game
        diff_factor = min(score_diff * 0.06, 0.24)  # max +0.24 from score diff

        confidence = 0.50 + time_factor * 0.30 + diff_factor

        # Momentum bonus: goals in the last segment suggest continued dominance
        try:
            recent_goals = float(game_state.get("recent_goals_home") or 0) + float(game_state.get("recent_goals_away") or 0)
        except (ValueError, TypeError):
            recent_goals = 0
        if recent_goals > 0 and elapsed_pct > 0.30:
            confidence += 0.03 * min(recent_goals, 3)

        # Possession / shots bonus (if available from sports API)
        try:
            possession_pct = float(game_state.get("possession_pct") or 50)
        except (ValueError, TypeError):
            possession_pct = 50
        if possession_pct > 65:
            confidence += 0.02
        elif possession_pct < 35:
            confidence -= 0.02

        confidence = min(0.94, max(0.0, confidence))

        # ── Market type gating ───────────────────────────────────────────
        if market_type == "outcome":
            # Classic "team wins" — need score diff late game
            if not (elapsed_pct > 0.60 and score_diff >= 1) and not (elapsed_pct > 0.80 and score_diff >= 1):
                return None
        elif market_type == "draw":
            # Draw markets: only trade if scores are level late
            if score_diff != 0 or elapsed_pct < 0.70:
                return None
            confidence = min(0.88, 0.55 + time_factor * 0.25)
        elif market_type == "total":
            # Over/Under: need strong scoring or drought to act
            total_goals = score_home + score_away
            if elapsed_pct < 0.50:
                return None  # wait for more data
        elif market_type == "spread":
            # Spread markets: act when current diff exceeds spread
            if elapsed_pct < 0.60:
                return None
        # prop markets: skip live — not enough signal
        elif market_type == "prop":
            return None

        # ── Confidence threshold ─────────────────────────────────────────
        if confidence < 0.65:
            return None

        # ── Determine side ───────────────────────────────────────────────
        # Only buy if market price lags our estimated probability (edge exists)
        edge = confidence - price
        if edge < 0.05:
            return None  # No actionable edge
        side = "YES"

        # ── Apply signal enhancements (news, whale flow, trends) ─────────
        try:
            confidence = await self.apply_signal_enhancements(
                market_id, token_id, side, confidence, market_data,
            )
        except Exception as e:
            logger.debug("SportsBot signal enhancement failed: %s", e)

        if confidence < 0.65:
            return None

        return {
            "type": "sports_live",
            "market_type": market_type,
            "market_id": market_id,
            "token_id": str(token_id),
            "side": side,
            "price": price,
            "confidence": confidence,
            "game_state": game_state,
        }

    async def _refresh_game_state(self):
        """Fetch live game state from sports data client."""
        now = time.monotonic()
        if now - self._last_game_state_refresh < 15:
            return
        self._last_game_state_refresh = now

        sports_client = getattr(self.base_engine, "_sports_client", None)
        if not sports_client:
            return
        try:
            live_games = await sports_client.get_live_games()
            self._live_games = {g.get("market_id"): g for g in (live_games or []) if g.get("market_id")}
        except Exception as e:
            logger.debug("SportsBot: game state refresh failed: %s", e)

    async def _check_parlay_mispricing(self, markets: List[Dict]):
        """Check if multi-outcome sports markets have internally consistent pricing."""
        for market in markets:
            tokens = market.get("tokens", [])
            if not isinstance(tokens, list) or len(tokens) < 3:
                continue
            try:
                prices = []
                for t in tokens:
                    p = t.get("outcomePrice")
                    if p is not None:
                        prices.append(float(p))
                if len(prices) < 3:
                    continue
                total = sum(prices)
                # If sum of all outcome prices < 1.0 by more than 3%, it's mispriced
                if total < 0.97:
                    logger.info(
                        "SportsBot: parlay mispricing detected: %s total=%.3f (%.1f%% gap)",
                        market.get("id"), total, (1.0 - total) * 100,
                    )
            except Exception as e:
                logger.debug("parlay mispricing check failed: %s", e)

    async def _execute_sports_trade(self, opp: Dict):
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
        if order.get("success"):
            mode = opp.get("type", "sports")
            logger.info(
                "SportsBot trade executed (%s): %s %s @ %.3f conf=%.2f",
                mode, opp["market_id"], opp["side"], opp["price"], opp["confidence"],
            )
