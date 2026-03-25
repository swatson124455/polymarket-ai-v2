import numpy as np
import pandas as pd
import math
from typing import Dict, List, Optional
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta, timezone
from structlog import get_logger
from base_engine.data.database import Database
from config.settings import settings

logger = get_logger()


class BoundedDict:
    """Dictionary with maximum size and LRU eviction."""
    def __init__(self, max_size: int = 10000):
        self.data = OrderedDict()
        self.max_size = max_size
    
    def __getitem__(self, key):
        if key in self.data:
            self.data.move_to_end(key)
        return self.data[key]
    
    def __setitem__(self, key, value):
        if key in self.data:
            self.data.move_to_end(key)
        elif len(self.data) >= self.max_size:
            self.data.popitem(last=False)
        self.data[key] = value
    
    def __contains__(self, key):
        return key in self.data
    
    def get(self, key, default=None):
        if key in self.data:
            self.data.move_to_end(key)
        return self.data.get(key, default)
    
    def keys(self):
        return self.data.keys()
    
    def values(self):
        return self.data.values()
    
    def items(self):
        return self.data.items()


class LearningEngine:
    def __init__(self, db: Database):
        self.db = db
        self.patterns = {
            "user_performance": BoundedDict(max_size=10000),
            "market_types": defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0}),
            "price_ranges": defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0}),
            # NOTE: "categories" was duplicate of "market_types" (same category key, same updates).
            # Removed to eliminate redundant storage and compute. "market_types" is canonical.
            "time_to_resolution": defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        }
        self.confidence_weights = {
            "user_based": 0.60,
            "bet_type": 0.40
        }

    async def init(self) -> None:
        """Load patterns from DB when persistence is on. Call once after construction."""
        if getattr(settings, "LEARNING_PERSISTENCE", False) and self.db.session_factory:
            try:
                await self.load_patterns_from_db()
                logger.info("Learning engine loaded patterns from database")
            except Exception as e:
                logger.warning("Could not load patterns from database: %s", e)
    
    async def learn_from_backtest(self, backtest_result) -> Dict:
        logger.info("Learning from backtest results")
        
        if self.db.session_factory is None:
            raise RuntimeError("Database required for learning. Cannot proceed without database connection.")
        
        async with self.db.get_session() as session:
            for trade in backtest_result.trades:
                await self._update_patterns(session, trade)
        
        await self._calculate_confidence_scores()
        if getattr(settings, "LEARNING_PERSISTENCE", False):
            await self.save_patterns_to_db()
        logger.info("Learning complete")
        return self.patterns
    
    async def learn_from_price_history(
        self, since: datetime, limit: int = 10000
    ) -> Dict:
        """
        Learn from price history when trades are sparse.
        Converts consecutive price pairs to trade-like format: pnl = next_price - current_price
        (positive = proxy win for YES). Heuristic: price movement may not reflect true trade outcomes.
        """
        if self.db.session_factory is None:
            raise RuntimeError("Database required for learning. Cannot proceed without database connection.")
        prices = await self.db.get_prices_since(since, limit)
        if not prices:
            logger.info("No price history since %s, skipping learn_from_price_history", since.isoformat())
            return self.patterns
        # Group by (market_id, token_id), sort by timestamp; create trade-like entries from consecutive pairs
        from itertools import groupby
        key_fn = lambda r: (r["market_id"], r.get("token_id") or "")
        sorted_prices = sorted(prices, key=key_fn)
        trade_like: List[Dict] = []
        for (mid, tid), group in groupby(sorted_prices, key=key_fn):
            if not mid or not tid:
                continue
            pts = list(group)
            pts.sort(key=lambda x: x.get("timestamp") or datetime.min)
            for i in range(len(pts) - 1):
                curr = pts[i]
                nxt = pts[i + 1]
                curr_p = float(curr.get("price", 0.5))
                nxt_p = float(nxt.get("price", 0.5))
                if curr_p <= 0 or curr_p > 1 or nxt_p <= 0 or nxt_p > 1:
                    continue
                pnl = nxt_p - curr_p
                trade_like.append({
                    "market_id": mid,
                    "entry_price": curr_p,
                    "pnl": pnl,
                    "entry_time": curr.get("timestamp") or datetime.now(timezone.utc),
                })
        if not trade_like:
            logger.info("No valid price pairs for learning")
            return self.patterns
        async with self.db.get_session() as session:
            for t in trade_like:
                await self._update_patterns(session, t)
        await self._calculate_confidence_scores()
        if getattr(settings, "LEARNING_PERSISTENCE", False):
            await self.save_patterns_to_db()
        logger.info("Learning from %d price-history pairs complete", len(trade_like))
        return self.patterns

    async def learn_from_trades(self, trades: List[Dict]) -> Dict:
        """Learn from a list of trade dicts (market_id, entry_price, pnl, entry_time)."""
        if not trades:
            return self.patterns
        if self.db.session_factory is None:
            raise RuntimeError("Database required for learning. Cannot proceed without database connection.")
        async with self.db.get_session() as session:
            for t in trades:
                await self._update_patterns(session, t)
        await self._calculate_confidence_scores()
        if getattr(settings, "LEARNING_PERSISTENCE", False):
            await self.save_patterns_to_db()
        logger.info("Learning from %d trades complete", len(trades))
        return self.patterns
    
    async def save_patterns_to_db(self) -> None:
        """Persist the four pattern dicts to learning_patterns table. Uses atomic increments to avoid losing in-memory updates on crash."""
        if self.db.session_factory is None:
            return
        from sqlalchemy import select, text
        from base_engine.data.database import LearningPattern
        async with self.db.get_session() as session:
            # L6 FIX: Also persist user_performance (was excluded, lost on restart)
            for ptype in ["market_types", "price_ranges", "time_to_resolution", "user_performance"]:
                for key, stats in self.patterns[ptype].items():
                    pk, pv = str(ptype), str(key)
                    wins, losses, total = stats.get("wins", 0), stats.get("losses", 0), stats.get("total", 0)
                    conf = float(stats.get("confidence", 0.0))
                    sample = stats.get("sample_size", total)
                    r = await session.execute(
                        select(LearningPattern).where(
                            LearningPattern.pattern_type == pk,
                            LearningPattern.pattern_key == pv,
                        )
                    )
                    row = r.scalar_one_or_none()
                    if row:
                        dw, dl, dt = max(0, wins - row.wins), max(0, losses - row.losses), max(0, total - row.total)
                        if dw == 0 and dl == 0 and dt == 0:
                            row.confidence = conf
                            row.sample_size = sample
                        else:
                            await session.execute(
                                text("""
                                    UPDATE learning_patterns
                                    SET wins = wins + :dw, losses = losses + :dl, total = total + :dt,
                                        confidence = :conf, sample_size = :sample
                                    WHERE pattern_type = :pt AND pattern_key = :pk
                                """),
                                {"dw": dw, "dl": dl, "dt": dt, "conf": conf, "sample": sample, "pt": pk, "pk": pv},
                            )
                    else:
                        session.add(LearningPattern(
                            pattern_type=pk, pattern_key=pv, wins=wins, losses=losses, total=total,
                            confidence=conf, sample_size=sample,
                        ))
            await session.commit()
    
    async def load_patterns_from_db(self) -> None:
        """Load patterns from learning_patterns into the four pattern dicts."""
        if self.db.session_factory is None:
            return
        from sqlalchemy import select
        from base_engine.data.database import LearningPattern
        async with self.db.get_session() as session:
            result = await session.execute(select(LearningPattern))
            for r in result.scalars().all():
                # L6 FIX: Also load user_performance patterns
                if r.pattern_type in ["market_types", "price_ranges", "time_to_resolution", "user_performance"]:
                    self.patterns[r.pattern_type][r.pattern_key] = {
                        "wins": r.wins, "losses": r.losses, "total": r.total,
                        "confidence": r.confidence, "sample_size": r.sample_size,
                    }
        logger.info("Loaded patterns from database")
    
    async def _update_patterns(self, session, trade: Dict):
        market_id = trade.get("market_id")
        if not market_id:
            logger.warning("Trade missing market_id, skipping pattern update")
            return
        
        from sqlalchemy import select
        from base_engine.data.database import Market
        
        # BUG FIX: Add comprehensive error handling for database operations
        # Root cause: Database operations can fail for various reasons
        # Impact: Learning engine crashes on database errors
        # Fix: Wrap in try/except with specific error handling
        try:
            result = await session.execute(
                select(Market).where(Market.id == market_id)
            )
            market = result.scalar_one_or_none()
            
            if not market:
                logger.debug(f"Market {market_id} not found in database, skipping pattern update")
                return
        except Exception as e:
            logger.warning(f"Database error fetching market {market_id}: {str(e)}")
            return
        
        entry_price = trade.get("entry_price", 0.5)
        
        # BUG FIX: Add validation for NaN/infinity values in calculations
        # Root cause: Entry price might be NaN or infinity, causing math errors
        # Impact: Learning calculations fail or produce invalid results
        # Fix: Validate and sanitize all numeric inputs
        if not isinstance(entry_price, (int, float)):
            logger.warning(f"Invalid entry_price type {type(entry_price)} in trade, using 0.5")
            entry_price = 0.5
        else:
            try:
                entry_price = float(entry_price)
                # Check for NaN/infinity
                if math.isnan(entry_price) or math.isinf(entry_price):
                    logger.warning(f"entry_price is NaN/Infinity in trade, using 0.5")
                    entry_price = 0.5
                elif entry_price < 0 or entry_price > 1:
                    logger.warning(f"entry_price {entry_price} out of range [0,1] in trade, clamping")
                    entry_price = max(0.0, min(1.0, entry_price))
            except (ValueError, TypeError):
                logger.warning(f"Could not convert entry_price {entry_price} to float, using 0.5")
                entry_price = 0.5
        
        price_range = self._get_price_range(entry_price)
        category = market.category or "unknown"
        entry_time = trade.get("entry_time")
        if isinstance(entry_time, str):
            try:
                if "Z" in entry_time:
                    entry_time = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                else:
                    entry_time = datetime.fromisoformat(entry_time)
            except (ValueError, AttributeError) as e:
                logger.warning(
                    "Could not parse entry_time",
                    entry_time_str=entry_time,
                    error=str(e),
                    using_default=True
                )
                entry_time = datetime.now(timezone.utc)
        elif not isinstance(entry_time, datetime):
            entry_time = datetime.now(timezone.utc)
        
        time_to_res = self._calculate_time_to_resolution(market.end_date_iso, entry_time)
        
        pnl = trade.get("pnl", 0.0)
        is_win = isinstance(pnl, (int, float)) and pnl > 0
        
        self.patterns["market_types"][category]["total"] += 1
        if is_win:
            self.patterns["market_types"][category]["wins"] += 1
        else:
            self.patterns["market_types"][category]["losses"] += 1
        
        self.patterns["price_ranges"][price_range]["total"] += 1
        if is_win:
            self.patterns["price_ranges"][price_range]["wins"] += 1
        else:
            self.patterns["price_ranges"][price_range]["losses"] += 1
        
        self.patterns["time_to_resolution"][time_to_res]["total"] += 1
        if is_win:
            self.patterns["time_to_resolution"][time_to_res]["wins"] += 1
        else:
            self.patterns["time_to_resolution"][time_to_res]["losses"] += 1
    
    def _get_price_range(self, price: float) -> str:
        if not isinstance(price, (int, float)):
            return "0.5-0.7"
        price = float(price)
        if price < 0.1:
            return "0-0.1"
        elif price <= 0.3:
            return "0.1-0.3"
        elif price <= 0.5:
            return "0.3-0.5"
        elif price <= 0.7:
            return "0.5-0.7"
        elif price <= 0.9:
            return "0.7-0.9"
        else:
            return "0.9-1.0"
    
    def _calculate_time_to_resolution(self, end_date: Optional[datetime], entry_time: datetime) -> str:
        if not end_date:
            return "unknown"
        # Normalize both to aware UTC for safe comparison
        if isinstance(end_date, datetime) and end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        if isinstance(entry_time, datetime) and entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        days = (end_date - entry_time).days
        if days < 1:
            return "<1day"
        elif days < 7:
            return "1-7days"
        elif days < 30:
            return "7-30days"
        else:
            return "30+days"
    
    async def _calculate_confidence_scores(self):
        for pattern_type in ["market_types", "price_ranges", "time_to_resolution"]:
            for key, stats in self.patterns[pattern_type].items():
                if stats["total"] > 0:
                    win_rate = stats["wins"] / stats["total"]
                    stats["confidence"] = win_rate
                    stats["sample_size"] = stats["total"]
    
    async def get_user_confidence(self, user_address: str) -> float:
        if user_address in self.patterns["user_performance"]:
            user_stats = self.patterns["user_performance"][user_address]
            if user_stats["total"] > 0:
                return user_stats["wins"] / user_stats["total"]
        raise RuntimeError(f"No confidence data available for user {user_address}. Learn from backtest data first.")
    
    async def get_bet_type_confidence(
        self,
        price: float,
        category: str,
        time_to_res: str
    ) -> float:
        import numpy as np
        from base_engine.utils.validation import validate_price
        
        try:
            price = validate_price(price, "price")
        except ValueError:
            logger.warning(f"Invalid price {price} in get_bet_type_confidence, using 0.5")
            price = 0.5
        
        price_range = self._get_price_range(price)
        
        confidences = []
        weights = []
        
        if price_range in self.patterns["price_ranges"]:
            stats = self.patterns["price_ranges"][price_range]
            if stats.get("total", 0) > 10 and "confidence" in stats:
                conf = stats["confidence"]
                if isinstance(conf, (int, float)) and not (math.isnan(conf) or math.isinf(conf)):
                    confidences.append(float(conf))
                    weights.append(min(stats.get("sample_size", stats.get("total", 0)) / 100, 1.0))
        
        # "categories" was merged into "market_types" (same data). Use market_types for category lookup.
        if category in self.patterns["market_types"]:
            stats = self.patterns["market_types"][category]
            if stats.get("total", 0) > 10 and "confidence" in stats:
                conf = stats["confidence"]
                if isinstance(conf, (int, float)) and not (math.isnan(conf) or math.isinf(conf)):
                    confidences.append(float(conf))
                    weights.append(min(stats.get("sample_size", stats.get("total", 0)) / 100, 1.0))
        
        if time_to_res in self.patterns["time_to_resolution"]:
            stats = self.patterns["time_to_resolution"][time_to_res]
            if stats.get("total", 0) > 10 and "confidence" in stats:
                conf = stats["confidence"]
                if isinstance(conf, (int, float)) and not (math.isnan(conf) or math.isinf(conf)):
                    confidences.append(float(conf))
                    weights.append(min(stats.get("sample_size", stats.get("total", 0)) / 100, 1.0))
        
        if not confidences:
            raise RuntimeError(f"No confidence data available for bet type (price={price}, category={category}, time_to_res={time_to_res}). Learn from backtest data first.")
        
        if weights and len(weights) == len(confidences):
            weighted_confidence = np.average(confidences, weights=weights)
        else:
            weighted_confidence = np.mean(confidences)
        
        result = float(weighted_confidence)
        if math.isnan(result) or math.isinf(result):
            logger.warning(f"Invalid confidence calculated: {result}, using 0.5")
            return 0.5
        
        return max(0.0, min(1.0, result))
    
    async def calculate_combined_confidence(
        self,
        user_address: str,
        price: float,
        category: str,
        time_to_res: str
    ) -> float:
        """Combined confidence from user + bet-type. Uses only bet-type when user missing or not learned."""
        # L3 FIX: user_performance dict is never populated, so user_conf was always
        # 0.5, contributing a constant 0.30 bias (60% * 0.5). Now: if no user data,
        # use 100% bet_type_conf instead of diluting with a meaningless constant.
        user_conf = None
        addr = (user_address or "").strip() if isinstance(user_address, str) else (user_address or "")
        if addr and addr in self.patterns["user_performance"]:
            u = self.patterns["user_performance"][addr]
            if u.get("total", 0) > 0:
                user_conf = u["wins"] / u["total"]
        try:
            bet_type_conf = await self.get_bet_type_confidence(price, category, time_to_res)
        except RuntimeError:
            bet_type_conf = 0.5
        if user_conf is not None:
            combined = (
                user_conf * self.confidence_weights["user_based"] +
                bet_type_conf * self.confidence_weights["bet_type"]
            )
        else:
            # No user data available — use bet_type_conf only (no constant dilution)
            combined = bet_type_conf
        return max(0.0, min(1.0, combined))
    
    async def update_simulation_confidence(
        self,
        market_id: str,
        predicted_prob: float,
        actual_outcome: float,
        error: float
    ):
        from base_engine.utils.validation import validate_numeric
        
        try:
            error = validate_numeric(error, "error", min_val=0.0)
            predicted_prob = validate_numeric(predicted_prob, "predicted_prob", min_val=0.0, max_val=1.0)
            actual_outcome = validate_numeric(actual_outcome, "actual_outcome", min_val=0.0, max_val=1.0)
        except ValueError as e:
            logger.warning(f"Invalid simulation confidence data: {str(e)}, skipping")
            return
        
        if market_id not in self.patterns:
            # M3 FIX: Evict oldest 20% of market keys when dict exceeds 5000 entries.
            # Without eviction, self.patterns grows unbounded (one key per market_id, never removed).
            # With 2700+ markets and growing, this leaks memory monotonically.
            _MAX_PATTERNS = getattr(settings, "LEARNING_ENGINE_MAX_PATTERNS", 5000) if hasattr(self, '_settings_checked') else 5000
            _STRUCTURAL_KEYS = {"user_performance", "market_types", "price_ranges", "time_to_resolution"}
            if len(self.patterns) >= _MAX_PATTERNS:
                _evict_count = max(1, len(self.patterns) // 5)  # evict 20%
                _oldest_keys = [k for k in list(self.patterns.keys()) if k not in _STRUCTURAL_KEYS][:_evict_count]
                for _k in _oldest_keys:
                    del self.patterns[_k]
                logger.debug("LearningEngine: evicted %d stale pattern keys (dict was at %d)", _evict_count, _MAX_PATTERNS)
            self.patterns[market_id] = {
                "simulation_errors": [],
                "avg_error": 0.0,
                "sample_size": 0
            }

        self.patterns[market_id]["simulation_errors"].append(error)
        if len(self.patterns[market_id]["simulation_errors"]) > 1000:
            self.patterns[market_id]["simulation_errors"].pop(0)
        
        errors = self.patterns[market_id]["simulation_errors"]
        if errors:
            avg_error = sum(errors) / len(errors)
            if not (math.isnan(avg_error) or math.isinf(avg_error)):
                self.patterns[market_id]["avg_error"] = avg_error
            self.patterns[market_id]["sample_size"] = len(errors)
