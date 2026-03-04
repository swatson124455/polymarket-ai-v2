import asyncio
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from structlog import get_logger
from bots.base_bot import BaseBot, get_end_date_from_dict
from base_engine.base_engine import BaseEngine
from base_engine.coordination.arbitrage_coordinator import ArbitrageTransactionCoordinator
from config.settings import settings

logger = get_logger()


def _arb_setting(name: str, default: float):
    return getattr(settings, name, default)


class ArbitrageBot(BaseBot):
    def __init__(self, base_engine: BaseEngine):
        super().__init__("ArbitrageBot", base_engine)
        self.min_profit_threshold = _arb_setting("ARB_MIN_PROFIT_THRESHOLD", 0.01)
        self.max_profit_threshold = _arb_setting("ARB_MAX_PROFIT_THRESHOLD", 0.05)
        self.default_order_size = _arb_setting("ARB_DEFAULT_ORDER_SIZE", 100.0)
        self.max_markets_per_scan = int(_arb_setting("ARB_MAX_MARKETS_PER_SCAN", 500))
        self._arb_coordinator: Optional[ArbitrageTransactionCoordinator] = None

    def _get_arb_coordinator(self) -> ArbitrageTransactionCoordinator:
        if self._arb_coordinator is None:
            tc = getattr(self.base_engine, "trade_coordinator", None)
            self._arb_coordinator = ArbitrageTransactionCoordinator(
                tc, self.place_order, self.bot_name
            )
        return self._arb_coordinator
    
    def _is_negrisk_market(self, market_data: Dict) -> bool:
        if not market_data:
            return False
        question = str(market_data.get("question", "")).lower()
        negrisk_keywords = ["negrisk", "negative risk", "no risk", "risk-free"]
        return any(keyword in question for keyword in negrisk_keywords)

    async def _has_recent_price_movement(
        self,
        market_id: str,
        token_ids: List[str],
        limit: Optional[int] = None,
        min_std: Optional[float] = None,
        price_history: Optional[List[Dict]] = None,
    ) -> bool:
        """Check if market has recent price movement (not stale). Used when ARBITRAGE_REQUIRE_PRICE_MOVEMENT=True."""
        if limit is None:
            limit = int(_arb_setting("ARB_PRICE_MOVEMENT_LIMIT", 20))
        if min_std is None:
            min_std = _arb_setting("ARB_PRICE_MOVEMENT_MIN_STD", 0.01)
        if price_history is not None and len(price_history) >= 3:
            totals = []
            seen_ts = set()
            for p in reversed(price_history[:limit]):
                ts = p.get("timestamp")
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                pr = float(p.get("price", 0))
                totals.append(pr)
            if len(totals) < 3:
                return False
            import statistics
            return (statistics.stdev(totals) if len(totals) >= 2 else 0.0) >= min_std
        db = getattr(self.base_engine, "db", None)
        if not db or not hasattr(db, "get_recent_prices_for_market"):
            return True
        try:
            prices = await db.get_recent_prices_for_market(market_id, token_ids, limit=limit)
            if len(prices) < 3:
                return False
            totals = []
            seen_ts = set()
            for p in reversed(prices):
                ts = p.get("timestamp")
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                pr = float(p.get("price", 0))
                totals.append(pr)
            if len(totals) < 3:
                return False
            import statistics
            std = statistics.stdev(totals) if len(totals) >= 2 else 0.0
            return std >= min_std
        except Exception as e:
            logger.debug("Price movement check failed for %s: %s", market_id, e)
            return False
    
    async def _get_cached_digest(self):
        for attempt in range(3):
            try:
                digest = await self.base_engine.get_markets_with_price_history(
                    active=True,
                    limit=min(self.max_markets_per_scan, settings.SCAN_MARKET_LIMIT),
                    price_limit_per_market=50,
                )
                return digest or []
            except Exception as e:
                logger.warning("get_markets_with_price_history attempt %s failed: %s", attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return []

    def _dedup_key(self, opportunity: Dict) -> str:
        otype = opportunity.get("type", "")
        mid = opportunity.get("market_id") or opportunity.get("market1_id", "") or ""
        prices = json.dumps(
            [
                opportunity.get("yes_price"),
                opportunity.get("no_price"),
                opportunity.get("total_price"),
            ],
            sort_keys=True,
        )
        return hashlib.sha256(f"{otype}:{mid}:{prices}".encode()).hexdigest()[:16]

    async def _was_executed_recently(self, opportunity: Dict) -> bool:
        cache = getattr(self.base_engine, "cache", None)
        if not cache or not getattr(cache, "redis", None):
            return False
        key = "arb:executed:" + self._dedup_key(opportunity)
        try:
            return (await cache.redis.get(key)) is not None
        except Exception:
            return False

    async def _mark_executed(self, opportunity: Dict) -> None:
        cache = getattr(self.base_engine, "cache", None)
        if not cache or not getattr(cache, "redis", None):
            return
        ttl = int(_arb_setting("ARB_OPPORTUNITY_DEDUP_TTL_SECONDS", 60))
        key = "arb:executed:" + self._dedup_key(opportunity)
        try:
            await cache.redis.set(key, "1", ex=ttl)
        except Exception as e:
            logger.debug("Arb dedup set failed: %s", e)

    async def _resolve_market_id(self, raw_id: str) -> str:
        """Resolve market_id to condition_id. Cached forever (IDs don't change)."""
        if not raw_id:
            return raw_id
        if not hasattr(self, "_market_id_cache"):
            self._market_id_cache: dict = {}
        cached = self._market_id_cache.get(raw_id)
        if cached is not None:
            return cached
        db = getattr(self.base_engine, "db", None)
        try:
            from base_engine.data.id_resolver import resolve_market_id
            resolved = await resolve_market_id(db, raw_id)
            result = resolved or raw_id
            self._market_id_cache[raw_id] = result
            return result
        except Exception as e:
            logger.debug("id_resolver failed for %s: %s", raw_id, e)
            return raw_id

    async def on_price_update(self, event: dict) -> None:
        """React to real-time WS price updates with ms-latency: no REST/DB calls."""
        await super().on_price_update(event)
        if not self.running:
            return
        # C1 FIX: Check kill switch in WS reactive path — the scan loop checks it, but
        # WS handlers fire between scans and bypass the kill switch entirely. Without this
        # check, the bot continues trading even after the daily loss limit triggers a halt.
        _mlks = getattr(self.base_engine, "multi_kill_switch", None)
        if _mlks is not None:
            try:
                if not await _mlks.should_trade(self.bot_name):
                    return
            except Exception:
                pass
        elif getattr(self.base_engine, "kill_switch", None) is not None:
            try:
                if await self.base_engine.kill_switch.is_engaged():
                    return
            except Exception:
                pass
        # Cold-start guard
        if not self.base_engine._market_index_populated:
            return
        market_id = event.get("market_id", "")
        token_id = event.get("token_id", "")
        new_price = float(event.get("price", 0))
        if not market_id or new_price <= 0:
            return
        # Track per-token prices from WS for binary arb check
        if not hasattr(self, "_ws_token_prices"):
            self._ws_token_prices: dict = {}
        # Phase 6.2: track update timestamps so we can verify both YES+NO are fresh
        if not hasattr(self, "_ws_token_timestamps"):
            self._ws_token_timestamps: dict = {}
        if token_id:
            self._ws_token_prices[token_id] = new_price
            self._ws_token_timestamps[token_id] = time.monotonic()
        # Threshold / extreme price check
        threshold = getattr(settings, "ARB_WS_PRICE_CHANGE_PCT", 0.008)
        if not hasattr(self, "_ws_prev_prices"):
            self._ws_prev_prices: dict = {}
        old_price = self._ws_prev_prices.get(market_id)
        self._ws_prev_prices[market_id] = new_price
        near_extreme = new_price < 0.05 or new_price > 0.95
        significant_move = old_price is not None and abs(new_price - old_price) / max(old_price, 0.01) >= threshold
        if not near_extreme and not significant_move:
            return
        # Cooldown
        now = time.monotonic()
        if not hasattr(self, "_ws_scan_cooldowns"):
            self._ws_scan_cooldowns: dict = {}
        cooldown = getattr(settings, "ARB_WS_COOLDOWN_SECONDS", 2)
        if now - self._ws_scan_cooldowns.get(market_id, 0) < cooldown:
            return
        self._ws_scan_cooldowns[market_id] = now
        try:
            # FAST PATH: O(1) market lookup from in-memory index
            market_data = self.base_engine.get_market_from_index(str(market_id))
            if not market_data:
                return
            # FAST PATH: Binary arb check using WS price cache — zero DB calls
            tokens = market_data.get("tokens") or []
            if len(tokens) < 2:
                return
            yes_token = next((t for t in tokens if isinstance(t, dict) and t.get("outcome") == "YES"), None)
            no_token = next((t for t in tokens if isinstance(t, dict) and t.get("outcome") == "NO"), None)
            if not yes_token or not no_token:
                return
            yes_tid = yes_token.get("tokenId", "")
            no_tid = no_token.get("tokenId", "")
            # Use live WS prices if available, fall back to market_data prices
            yes_price = self._ws_token_prices.get(yes_tid) or float(yes_token.get("outcomePrice", 0) or 0)
            no_price = self._ws_token_prices.get(no_tid) or float(no_token.get("outcomePrice", 0) or 0)
            if yes_price <= 0 or no_price <= 0:
                return
            # Phase 6.2: only fire arb when BOTH token prices are from fresh WS ticks.
            # Stale price + fresh price = false arb. Max age gap = ARB_MAX_PRICE_AGE_SECONDS.
            _max_age = getattr(settings, "ARB_MAX_PRICE_AGE_SECONDS", 5)
            _yes_ts = self._ws_token_timestamps.get(yes_tid, 0) if hasattr(self, "_ws_token_timestamps") else 0
            _no_ts = self._ws_token_timestamps.get(no_tid, 0) if hasattr(self, "_ws_token_timestamps") else 0
            if _yes_ts > 0 and _no_ts > 0 and abs(_yes_ts - _no_ts) > _max_age:
                return  # One token price is stale — skip to avoid false arb
            total = yes_price + no_price
            # Quick arb screen: no arb if sum is near 1.0
            if 1.0 - self.min_profit_threshold <= total <= 1.0 + self.min_profit_threshold:
                return
            # Confirmed arb potential — patch market_data with live WS prices
            updated_market = dict(market_data)
            updated_tokens = []
            for t in tokens:
                t_copy = dict(t)
                tid = t_copy.get("tokenId", "")
                if tid in self._ws_token_prices:
                    t_copy["outcomePrice"] = str(self._ws_token_prices[tid])
                updated_tokens.append(t_copy)
            updated_market["tokens"] = updated_tokens
            opp = await self.analyze_opportunity(updated_market, price_history=None)
            if opp:
                # Position guard: don't stack positions on the same market from multiple WS ticks
                og = getattr(self.base_engine, "order_gateway", None)
                if og is not None and og.has_open_position(self.bot_name, str(market_id)):
                    return
                logger.info("ArbitrageBot WS reactive arb on %s (sum=%.4f)", market_id, total)
                await self._execute_arbitrage(opp)
        except Exception as e:
            logger.debug("ArbitrageBot WS reactive check failed: %s", e)

    async def scan_and_trade(self):
        # Early exit if Polymarket API circuit breaker is OPEN — prevents 120s DB session hold
        _cb = getattr(getattr(self.base_engine, "client", None), "circuit_breaker", None)
        if _cb is not None and _cb.state == "OPEN":
            if _cb.last_failure_time is not None and (time.time() - _cb.last_failure_time <= _cb.timeout):
                logger.debug("ArbitrageBot: circuit breaker OPEN — skipping scan")
                return
        try:
            digest = await self._get_cached_digest()
            markets = [d["market"] for d in digest if d.get("market")]
            self.base_engine.update_market_index(markets)
            markets = self.base_engine.filter_markets_for_trading(markets)
            digest_by_id = {str(d["market"].get("id")): d for d in digest if d.get("market") and d["market"].get("id")}
        except Exception as e:
            logger.error("Failed to fetch markets: %s", e, exc_info=True)
            return

        if not markets or not isinstance(markets, list):
            logger.warning("No markets returned or invalid response")
            return

        # Parallel opportunity analysis — Semaphore limits concurrent DB sessions.
        _opp_sem = asyncio.Semaphore(2)

        async def _analyze_one(market: dict):
            if not market or not isinstance(market, dict):
                return None
            async with _opp_sem:
                try:
                    mid = market.get("id")
                    d = digest_by_id.get(str(mid)) if mid is not None else None
                    price_history = (d.get("price_history") or []) if d else []
                    return await self.analyze_opportunity(market, price_history=price_history)
                except Exception as e:
                    logger.warning("Error analyzing market %s: %s", market.get("id", "unknown"), e)
                    return None

        _results = await asyncio.gather(*[_analyze_one(m) for m in markets], return_exceptions=True)
        opportunities = [o for o in _results if isinstance(o, dict) and o]

        if not opportunities:
            # P5a: Diagnostic — log why no opportunities (was DEBUG, invisible)
            logger.info(
                "ArbitrageBot scan: 0 opportunities from %d markets",
                len(markets),
            )
            return

        # P5a: Diagnostic summary
        logger.info(
            "ArbitrageBot scan: %d opportunities from %d markets (top margin=%.4f)",
            len(opportunities), len(markets),
            opportunities[0].get("profit_margin", 0) if opportunities else 0,
        )
        opportunities.sort(key=lambda x: x.get("profit_margin", 0), reverse=True)
        max_opps = int(_arb_setting("ARB_MAX_OPPORTUNITIES_PER_SCAN", 10))
        delay = _arb_setting("ARB_ORDER_DELAY_SECONDS", 0.5)
        for opportunity in opportunities[:max_opps]:
            try:
                if await self._was_executed_recently(opportunity):
                    continue
                await self._execute_arbitrage(opportunity)
                await self._mark_executed(opportunity)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error("Error executing arbitrage: %s", e, exc_info=True)
                continue

        # Sub-scan timeouts: each sub-scan gets at most 30s so the combined scan
        # stays well inside the 120s BOT_SCAN_TIMEOUT_SECONDS budget.
        # DB semaphore exhaustion can cause correlation lookups to block for 30s each;
        # without these guards the scan reliably exceeds the 120s hard limit.
        try:
            await asyncio.wait_for(self._scan_cross_market_arbitrage(markets), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("cross_market_arb sub-scan timed out after 30s — skipping")
        except Exception as e:
            logger.warning("Error in cross-market scan: %s", e, exc_info=True)

        # Bond strategy: buy near-certain outcomes approaching resolution
        try:
            await asyncio.wait_for(self._scan_bond_opportunities(markets), timeout=20.0)
        except asyncio.TimeoutError:
            logger.warning("bond_opportunities sub-scan timed out after 20s — skipping")
        except Exception as e:
            logger.warning("Error in bond strategy scan: %s", e, exc_info=True)

        # NegRisk multi-outcome arbitrage
        try:
            await asyncio.wait_for(self._scan_negrisk_arbitrage(markets), timeout=20.0)
        except asyncio.TimeoutError:
            logger.warning("negrisk_arb sub-scan timed out after 20s — skipping")
        except Exception as e:
            logger.warning("Error in NegRisk arb scan: %s", e, exc_info=True)
    
    async def analyze_opportunity(
        self, market_data: Dict, price_history: Optional[List[Dict]] = None
    ) -> Optional[Dict]:
        if not market_data or not isinstance(market_data, dict):
            return None

        market_id = str(market_data.get("id", "")).strip()
        if not market_id:
            return None
        
        tokens = market_data.get("tokens", [])
        if not tokens or not isinstance(tokens, list):
            return None
        
        if len(tokens) < 2:
            return await self._analyze_bundle_arbitrage(market_data)
        
        yes_token = next((t for t in tokens if isinstance(t, dict) and t.get("outcome") == "YES"), None)
        no_token = next((t for t in tokens if isinstance(t, dict) and t.get("outcome") == "NO"), None)
        
        if not yes_token or not no_token:
            return await self._analyze_bundle_arbitrage(market_data)
        
        yes_token_id = yes_token.get("tokenId")
        no_token_id = no_token.get("tokenId")
        
        if not yes_token_id or not no_token_id:
            logger.warning(f"Missing tokenId for market {market_id}")
            return None
        
        try:
            yes_price = float(yes_token.get("outcomePrice", 0))
            no_price = float(no_token.get("outcomePrice", 0))
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid price for market {market_id}: {str(e)}")
            return None
        
        if yes_price <= 0 or no_price <= 0:
            return None
        
        total_price = yes_price + no_price
        is_negrisk = self._is_negrisk_market(market_data)
        
        if total_price < 1.0 - self.min_profit_threshold:
            profit_margin = 1.0 - total_price
            # Cost model: skip if profit_margin below min edge for profitability (fees)
            min_edge = getattr(settings, "ARB_MIN_NET_EDGE", 0.005)
            try:
                from base_engine.risk.transaction_cost import TransactionCostModel
                order_value = self.default_order_size * total_price  # capital at risk for both legs
                min_edge = max(min_edge, TransactionCostModel().min_edge_for_profitability(order_value, 0))
            except Exception as e:
                logger.debug("transaction cost model failed for long arb: %s", e)
            if profit_margin < min_edge:
                logger.debug("Arb profit_margin %.2f%% below min_edge %.2f%%", profit_margin * 100, min_edge * 100)
                return None
            confidence_boost = _arb_setting("ARB_NEGRISK_CONFIDENCE_BOOST", 0.1) if is_negrisk else 0.0
            opp = {
                "type": "long_arbitrage",
                "market_id": market_id,
                "yes_token_id": str(yes_token_id),
                "no_token_id": str(no_token_id),
                "yes_price": yes_price,
                "no_price": no_price,
                "total_price": total_price,
                "profit_margin": profit_margin,
                "confidence": min(0.95, 0.5 + profit_margin * 2 + confidence_boost),
                "is_negrisk": is_negrisk,
                "price_fetched_at": time.time(),
            }
            if getattr(settings, "ARBITRAGE_REQUIRE_PRICE_MOVEMENT", False):
                if not await self._has_recent_price_movement(market_id, [yes_token_id, no_token_id]):
                    return None
            # Tier 2 #18: VPIN toxicity — reject arb if informed traders active
            opp = await self._apply_vpin_filter(opp, str(yes_token_id))
            return opp

        elif total_price > 1.0 + self.min_profit_threshold:
            profit_margin = total_price - 1.0
            # Cost model: skip if profit_margin below min edge for profitability (fees)
            min_edge = getattr(settings, "ARB_MIN_NET_EDGE", 0.005)
            try:
                from base_engine.risk.transaction_cost import TransactionCostModel
                order_value = self.default_order_size * total_price
                min_edge = max(min_edge, TransactionCostModel().min_edge_for_profitability(order_value, 0))
            except Exception as e:
                logger.debug("transaction cost model failed for short arb: %s", e)
            if profit_margin < min_edge:
                logger.debug("Arb profit_margin %.2f%% below min_edge %.2f%%", profit_margin * 100, min_edge * 100)
                return None
            confidence_boost = _arb_setting("ARB_NEGRISK_CONFIDENCE_BOOST", 0.1) if is_negrisk else 0.0
            opp = {
                "type": "short_arbitrage",
                "market_id": market_id,
                "yes_token_id": str(yes_token_id),
                "no_token_id": str(no_token_id),
                "yes_price": yes_price,
                "no_price": no_price,
                "total_price": total_price,
                "profit_margin": profit_margin,
                "confidence": min(0.95, 0.5 + profit_margin * 2 + confidence_boost),
                "is_negrisk": is_negrisk,
                "price_fetched_at": time.time(),
            }
            if getattr(settings, "ARBITRAGE_REQUIRE_PRICE_MOVEMENT", False):
                if not await self._has_recent_price_movement(
                    market_id, [yes_token_id, no_token_id], price_history=price_history
                ):
                    return None
            # Tier 2 #18: VPIN toxicity — reject arb if informed traders active
            opp = await self._apply_vpin_filter(opp, str(yes_token_id))
            return opp

        return None
    
    async def _apply_vpin_filter(self, opp: Optional[Dict], token_id: str) -> Optional[Dict]:
        """Tier 2 #18: Reject arb opportunity if VPIN indicates toxic (informed) flow."""
        if opp is None:
            return None
        try:
            tfa = getattr(self.base_engine, "trade_flow_analyzer", None)
            if not tfa:
                return opp
            vpin_result = await tfa.get_vpin(token_id, minutes=60)
            vpin = vpin_result.get("vpin", 0.0)
            if vpin > 0.7:
                logger.info(
                    "ArbitrageBot: VPIN toxic (%.2f), rejecting %s on %s",
                    vpin, opp.get("type"), opp.get("market_id"),
                )
                return None
            if vpin > 0.5:
                # Elevated but not toxic — reduce confidence
                opp["confidence"] = opp.get("confidence", 0.5) * 0.9
        except Exception as e:
            logger.debug("ArbitrageBot VPIN filter failed: %s", e)
        return opp

    async def _analyze_bundle_arbitrage(self, market_data: Dict) -> Optional[Dict]:
        if not market_data:
            return None
        
        tokens = market_data.get("tokens", [])
        if not tokens or not isinstance(tokens, list) or len(tokens) < 3:
            return None
        
        total_price = 0.0
        token_ids = []
        prices = []
        
        for token in tokens:
            if not isinstance(token, dict):
                continue
            
            token_id = token.get("tokenId")
            if not token_id:
                return None
            
            try:
                price = float(token.get("outcomePrice", 0))
            except (ValueError, TypeError):
                return None
            
            if price <= 0:
                return None
            
            total_price += price
            token_ids.append(str(token_id))
            prices.append(price)
        
        if total_price < 1.0 - self.min_profit_threshold:
            profit_margin = 1.0 - total_price
            # Cost model: skip if profit_margin below min edge for profitability (fees)
            min_edge = getattr(settings, "ARB_MIN_NET_EDGE", 0.005)
            try:
                from base_engine.risk.transaction_cost import TransactionCostModel
                # Bundle arb buys N legs — total capital at risk is size * total_price
                order_value = self.default_order_size * total_price
                min_edge = max(min_edge, TransactionCostModel().min_edge_for_profitability(order_value, 0))
            except Exception as e:
                logger.debug("transaction cost model failed for bundle arb: %s", e)
            if profit_margin < min_edge:
                logger.debug("Bundle arb profit_margin %.2f%% below min_edge %.2f%%", profit_margin * 100, min_edge * 100)
                return None
            return {
                "type": "bundle_arbitrage",
                "market_id": str(market_data.get("id", "")),
                "token_ids": token_ids,
                "prices": prices,
                "total_price": total_price,
                "profit_margin": profit_margin,
                "confidence": min(0.95, _arb_setting("ARB_DEFAULT_CONFIDENCE", 0.5) + profit_margin * 2)
            }
        return None

    async def _scan_cross_market_arbitrage(self, markets: List[Dict]):
        if not markets or len(markets) < 2:
            return

        max_pairs = int(_arb_setting("ARB_CROSS_MARKET_MAX_PAIRS", 100))
        # Default 5 (was 20): each find_correlated_markets() call uses one DB session;
        # 20 sequential calls × 30s semaphore timeout = 600s worst-case. 5 caps that at 150s,
        # and the 30s sub-scan timeout above prevents even that from blowing the scan budget.
        corr_limit = int(_arb_setting("ARB_CORRELATION_MARKET_LIMIT", 5))
        corr_min = _arb_setting("ARB_CORRELATION_MIN", 0.7)
        corr_lookback = int(_arb_setting("ARB_CORRELATION_LOOKBACK_DAYS", 30))
        corr_strat = getattr(self.base_engine, "correlation_strategy", None)

        # Pre-compute candidate pairs: category index + correlation lookup
        # This avoids O(n^2) _are_related_markets checks on every pair
        candidate_pairs: set = set()

        # 1. Build category index: group markets by category for O(n) pair generation
        from collections import defaultdict as _defaultdict
        cat_index: Dict[str, List[int]] = _defaultdict(list)
        market_by_id: Dict[str, int] = {}
        for idx, m in enumerate(markets):
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id", ""))
            if not mid:
                continue
            market_by_id[mid] = idx
            cat = m.get("category") or ""
            if cat:
                cat_index[cat].append(idx)

        # Generate pairs from same-category markets
        for cat, indices in cat_index.items():
            if len(indices) < 2:
                continue
            for ii in range(len(indices) - 1):
                for jj in range(ii + 1, len(indices)):
                    mid1 = str(markets[indices[ii]].get("id", ""))
                    mid2 = str(markets[indices[jj]].get("id", ""))
                    candidate_pairs.add(tuple(sorted([mid1, mid2])))
                    if len(candidate_pairs) >= max_pairs * 2:
                        break
                if len(candidate_pairs) >= max_pairs * 2:
                    break

        # 2. Add correlation-based pairs
        if corr_strat is not None:
            try:
                for m in markets[:corr_limit]:
                    mid = str(m.get("id", ""))
                    if not mid:
                        continue
                    correlated = await corr_strat.find_correlated_markets(mid, min_correlation=corr_min, lookback_days=corr_lookback)
                    for c in correlated:
                        cid = c.get("market_id", "")
                        if cid and cid in market_by_id:
                            candidate_pairs.add(tuple(sorted([mid, cid])))
            except Exception as e:
                logger.debug("CorrelationStrategy lookup failed: %s", e)

        # 3. Scan only candidate pairs (O(k) where k = related pairs, not O(n^2))
        pairs_checked = 0
        for mid1, mid2 in candidate_pairs:
            if pairs_checked >= max_pairs:
                break
            idx1 = market_by_id.get(mid1)
            idx2 = market_by_id.get(mid2)
            if idx1 is None or idx2 is None:
                continue
            try:
                opportunity = await self._detect_cross_market_arbitrage(markets[idx1], markets[idx2])
                if opportunity:
                    await self._execute_cross_market_arbitrage(opportunity)
                pairs_checked += 1
            except Exception as e:
                logger.warning("Error in cross-market scan pair: %s", e)
                continue
    
    def _are_related_markets(self, market1: Dict, market2: Dict) -> bool:
        if not market1 or not market2:
            return False
        
        if market1.get("category") == market2.get("category") and market1.get("category"):
            return True
        
        q1 = str(market1.get("question", "")).lower()
        q2 = str(market2.get("question", "")).lower()
        
        if not q1 or not q2:
            return False
        
        words1 = set(q1.split())
        words2 = set(q2.split())
        
        common_words = words1.intersection(words2)
        if len(common_words) >= 2:
            return True
        
        return False
    
    async def _detect_cross_market_arbitrage(self, market1: Dict, market2: Dict) -> Optional[Dict]:
        # BUG FIX: Added comprehensive validation for all price values
        # Root cause: Code assumed all tokens have valid prices, but API responses might have
        # missing or null price fields, leading to potential division by zero or type errors
        # Impact: Bot crashes when encountering invalid price data, arbitrage detection fails
        # Fix: Add comprehensive validation for all price values before calculations
        if not market1 or not market2:
            return None
        
        tokens1 = market1.get("tokens", [])
        tokens2 = market2.get("tokens", [])
        
        if not tokens1 or not tokens2 or len(tokens1) < 2 or len(tokens2) < 2:
            return None
        
        yes1 = next((t for t in tokens1 if isinstance(t, dict) and t.get("outcome") == "YES"), None)
        no1 = next((t for t in tokens1 if isinstance(t, dict) and t.get("outcome") == "NO"), None)
        yes2 = next((t for t in tokens2 if isinstance(t, dict) and t.get("outcome") == "YES"), None)
        no2 = next((t for t in tokens2 if isinstance(t, dict) and t.get("outcome") == "NO"), None)
        
        if not all([yes1, no1, yes2, no2]):
            return None
        
        try:
            # Validate prices exist and are valid numbers
            price1_yes_raw = yes1.get("outcomePrice")
            price1_no_raw = no1.get("outcomePrice")
            price2_yes_raw = yes2.get("outcomePrice")
            price2_no_raw = no2.get("outcomePrice")
            
            # Check for None or invalid values before conversion
            if any(p is None for p in [price1_yes_raw, price1_no_raw, price2_yes_raw, price2_no_raw]):
                logger.debug("Missing price data in cross-market arbitrage detection")
                return None
            
            price1_yes = float(price1_yes_raw)
            price1_no = float(price1_no_raw)
            price2_yes = float(price2_yes_raw)
            price2_no = float(price2_no_raw)
            
            # Validate price ranges (0 < price <= 1 for Polymarket)
            if any(p <= 0 or p > 1 for p in [price1_yes, price1_no, price2_yes, price2_no]):
                logger.debug("Invalid price range in cross-market arbitrage detection")
                return None
            
            # Check for NaN or Infinity
            import math
            if any(math.isnan(p) or math.isinf(p) for p in [price1_yes, price1_no, price2_yes, price2_no]):
                logger.debug("NaN/Infinity prices in cross-market arbitrage detection")
                return None
        except (ValueError, TypeError) as e:
            logger.debug(f"Price conversion error in cross-market arbitrage: {str(e)}")
            return None
        
        yes1_token_id = yes1.get("tokenId")
        no1_token_id = no1.get("tokenId")
        yes2_token_id = yes2.get("tokenId")
        no2_token_id = no2.get("tokenId")
        
        if not all([yes1_token_id, no1_token_id, yes2_token_id, no2_token_id]):
            return None
        
        combined_yes = price1_yes + price2_yes
        combined_no = price1_no + price2_no
        
        if combined_yes < 1.0 - self.min_profit_threshold:
            return {
                "type": "cross_market_long",
                "market1_id": str(market1.get("id", "")),
                "market2_id": str(market2.get("id", "")),
                "market1_yes_token": str(yes1_token_id),
                "market2_yes_token": str(yes2_token_id),
                "market1_yes_price": price1_yes,
                "market2_yes_price": price2_yes,
                "combined_price": combined_yes,
                "profit_margin": 1.0 - combined_yes,
                "confidence": min(0.90, _arb_setting("ARB_DEFAULT_CONFIDENCE_CROSS", 0.4) + (1.0 - combined_yes) * 2)
            }
        elif combined_no < 1.0 - self.min_profit_threshold:
            return {
                "type": "cross_market_long",
                "market1_id": str(market1.get("id", "")),
                "market2_id": str(market2.get("id", "")),
                "market1_no_token": str(no1_token_id),
                "market2_no_token": str(no2_token_id),
                "market1_no_price": price1_no,
                "market2_no_price": price2_no,
                "combined_price": combined_no,
                "profit_margin": 1.0 - combined_no,
                "confidence": min(0.90, _arb_setting("ARB_DEFAULT_CONFIDENCE_CROSS", 0.4) + (1.0 - combined_no) * 2)
            }
        
        return None
    
    async def _execute_arbitrage(self, opportunity: Dict):
        if not opportunity or not isinstance(opportunity, dict):
            logger.warning("Invalid opportunity dict")
            return

        # T5 FIX: Validate price freshness before execution.
        # Scan loop processes markets sequentially — by market #10, prices could be 30s+ old.
        price_fetched_at = opportunity.get("price_fetched_at")
        if price_fetched_at is not None:
            max_age = _arb_setting("ARB_MAX_PRICE_AGE_SECONDS", 5.0)
            age = time.time() - price_fetched_at
            if age > max_age:
                logger.debug(
                    "Arb opportunity stale (%.1fs > %.1fs), skipping %s",
                    age, max_age, opportunity.get("market_id"),
                )
                return

        opp_type = opportunity.get("type")

        if opp_type == "long_arbitrage":
            await self._execute_long_arbitrage(opportunity)
        elif opp_type == "short_arbitrage":
            await self._execute_short_arbitrage(opportunity)
        elif opp_type == "bundle_arbitrage":
            await self._execute_bundle_arbitrage(opportunity)
        else:
            logger.warning(f"Unknown arbitrage type: {opp_type}")
    
    async def _execute_long_arbitrage(self, opportunity: Dict):
        import math
        try:
            market_id = opportunity.get("market_id")
            yes_token_id = opportunity.get("yes_token_id")
            no_token_id = opportunity.get("no_token_id")
            yes_price = opportunity.get("yes_price")
            no_price = opportunity.get("no_price")
            confidence = opportunity.get("confidence", _arb_setting("ARB_DEFAULT_CONFIDENCE", 0.5))
            is_negrisk = opportunity.get("is_negrisk", False)
            if not all([market_id, yes_token_id, no_token_id, yes_price, no_price]):
                logger.warning("Missing required fields for long arbitrage")
                return
            try:
                yes_price_float = float(yes_price)
                no_price_float = float(no_price)
            except (ValueError, TypeError) as e:
                logger.warning("Invalid price format for market %s: %s", market_id, e)
                return
            if yes_price_float <= 0 or yes_price_float > 1 or no_price_float <= 0 or no_price_float > 1:
                logger.warning("Invalid price range for market %s", market_id)
                return
            if math.isnan(yes_price_float) or math.isinf(yes_price_float) or math.isnan(no_price_float) or math.isinf(no_price_float):
                logger.warning("NaN/Infinity prices for market %s", market_id)
                return
            market_id = await self._resolve_market_id(str(market_id))
            # Kelly-based sizing: use risk_manager for proper per-bot fractional Kelly
            size_multiplier = _arb_setting("ARB_NEGRISK_SIZE_MULTIPLIER", 1.2) if is_negrisk else 1.0
            try:
                _avg_price = (yes_price_float + no_price_float) / 2.0
                size = await self.calculate_bot_position_size(float(confidence), _avg_price)
                size = max(1.0, size * size_multiplier)
                size = min(size, self.default_order_size * 2.0)  # cap at 2× default for safety
            except Exception:
                size = self.default_order_size * size_multiplier
            risk_mgr = getattr(self.base_engine, "risk_manager", None)
            if risk_mgr and hasattr(risk_mgr, "check_arbitrage_risk_limits"):
                legs = [
                    {"market_id": market_id, "token_id": yes_token_id, "side": "BUY", "size": size, "price": yes_price_float},
                    {"market_id": market_id, "token_id": no_token_id, "side": "BUY", "size": size, "price": no_price_float},
                ]
                arb_risk = await risk_mgr.check_arbitrage_risk_limits(self.bot_name, legs)
                if not arb_risk.get("allowed", True):
                    logger.warning("Arb risk check failed: %s", arb_risk.get("reasons"))
                    return
            coord = self._get_arb_coordinator()
            success, err = await coord.execute_long_arbitrage(
                market_id, yes_token_id, no_token_id, yes_price_float, no_price_float,
                size, float(confidence), self.min_profit_threshold,
                price_fetched_at=opportunity.get("price_fetched_at"),
            )
            if success:
                logger.info(
                    "Long arbitrage executed: %s profit margin %s",
                    market_id, opportunity.get("profit_margin", 0),
                    market=market_id, profit_margin=opportunity.get("profit_margin", 0), is_negrisk=is_negrisk
                )
            elif err:
                logger.warning("Long arbitrage failed for %s: %s", market_id, err)
        except Exception as e:
            logger.error("Error executing long arbitrage: %s", e, exc_info=True)
    
    async def _execute_short_arbitrage(self, opportunity: Dict):
        import math
        try:
            market_id = opportunity.get("market_id")
            yes_token_id = opportunity.get("yes_token_id")
            no_token_id = opportunity.get("no_token_id")
            yes_price = opportunity.get("yes_price")
            no_price = opportunity.get("no_price")
            confidence = opportunity.get("confidence", _arb_setting("ARB_DEFAULT_CONFIDENCE", 0.5))
            is_negrisk = opportunity.get("is_negrisk", False)
            if not all([market_id, yes_token_id, no_token_id, yes_price, no_price]):
                logger.warning("Missing required fields for short arbitrage")
                return
            try:
                yes_price_float = float(yes_price)
                no_price_float = float(no_price)
            except (ValueError, TypeError) as e:
                logger.warning("Invalid price format for market %s: %s", market_id, e)
                return
            if yes_price_float <= 0 or yes_price_float > 1 or no_price_float <= 0 or no_price_float > 1:
                logger.warning("Invalid price range for market %s", market_id)
                return
            if math.isnan(yes_price_float) or math.isinf(yes_price_float) or math.isnan(no_price_float) or math.isinf(no_price_float):
                logger.warning("NaN/Infinity prices for market %s", market_id)
                return
            market_id = await self._resolve_market_id(str(market_id))
            # Kelly-based sizing: use risk_manager for proper per-bot fractional Kelly
            size_multiplier = _arb_setting("ARB_NEGRISK_SIZE_MULTIPLIER", 1.2) if is_negrisk else 1.0
            try:
                _avg_price = (yes_price_float + no_price_float) / 2.0
                size = await self.calculate_bot_position_size(float(confidence), _avg_price)
                size = max(1.0, size * size_multiplier)
                size = min(size, self.default_order_size * 2.0)  # cap at 2× default for safety
            except Exception:
                size = self.default_order_size * size_multiplier
            risk_mgr = getattr(self.base_engine, "risk_manager", None)
            if risk_mgr and hasattr(risk_mgr, "check_arbitrage_risk_limits"):
                legs = [
                    {"market_id": market_id, "token_id": yes_token_id, "side": "SELL", "size": size, "price": yes_price_float},
                    {"market_id": market_id, "token_id": no_token_id, "side": "SELL", "size": size, "price": no_price_float},
                ]
                arb_risk = await risk_mgr.check_arbitrage_risk_limits(self.bot_name, legs)
                if not arb_risk.get("allowed", True):
                    logger.warning("Arb risk check failed: %s", arb_risk.get("reasons"))
                    return
            coord = self._get_arb_coordinator()
            success, err = await coord.execute_short_arbitrage(
                market_id, yes_token_id, no_token_id, yes_price_float, no_price_float,
                size, float(confidence), price_fetched_at=opportunity.get("price_fetched_at"),
            )
            if success:
                logger.info(
                    "Short arbitrage executed: %s profit margin %s",
                    market_id, opportunity.get("profit_margin", 0),
                    market=market_id, profit_margin=opportunity.get("profit_margin", 0), is_negrisk=is_negrisk
                )
            elif err:
                logger.warning("Short arbitrage failed for %s: %s", market_id, err)
        except Exception as e:
            logger.error("Error executing short arbitrage: %s", e, exc_info=True)
    
    async def _execute_bundle_arbitrage(self, opportunity: Dict):
        try:
            market_id = opportunity.get("market_id")
            token_ids = opportunity.get("token_ids", [])
            prices = opportunity.get("prices", [])
            confidence = opportunity.get("confidence", _arb_setting("ARB_DEFAULT_CONFIDENCE", 0.5))
            if not market_id or not token_ids or not prices or len(token_ids) != len(prices):
                logger.warning("Invalid bundle arbitrage opportunity")
                return
            market_id = await self._resolve_market_id(str(market_id))
            # Kelly-based sizing for bundle arb
            try:
                _avg_price = sum(float(p) for p in prices) / len(prices) if prices else 0.5
                size = await self.calculate_bot_position_size(float(confidence), _avg_price)
                size = max(1.0, size)
                size = min(size, self.default_order_size * 2.0)  # cap at 2× default
            except Exception:
                size = self.default_order_size
            orders = []
            for token_id, price in zip(token_ids, prices):
                order = await self.place_order(
                    market_id=market_id,
                    token_id=str(token_id),
                    side="BUY",
                    size=size,
                    price=float(price),
                    confidence=float(confidence),
                )
                orders.append(order)
                if not order.get("success"):
                    logger.warning("Bundle arbitrage partial failure: %s order failed", token_id)
                    break
            if all(o.get("success") for o in orders):
                logger.info(
                    "Bundle arbitrage executed: %s profit margin %s",
                    market_id, opportunity.get("profit_margin", 0),
                    market=market_id, profit_margin=opportunity.get("profit_margin", 0), token_count=len(token_ids)
                )
        except Exception as e:
            logger.error("Error executing bundle arbitrage: %s", e, exc_info=True)
    
    # ── Bond Strategy: buy >$0.95 near resolution for ~5% return ────────

    async def _scan_bond_opportunities(self, markets: List[Dict]) -> None:
        """Buy YES tokens priced > $0.95 with < 7 days to resolution for near-certain return."""
        min_price = _arb_setting("BOND_STRATEGY_MIN_PRICE", 0.95)
        max_days = int(_arb_setting("BOND_STRATEGY_MAX_RESOLUTION_DAYS", 7))
        min_liquidity = _arb_setting("MIN_MARKET_LIQUIDITY", 1000.0)
        now = datetime.now(timezone.utc)
        max_bonds = int(_arb_setting("BOND_MAX_PER_SCAN", 3))
        bonds_executed = 0

        for market in markets:
            if bonds_executed >= max_bonds:
                break
            try:
                mid = str(market.get("id", ""))
                if not mid:
                    continue
                # Check time to resolution
                end_raw = get_end_date_from_dict(market)
                if not end_raw:
                    continue
                if isinstance(end_raw, (int, float)):
                    end_dt = datetime.fromtimestamp(end_raw, tz=timezone.utc)
                elif isinstance(end_raw, str):
                    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                else:
                    continue
                days_left = (end_dt - now).total_seconds() / 86400
                if days_left <= 0 or days_left > max_days:
                    continue

                # Check liquidity (API may return volume as string)
                try:
                    volume = float(market.get("volume") or 0)
                except (ValueError, TypeError):
                    volume = 0
                if volume < min_liquidity:
                    continue

                tokens = market.get("tokens", [])
                for token in tokens:
                    if not isinstance(token, dict):
                        continue
                    price_raw = token.get("outcomePrice") or token.get("price")
                    price = self.validate_price(price_raw, mid)
                    if price is None or price < min_price:
                        continue
                    token_id = token.get("tokenId") or token.get("token_id")
                    if not token_id:
                        continue

                    # Skip ambiguous resolution criteria (simple heuristic: question too short)
                    question = market.get("question", "")
                    if len(question) < 20:
                        continue

                    profit = 1.0 - price
                    if profit <= 0.005:
                        continue  # Not worth transaction costs

                    if await self._was_executed_recently({"type": "bond", "market_id": mid}):
                        continue

                    resolved_mid = await self._resolve_market_id(mid)

                    # K3 FIX: Apply signal enhancements to bond trades (sentiment, signals)
                    bond_confidence = min(0.98, 0.90 + profit)
                    try:
                        bond_confidence = await self.apply_signal_enhancements(
                            resolved_mid, str(token_id), "YES", bond_confidence, market,
                        )
                    except Exception as e:
                        logger.debug("Bond signal enhancement failed: %s", e)

                    # Kelly-based sizing for bond arb
                    try:
                        size = await self.calculate_bot_position_size(bond_confidence, price)
                        size = max(1.0, size)
                        size = min(size, _arb_setting("BOND_MAX_SIZE", 200.0))
                    except Exception:
                        size = min(self.default_order_size, _arb_setting("BOND_MAX_SIZE", 200.0))
                    order = await self.place_order(
                        market_id=resolved_mid,
                        token_id=str(token_id),
                        side="BUY",
                        size=size,
                        price=price,
                        confidence=bond_confidence,
                    )
                    if order.get("success"):
                        await self._mark_executed({"type": "bond", "market_id": mid})
                        bonds_executed += 1
                        logger.info(
                            "Bond strategy executed: %s @ %.3f (%.1f%% return in %.0f days)",
                            mid, price, profit * 100, days_left,
                            market=mid, price=price, profit_pct=profit * 100, days_left=days_left,
                        )
                    break  # One token per market
            except Exception as e:
                logger.debug("Bond scan error for %s: %s", market.get("id"), e)

    # ── NegRisk Multi-Outcome Arbitrage ──────────────────────────────────

    async def _scan_negrisk_arbitrage(self, markets: List[Dict]) -> None:
        """
        Tier 2 #12: Multi-outcome NegRisk arbitrage.

        Two legs:
          LEG A (Buy All): SUM(YES_prices) < 1 → buy all outcomes, one must resolve YES.
          LEG B (Sell All): SUM(YES_prices) > N-1 → equivalent to SUM(NO_prices) < 1.
                            Buy all NO outcomes (short all YES). One must resolve NO.

        Position sizing:
          - Confidence = profit / max_price (normalized edge signal, capped at 0.95)
          - Per-outcome Kelly size proportional to (1 - outcome_price) / total_budget
            i.e. cheaper outcomes get a larger allocation (more upside per dollar)
          - Max total risk = ARB_DEFAULT_ORDER_SIZE × n_outcomes
          - Min edge = ARB_MIN_NET_EDGE (default 0.005 = 0.5pp)

        NegRisk filter:
          - market.neg_risk (ORM column from migration 017) enables priority processing
          - Also detects via outcome_count > 2 OR token count >= 3
        """
        min_edge = getattr(settings, "ARB_MIN_NET_EDGE", 0.005)
        max_total_risk = getattr(settings, "NEGRISK_MAX_TOTAL_RISK", self.default_order_size * 3)

        for market in markets:
            try:
                tokens = market.get("tokens", [])
                if not isinstance(tokens, list) or len(tokens) < 3:
                    continue
                mid = str(market.get("id", ""))
                if not mid:
                    continue

                # Use neg_risk ORM flag (migration 017) as priority signal
                is_negrisk = bool(market.get("neg_risk")) or len(tokens) >= 3

                if not is_negrisk:
                    continue

                yes_prices = []
                token_ids = []
                for token in tokens:
                    if not isinstance(token, dict):
                        continue
                    price_raw = token.get("outcomePrice")
                    tid = token.get("tokenId")
                    if price_raw is None or not tid:
                        continue
                    try:
                        p = float(price_raw)
                        if p <= 0 or p >= 1 or math.isnan(p) or math.isinf(p):
                            continue
                    except (ValueError, TypeError):
                        continue
                    yes_prices.append(p)
                    token_ids.append(str(tid))

                if len(yes_prices) < 3:
                    continue

                n = len(yes_prices)
                total_yes = sum(yes_prices)
                resolved_mid = await self._resolve_market_id(mid)

                # ── LEG A: Buy all YES outcomes when SUM(YES) < 1 ──────────
                if total_yes < 1.0 - self.min_profit_threshold:
                    profit = 1.0 - total_yes
                    if profit < min_edge:
                        continue
                    if await self._was_executed_recently({"type": "negrisk_buy_all", "market_id": mid}):
                        continue

                    # Proportional Kelly sizing: cheaper outcomes get more allocation
                    # Budget per outcome ∝ (1 - price) so we equalize dollar risk per outcome
                    complement_sum = sum(1.0 - p for p in yes_prices)
                    confidence = min(0.95, 0.5 + profit * 3)

                    all_success = True
                    for tid, pr in zip(token_ids, yes_prices):
                        # Kelly-based sizing per leg, proportional to upside
                        complement_weight = (1.0 - pr) / complement_sum if complement_sum > 0 else 1.0 / n
                        try:
                            size = await self.calculate_bot_position_size(confidence, pr)
                            size = max(1.0, size * complement_weight * n)
                            size = min(size, max_total_risk * complement_weight, self.default_order_size * 2)
                        except Exception:
                            size = min(max_total_risk * complement_weight, self.default_order_size * 2)
                        size = max(size, 1.0)
                        order = await self.place_order(
                            market_id=resolved_mid, token_id=tid, side="BUY",
                            size=size, price=pr, confidence=confidence,
                        )
                        if not order.get("success"):
                            all_success = False
                            break

                    if all_success:
                        await self._mark_executed({"type": "negrisk_buy_all", "market_id": mid})
                        logger.info(
                            "NegRisk LEG-A (buy all %d outcomes): market=%s total_yes=%.3f profit=%.3f confidence=%.2f",
                            n, mid, total_yes, profit, confidence,
                            market=mid, num_outcomes=n, total_yes=total_yes, profit=profit,
                        )

                # ── LEG B: Buy all NO outcomes when SUM(YES) > N-1 ────────
                # Equivalent to SUM(NO prices) < 1 because NO_i = 1 - YES_i
                # and SUM(NO) = N - SUM(YES) → profit = 1 - (N - SUM(YES)) = SUM(YES) - (N-1)
                elif total_yes > (n - 1) + self.min_profit_threshold:
                    no_prices = [1.0 - p for p in yes_prices]
                    total_no = sum(no_prices)
                    profit = 1.0 - total_no  # = total_yes - (n-1)
                    if profit < min_edge:
                        continue
                    if await self._was_executed_recently({"type": "negrisk_sell_all", "market_id": mid}):
                        continue

                    # Complementary sizing: now cheap NOs get more allocation
                    no_complement_sum = sum(1.0 - np for np in no_prices)
                    confidence = min(0.95, 0.5 + profit * 3)

                    all_success = True
                    for tid, pr_yes, pr_no in zip(token_ids, yes_prices, no_prices):
                        if pr_no <= 0 or pr_no >= 1:
                            continue
                        complement_weight = (1.0 - pr_no) / no_complement_sum if no_complement_sum > 0 else 1.0 / n
                        try:
                            size = await self.calculate_bot_position_size(confidence, pr_no)
                            size = max(1.0, size * complement_weight * n)
                            size = min(size, max_total_risk * complement_weight, self.default_order_size * 2)
                        except Exception:
                            size = min(max_total_risk * complement_weight, self.default_order_size * 2)
                        size = max(size, 1.0)
                        # Buy NO = sell YES on the other side; use NO token (tokenId is outcome-specific)
                        order = await self.place_order(
                            market_id=resolved_mid, token_id=tid, side="NO",
                            size=size, price=pr_no, confidence=confidence,
                        )
                        if not order.get("success"):
                            all_success = False
                            break

                    if all_success:
                        await self._mark_executed({"type": "negrisk_sell_all", "market_id": mid})
                        logger.info(
                            "NegRisk LEG-B (sell all %d outcomes): market=%s total_yes=%.3f profit=%.3f confidence=%.2f",
                            n, mid, total_yes, profit, confidence,
                            market=mid, num_outcomes=n, total_yes=total_yes, profit=profit,
                        )

            except Exception as e:
                logger.debug("NegRisk scan error for %s: %s", market.get("id"), e)

    async def _execute_cross_market_arbitrage(self, opportunity: Dict):
        try:
            opp_type = opportunity.get("type")
            if opp_type != "cross_market_long":
                logger.warning("Unknown cross-market arbitrage type: %s", opp_type)
                return
            market1_id = await self._resolve_market_id(str(opportunity.get("market1_id", "")))
            market2_id = await self._resolve_market_id(str(opportunity.get("market2_id", "")))
            confidence = opportunity.get("confidence", _arb_setting("ARB_DEFAULT_CONFIDENCE_CROSS", 0.4))
            # Kelly-based sizing for cross-market arb
            try:
                _avg_price = ((float(opportunity.get("market1_yes_price") or opportunity.get("market1_no_price") or 0.5))
                              + (float(opportunity.get("market2_yes_price") or opportunity.get("market2_no_price") or 0.5))) / 2.0
                size = await self.calculate_bot_position_size(float(confidence), _avg_price)
                size = max(1.0, size)
                size = min(size, self.default_order_size * 2.0)
            except Exception:
                size = self.default_order_size
            if "market1_yes_token" in opportunity:
                token1 = opportunity.get("market1_yes_token")
                token2 = opportunity.get("market2_yes_token")
                price1 = opportunity.get("market1_yes_price")
                price2 = opportunity.get("market2_yes_price")
            else:
                token1 = opportunity.get("market1_no_token")
                token2 = opportunity.get("market2_no_token")
                price1 = opportunity.get("market1_no_price")
                price2 = opportunity.get("market2_no_price")
            
            if not all([market1_id, market2_id, token1, token2, price1, price2]):
                logger.warning("Missing required fields for cross-market arbitrage")
                return
            
            order1 = await self.place_order(
                market_id=str(market1_id),
                token_id=str(token1),
                side="BUY",
                size=size,
                price=float(price1),
                confidence=float(confidence),
            )
            if order1.get("success"):
                order2 = await self.place_order(
                    market_id=str(market2_id),
                    token_id=str(token2),
                    side="BUY",
                    size=size,
                    price=float(price2),
                    confidence=float(confidence),
                )
                if not order2.get("success"):
                    order2 = await self.place_order(
                        market_id=str(market2_id),
                        token_id=str(token2),
                        side="BUY",
                        size=size,
                        price=float(price2),
                        confidence=float(confidence),
                    )
                if order2.get("success"):
                    logger.info(
                        "Cross-market arbitrage executed: %s + %s profit margin %s",
                        market1_id, market2_id, opportunity.get("profit_margin", 0),
                        market1=market1_id, market2=market2_id, profit_margin=opportunity.get("profit_margin", 0)
                    )
                else:
                    exit1 = await self.place_order(
                        market_id=str(market1_id),
                        token_id=str(token1),
                        side="SELL",
                        size=size,
                        price=float(price1),
                        confidence=float(confidence),
                    )
                    if exit1.get("success"):
                        logger.warning("Cross-market arb leg 2 failed, exited leg 1 for %s", market1_id)
                    else:
                        logger.error("Cross-market arb leg 2 failed and exit leg 1 failed for %s", market1_id)
            else:
                logger.warning("Cross-market arbitrage failed: Market1 order failed")
        except Exception as e:
            logger.error("Error executing cross-market arbitrage: %s", e, exc_info=True)
