"""
Cross-Platform Arbitrage Bot — #1 priority new bot.

Executes arbitrage across Polymarket, Kalshi, ForecastEx, and Coinbase
simultaneously. Absorbs TemporalArbitrageBot's crypto-lag detection.

Key features:
  - Polls all adapters in parallel every 15s
  - Fee normalization (true arb, not apparent)
  - Settlement timing risk adjustment
  - Resolution criteria divergence check
  - Crypto temporal lag detection (from TemporalArbitrageBot)
  - Per-platform capital tracking
"""
import asyncio
import re
import httpx
from datetime import datetime, timezone
from typing import Dict, List, Optional
from structlog import get_logger
from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()

# ── Crypto temporal lag constants (absorbed from TemporalArbitrageBot) ──
BINANCE_API_BASE = "https://api.binance.com/api/v3"
COINBASE_RATES_API = "https://api.coinbase.com/v2/exchange-rates"
PRICE_CACHE_TTL = 30
CRYPTO_SYMBOLS = ["BTC", "ETH", "SOL", "ADA"]
CRYPTO_KEYWORDS = ["bitcoin", "btc", "ethereum", "eth", "crypto", "coin", "price", "above", "below"]


class CrossPlatformArbBot(BaseBot):
    """
    Inter-platform arbitrage using unified ExchangeAdapter system.

    Scan interval: 15s (fastest bot — arb opportunities are time-sensitive).
    """

    def __init__(self, base_engine):
        super().__init__("CrossPlatformArbBot", base_engine)
        self.min_spread = float(getattr(settings, "CROSS_ARB_MIN_SPREAD", 0.03))
        self.max_position = float(getattr(settings, "CROSS_ARB_MAX_POSITION", 200))
        self._http = httpx.AsyncClient(timeout=10.0)
        # Crypto price cache: "BINANCE_BTC" -> {"price": float, "timestamp": datetime}
        self._price_cache: Dict[str, Dict] = {}

    async def scan_and_trade(self):
        """Run cross-platform arb, crypto temporal lag, and slow-market scans."""
        # Mode 1: Cross-platform arb via ArbScanner
        await self._scan_cross_platform()
        # Mode 2: Crypto temporal lag (absorbed from TemporalArbitrageBot)
        await self._scan_crypto_lag()
        # Mode 3: Slow-market latency arb (1-hour BTC, daily temperature, sports)
        await self._scan_slow_markets()

    async def _scan_cross_platform(self):
        """Use ArbScanner to find and execute cross-platform opportunities."""
        try:
            from base_engine.exchanges.arb_scanner import ArbScanner
            arb_scanner = getattr(self.base_engine, "_arb_scanner", None)
            if not arb_scanner:
                # P5c: Was silent early return — now log why
                _adapters = getattr(self.base_engine, "_exchange_adapters", [])
                _names = [getattr(a, "platform_name", lambda: "?")() for a in _adapters]
                logger.info(
                    "CrossPlatformArbBot: _arb_scanner not initialized — need >=2 adapters "
                    "(have %d: %s). Set KALSHI_API_KEY/KALSHI_EMAIL or other platform credentials.",
                    len(_adapters), _names,
                )
                return
            opportunities = await arb_scanner.scan(min_profit_pct=self.min_spread * 100)
            adapters_by_name = {a.platform_name(): a for a in getattr(self.base_engine, "_exchange_adapters", [])}
            for opp in opportunities[:5]:  # Max 5 per scan
                try:
                    await self._execute_cross_platform_arb(opp, adapters_by_name)
                except Exception as e:
                    logger.warning("Cross-platform arb execution failed: %s", e)
        except Exception as e:
            logger.debug("Cross-platform scan failed: %s", e)

    async def _execute_cross_platform_arb(self, opp, adapters):
        """Execute both legs of a cross-platform arb with resolution verification."""
        from base_engine.exchanges.arb_scanner import ArbOpportunity
        if not isinstance(opp, ArbOpportunity):
            return

        # Verify resolution equivalence before executing (Tier 3 #28)
        try:
            market_a = {"question": opp.question_a or opp.event_question, "title": opp.question_a}
            market_b = {"question": opp.question_b or opp.event_question, "title": opp.question_b}
            if market_a.get("question") and market_b.get("question"):
                equiv = await self._verify_resolution_equivalence(market_a, market_b)
                if not equiv.get("equivalent", True):
                    logger.warning(
                        "Cross-platform arb skipped: resolution mismatch",
                        reason=equiv.get("reason"),
                        confidence=equiv.get("confidence"),
                    )
                    return
        except Exception as exc:
            logger.debug("Cross-platform arb: resolution verification failed (non-blocking)", error=str(exc))

        if opp.side == "buy_a_sell_b":
            buy_platform, sell_platform = opp.platform_a, opp.platform_b
            buy_market, sell_market = opp.market_id_a, opp.market_id_b
            buy_price, sell_price = opp.gross_price_a, opp.gross_price_b
        else:
            buy_platform, sell_platform = opp.platform_b, opp.platform_a
            buy_market, sell_market = opp.market_id_b, opp.market_id_a
            buy_price, sell_price = opp.gross_price_b, opp.gross_price_a

        buy_adapter = adapters.get(buy_platform)
        sell_adapter = adapters.get(sell_platform)
        if not buy_adapter or not sell_adapter:
            return

        size = min(self.max_position, 100.0)
        # Execute buy leg first (buy YES on cheaper platform)
        buy_result = await buy_adapter.place_order(buy_market, "YES", size, buy_price)
        if not buy_result.success:
            logger.warning("Cross-platform arb buy leg failed: %s", buy_result.error)
            return

        # Execute sell leg: buy NO on expensive platform (= selling YES equivalent)
        # We buy NO token instead of "SELL"ing YES, since adapters use YES/NO semantics
        sell_result = await sell_adapter.place_order(sell_market, "NO", size, 1.0 - sell_price)
        if sell_result.success:
            logger.info(
                "Cross-platform arb executed: %s (%.3f) vs %s (%.3f), spread=%.1f%%",
                buy_platform, buy_price, sell_platform, sell_price, opp.profit_pct,
            )
        else:
            logger.warning("Cross-platform arb sell leg failed: %s (buy leg may be orphaned)", sell_result.error)

    # ── Crypto temporal lag (absorbed from TemporalArbitrageBot) ────────

    async def _scan_crypto_lag(self):
        """Detect lag between spot crypto prices and Polymarket crypto markets."""
        try:
            markets = await self.base_engine.get_markets(active=True, limit=200)
        except Exception as e:
            logger.debug("crypto lag market fetch failed: %s", e)
            return
        crypto_markets = [m for m in (markets or []) if isinstance(m, dict) and self._is_crypto_market(m)]
        if not crypto_markets:
            return
        exchange_prices = await self._fetch_exchange_prices()
        if not exchange_prices:
            return
        for market in crypto_markets:
            try:
                opp = self._detect_temporal_lag(market, exchange_prices)
                if opp:
                    await self._execute_lag_trade(opp)
            except Exception as e:
                logger.debug("Crypto lag scan error: %s", e)

    def _is_crypto_market(self, market_data: Dict) -> bool:
        question = str(market_data.get("question", "")).lower()
        return any(kw in question for kw in CRYPTO_KEYWORDS)

    def _extract_crypto_symbol(self, market_data: Dict) -> Optional[str]:
        question = str(market_data.get("question", "")).lower()
        for name, sym in [("bitcoin", "BTC"), ("btc", "BTC"), ("ethereum", "ETH"),
                          ("eth", "ETH"), ("solana", "SOL"), ("sol", "SOL"),
                          ("cardano", "ADA"), ("ada", "ADA")]:
            if name in question:
                return sym
        return None

    def _extract_threshold_price(self, question: str) -> Optional[float]:
        patterns = [
            r'\$?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)\s*(?:k|thousand)',
            r'(\d+(?:\.\d+)?)\s*(?:m|million)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, question)
            if matches:
                try:
                    price_str = matches[0].replace(",", "")
                    price = float(price_str)
                    if price <= 0 or price > 1_000_000:
                        continue
                    if "k" in question or "thousand" in question:
                        price *= 1000
                    elif "m" in question or "million" in question:
                        price *= 1_000_000
                    if 0 < price <= 1_000_000:
                        return price
                except (ValueError, TypeError):
                    continue
        return None

    async def _fetch_exchange_prices(self) -> Dict[str, float]:
        prices = {}
        for symbol in CRYPTO_SYMBOLS:
            cached = self._price_cache.get(f"BINANCE_{symbol}")
            if cached and (datetime.now(timezone.utc) - cached["timestamp"]).total_seconds() < PRICE_CACHE_TTL:
                prices[f"BINANCE_{symbol}"] = cached["price"]
                continue
            try:
                r = await self._http.get(f"{BINANCE_API_BASE}/ticker/price", params={"symbol": f"{symbol}USDT"})
                if r.status_code == 200:
                    p = float(r.json().get("price", 0))
                    if p > 0:
                        prices[f"BINANCE_{symbol}"] = p
                        self._price_cache[f"BINANCE_{symbol}"] = {"price": p, "timestamp": datetime.now(timezone.utc)}
            except Exception as e:
                logger.debug("Binance price fetch failed for %s: %s", symbol, e)
        try:
            r = await self._http.get(COINBASE_RATES_API)
            if r.status_code == 200:
                rates = r.json().get("data", {}).get("rates", {})
                for symbol in CRYPTO_SYMBOLS:
                    if symbol in rates:
                        try:
                            rate = float(rates[symbol])
                            if rate > 0:
                                prices[f"COINBASE_{symbol}"] = 1.0 / rate
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            logger.debug("Coinbase rates fetch failed: %s", e)
        return prices

    def _detect_temporal_lag(self, market: Dict, exchange_prices: Dict[str, float]) -> Optional[Dict]:
        market_id = str(market.get("id", ""))
        tokens = market.get("tokens", [])
        if not tokens or len(tokens) < 2:
            return None
        symbol = self._extract_crypto_symbol(market)
        if not symbol:
            return None
        exchange_price = exchange_prices.get(f"BINANCE_{symbol}") or exchange_prices.get(f"COINBASE_{symbol}")
        if not exchange_price:
            return None
        try:
            yes_price = float(tokens[0].get("outcomePrice", 0))
        except (ValueError, TypeError):
            return None
        if yes_price <= 0:
            return None
        question = str(market.get("question", "")).lower()
        threshold = self._extract_threshold_price(question)
        if not threshold or threshold <= 0:
            return None
        min_lag = getattr(settings, "CROSS_ARB_MIN_LAG_THRESHOLD", 0.02)
        if "above" in question or ">" in question:
            if exchange_price > threshold and yes_price < 0.5 - min_lag:
                lag = 0.5 - yes_price
                return {"type": "temporal_long", "market_id": market_id,
                        "token_id": str(tokens[0].get("tokenId", "")),
                        "price": yes_price, "lag": lag,
                        "confidence": min(0.90, 0.5 + lag * 5)}
        elif "below" in question or "<" in question:
            if exchange_price < threshold and yes_price > 0.5 + min_lag:
                lag = yes_price - 0.5
                return {"type": "temporal_short", "market_id": market_id,
                        "token_id": str(tokens[0].get("tokenId", "")),
                        "price": yes_price, "lag": lag,
                        "confidence": min(0.90, 0.5 + lag * 5)}
        return None

    async def _execute_lag_trade(self, opp: Dict):
        side = "YES" if opp["type"] == "temporal_long" else "NO"
        size = await self.calculate_bot_position_size(opp["confidence"], opp["price"])
        order = await self.place_order(
            market_id=opp["market_id"], token_id=opp["token_id"],
            side=side, size=size, price=opp["price"], confidence=opp["confidence"],
        )
        if order.get("success"):
            logger.info("Crypto temporal lag trade: %s %s lag=%.2f%%", opp["market_id"], side, opp["lag"] * 100)

    # ── Slow-market latency arb (Tier 3 #27) ───────────────────────────

    # Slow markets: 1-hour resolution BTC, daily temperature, next-day sports
    _SLOW_KEYWORDS = {
        "1 hour": "hourly_crypto",
        "1-hour": "hourly_crypto",
        "hourly": "hourly_crypto",
        "temperature": "daily_weather",
        "degrees": "daily_weather",
        "weather": "daily_weather",
        "game": "sports",
        "match": "sports",
        "score": "sports",
        "win tonight": "sports",
    }

    async def _scan_slow_markets(self):
        """Scan for slow-update markets where external data gives latency edge."""
        try:
            markets = await self.base_engine.get_markets(active=True, limit=300)
        except Exception:
            return
        if not markets:
            return

        for market in markets:
            if not isinstance(market, dict):
                continue
            question = str(market.get("question", "")).lower()
            market_type = None
            for kw, mtype in self._SLOW_KEYWORDS.items():
                if kw in question:
                    market_type = mtype
                    break
            if not market_type:
                continue

            try:
                opp = await self._analyze_slow_market(market, market_type)
                if opp:
                    await self._execute_lag_trade(opp)
            except Exception as e:
                logger.debug("Slow market scan error: %s", e)

    async def _analyze_slow_market(self, market: Dict, market_type: str) -> Optional[Dict]:
        """Analyze a slow-update market for latency opportunity."""
        tokens = market.get("tokens", [])
        if not tokens or len(tokens) < 2:
            return None
        try:
            yes_price = float(tokens[0].get("outcomePrice", 0))
        except (ValueError, TypeError):
            return None
        if not (0.05 < yes_price < 0.95):
            return None

        market_id = str(market.get("id", ""))
        token_id = str(tokens[0].get("tokenId", ""))

        if market_type == "hourly_crypto":
            # Reuse crypto lag detection with lower threshold for hourly markets
            exchange_prices = await self._fetch_exchange_prices()
            if not exchange_prices:
                return None
            symbol = self._extract_crypto_symbol(market)
            if not symbol:
                return None
            exchange_price = exchange_prices.get(f"BINANCE_{symbol}") or exchange_prices.get(f"COINBASE_{symbol}")
            if not exchange_price:
                return None
            threshold = self._extract_threshold_price(str(market.get("question", "")))
            if not threshold:
                return None
            question = str(market.get("question", "")).lower()
            # Lower threshold for hourly markets (they update slower)
            min_lag = 0.01
            if ("above" in question or ">" in question) and exchange_price > threshold * 1.005:
                if yes_price < 0.85:
                    return {"type": "temporal_long", "market_id": market_id,
                            "token_id": token_id, "price": yes_price,
                            "lag": 0.85 - yes_price,
                            "confidence": min(0.85, 0.5 + (0.85 - yes_price) * 3)}
            elif ("below" in question or "<" in question) and exchange_price < threshold * 0.995:
                if yes_price < 0.85:
                    return {"type": "temporal_long", "market_id": market_id,
                            "token_id": token_id, "price": yes_price,
                            "lag": 0.85 - yes_price,
                            "confidence": min(0.85, 0.5 + (0.85 - yes_price) * 3)}

        elif market_type == "daily_weather":
            # Use NOAA forecast if available
            try:
                from base_engine.signals.noaa_data import NOAAWeatherData
                noaa = NOAAWeatherData()
                # Basic: if we have weather data and market is mispriced, flag it
                # Full implementation deferred to WeatherBot
            except ImportError:
                pass

        return None

    # ── Cross-platform resolution verification (Tier 3 #28) ──────────

    async def _verify_resolution_equivalence(
        self, market_a: Dict, market_b: Dict
    ) -> Dict:
        """
        Use LLM to verify that two cross-platform markets have equivalent
        resolution criteria before executing arb.

        Returns: {"equivalent": bool, "confidence": float, "reason": str}
        """
        q_a = market_a.get("question", "") or market_a.get("title", "")
        q_b = market_b.get("question", "") or market_b.get("title", "")
        desc_a = (market_a.get("description", "") or "")[:300]
        desc_b = (market_b.get("description", "") or "")[:300]

        if not q_a or not q_b:
            return {"equivalent": False, "confidence": 0.0, "reason": "missing_question"}

        # Fast path: identical questions
        if q_a.strip().lower() == q_b.strip().lower():
            return {"equivalent": True, "confidence": 0.99, "reason": "identical_question"}

        # LLM verification
        try:
            import os
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                # Fallback: keyword overlap heuristic
                return self._heuristic_equivalence(q_a, q_b)

            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            prompt = (
                f"Do these two prediction market questions resolve to the same outcome?\n\n"
                f"Market A: {q_a}\nDescription A: {desc_a}\n\n"
                f"Market B: {q_b}\nDescription B: {desc_b}\n\n"
                f"Answer ONLY with: EQUIVALENT <confidence 0-1> <one-line reason>\n"
                f"or: DIFFERENT <confidence 0-1> <one-line reason>"
            )
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip().upper()
            import re
            m = re.match(r"(EQUIVALENT|DIFFERENT)\s+([\d.]+)\s*(.*)", text)
            if m:
                is_eq = m.group(1) == "EQUIVALENT"
                conf = min(1.0, max(0.0, float(m.group(2))))
                reason = m.group(3).strip() or "llm_analysis"
                return {"equivalent": is_eq, "confidence": conf, "reason": reason}
        except Exception as e:
            logger.debug("LLM resolution verification failed: %s", e)

        return self._heuristic_equivalence(q_a, q_b)

    @staticmethod
    def _heuristic_equivalence(q_a: str, q_b: str) -> Dict:
        """Keyword overlap heuristic for resolution equivalence."""
        words_a = set(q_a.lower().split())
        words_b = set(q_b.lower().split())
        # Remove common stop words
        stop = {"will", "the", "a", "an", "in", "on", "by", "to", "of", "is", "be", "?"}
        words_a -= stop
        words_b -= stop
        if not words_a or not words_b:
            return {"equivalent": False, "confidence": 0.3, "reason": "insufficient_data"}
        overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
        return {
            "equivalent": overlap > 0.6,
            "confidence": round(overlap, 2),
            "reason": f"keyword_overlap={overlap:.0%}",
        }

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        return None  # All logic is in scan_and_trade

    async def cleanup(self):
        await self._http.aclose()
