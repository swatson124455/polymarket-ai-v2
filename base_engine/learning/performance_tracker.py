"""
Performance Tracker - Track and analyze trading performance by multiple dimensions.

Populates the Pattern Analysis dashboard with comprehensive performance metrics.
"""
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from base_engine.data.database import Database, PerformanceRecord, Position, Trade
from base_engine.data.redis_cache import RedisCache

logger = get_logger()


class PerformanceTracker:
    """
    Track and analyze trading performance by multiple dimensions.
    Populates the Pattern Analysis dashboard.
    """
    
    DIMENSIONS = [
        "market_category",      # politics, crypto, sports, etc.
        "entry_price_range",    # 0-20, 20-40, 40-60, 60-80, 80-100
        "time_to_resolution",   # <1 week, 1-4 weeks, 1-3 months, 3+ months
        "liquidity_level",      # thin, moderate, deep
        "bot_strategy",         # which bot made the trade
        "signal_source",        # what triggered the entry
        "day_of_week",
        "hour_of_day",
        "market_regime",        # CALM, VOLATILE, TRENDING, etc.
    ]
    
    def __init__(self, db: Database, cache: RedisCache):
        self.db = db
        self.cache = cache
    
    async def record_trade_outcome(
        self,
        trade_id: str,
        bot_name: str,
        market_id: str,
        entry_price: float,
        exit_price: float,
        entry_time: datetime,
        exit_time: datetime,
        profit: float,
        market_category: Optional[str] = None,
        signal_source: Optional[str] = None,
        market_regime: Optional[str] = None
    ):
        """
        Record trade outcome for analysis.
        
        Args:
            trade_id: Trade ID
            bot_name: Bot that made the trade
            market_id: Market ID
            entry_price: Entry price
            exit_price: Exit price
            entry_time: Entry timestamp
            exit_time: Exit timestamp
            profit: Profit/loss in USD
            market_category: Market category (politics, crypto, etc.)
            signal_source: Signal source (news, social, whale, etc.)
            market_regime: Market regime at entry
        """
        try:
            # Calculate metrics
            profit_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
            hold_time_hours = (exit_time - entry_time).total_seconds() / 3600 if exit_time and entry_time else 0.0
            was_winner = profit > 0
            
            # Get market info for additional dimensions
            market = await self._get_market_info(market_id)
            if not market_category and market:
                market_category = market.get("category", "unknown")
            
            # Calculate dimensions
            entry_price_range = self._get_price_range(entry_price)
            liquidity_level = self._get_liquidity_level(market.get("liquidity", 0) if market else 0)
            time_to_resolution_days = int(hold_time_hours / 24) if hold_time_hours > 0 else None
            day_of_week = entry_time.weekday() if entry_time else None
            hour_of_day = entry_time.hour if entry_time else None
            
            # Create performance record
            if self.db.session_factory:
                async with self.db.get_session() as session:
                    record = PerformanceRecord(
                        trade_id=trade_id,
                        bot_name=bot_name,
                        market_id=market_id,
                        market_category=market_category,
                        entry_price_range=entry_price_range,
                        time_to_resolution_days=time_to_resolution_days,
                        liquidity_level=liquidity_level,
                        signal_source=signal_source or "unknown",
                        market_regime=market_regime or "UNKNOWN",
                        day_of_week=day_of_week,
                        hour_of_day=hour_of_day,
                        profit=profit,
                        profit_pct=profit_pct,
                        hold_time_hours=hold_time_hours,
                        was_winner=was_winner,
                        entry_time=entry_time,
                        exit_time=exit_time
                    )
                    session.add(record)
                    await session.commit()
            
            # Update Redis counters for real-time dashboard
            if self.cache.redis:
                for dimension in self.DIMENSIONS:
                    dimension_value = self._extract_dimension_value(
                        dimension,
                        market_category=market_category,
                        entry_price_range=entry_price_range,
                        liquidity_level=liquidity_level,
                        bot_strategy=bot_name,
                        signal_source=signal_source,
                        market_regime=market_regime,
                        day_of_week=day_of_week,
                        hour_of_day=hour_of_day
                    )
                    
                    if dimension_value:
                        key = f"performance:{dimension}:{dimension_value}"
                        
                        if was_winner:
                            await self.cache.redis.hincrby(key, "wins", 1)
                            await self.cache.redis.hincrbyfloat(key, "total_profit", profit)
                        else:
                            await self.cache.redis.hincrby(key, "losses", 1)
                            await self.cache.redis.hincrbyfloat(key, "total_loss", abs(profit))
                        
                        await self.cache.redis.hincrby(key, "total_trades", 1)
                        await self.cache.redis.expire(key, 86400 * 90)  # 90 days TTL
            
            logger.debug(f"Recorded performance for trade {trade_id}", profit=profit, was_winner=was_winner)
            
        except Exception as e:
            logger.error(f"Error recording trade outcome: {str(e)}", exc_info=True)
    
    def _get_price_range(self, price: float) -> str:
        """Convert price to range category."""
        price_pct = price * 100
        if price_pct < 20:
            return "0-20"
        elif price_pct < 40:
            return "20-40"
        elif price_pct < 60:
            return "40-60"
        elif price_pct < 80:
            return "60-80"
        else:
            return "80-100"
    
    def _get_liquidity_level(self, liquidity: float) -> str:
        """Convert liquidity to level category."""
        if liquidity < 10000:
            return "thin"
        elif liquidity < 100000:
            return "moderate"
        else:
            return "deep"
    
    def _extract_dimension_value(
        self,
        dimension: str,
        **kwargs
    ) -> Optional[str]:
        """Extract dimension value from kwargs."""
        mapping = {
            "market_category": kwargs.get("market_category"),
            "entry_price_range": kwargs.get("entry_price_range"),
            "liquidity_level": kwargs.get("liquidity_level"),
            "bot_strategy": kwargs.get("bot_strategy"),
            "signal_source": kwargs.get("signal_source"),
            "market_regime": kwargs.get("market_regime"),
            "day_of_week": str(kwargs.get("day_of_week")) if kwargs.get("day_of_week") is not None else None,
            "hour_of_day": str(kwargs.get("hour_of_day")) if kwargs.get("hour_of_day") is not None else None,
        }
        return mapping.get(dimension)
    
    async def _get_market_info(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Get market information."""
        # Try cache first
        if self.cache.redis:
            cached = await self.cache.get(f"market:{market_id}")
            if cached:
                return cached
        
        # Try database
        if self.db.session_factory:
            async with self.db.get_session() as session:
                from sqlalchemy import select
                from base_engine.data.database import Market
                
                result = await session.execute(
                    select(Market).where(Market.id == market_id)
                )
                market = result.scalar_one_or_none()
                
                if market:
                    market_dict = {
                        "id": market.id,
                        "category": getattr(market, "category", None),
                        "liquidity": getattr(market, "liquidity", 0.0)
                    }
                    
                    # Cache it
                    if self.cache.redis:
                        await self.cache.set(f"market:{market_id}", market_dict, ttl=3600)
                    
                    return market_dict
        
        return None
    
    async def get_performance_by_dimension(
        self,
        dimension: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get performance breakdown by dimension.
        
        Args:
            dimension: Dimension name (e.g., "market_category", "bot_strategy")
        
        Returns:
            Dictionary mapping dimension values to performance metrics
        """
        results = {}
        
        # Get from Redis first (faster, real-time)
        if self.cache.redis:
            pattern = f"performance:{dimension}:*"
            keys = await self.cache.redis.keys(pattern)
            
            for key in keys:
                value = key.decode().split(":")[-1] if isinstance(key, bytes) else key.split(":")[-1]
                data = await self.cache.redis.hgetall(key)
                
                if data:
                    wins = int(data.get(b"wins", 0) if isinstance(data.get(b"wins"), bytes) else data.get("wins", 0))
                    losses = int(data.get(b"losses", 0) if isinstance(data.get(b"losses"), bytes) else data.get("losses", 0))
                    total = wins + losses
                    
                    if total > 0:
                        total_profit = float(data.get(b"total_profit", 0) if isinstance(data.get(b"total_profit"), bytes) else data.get("total_profit", 0))
                        total_loss = float(data.get(b"total_loss", 0) if isinstance(data.get(b"total_loss"), bytes) else data.get("total_loss", 0))
                        
                        results[value] = {
                            "dimension_value": value,
                            "total_trades": total,
                            "wins": wins,
                            "losses": losses,
                            "win_rate": wins / total,
                            "total_profit": total_profit,
                            "total_loss": total_loss,
                            "profit_factor": total_profit / total_loss if total_loss > 0 else float('inf'),
                            "avg_profit_per_trade": (total_profit - total_loss) / total,
                            "net_profit": total_profit - total_loss
                        }
        
        # Also get from database for historical data
        if self.db.session_factory and not results:
            async with self.db.get_session() as session:
                from sqlalchemy import select, func, and_
                
                # Map dimension to column
                dimension_column_map = {
                    "market_category": PerformanceRecord.market_category,
                    "entry_price_range": PerformanceRecord.entry_price_range,
                    "liquidity_level": PerformanceRecord.liquidity_level,
                    "bot_strategy": PerformanceRecord.bot_name,
                    "signal_source": PerformanceRecord.signal_source,
                    "market_regime": PerformanceRecord.market_regime,
                    "day_of_week": PerformanceRecord.day_of_week,
                    "hour_of_day": PerformanceRecord.hour_of_day,
                }
                
                column = dimension_column_map.get(dimension)
                if column:
                    result = await session.execute(
                        select(
                            column,
                            func.count(PerformanceRecord.id).label("total_trades"),
                            func.sum(func.case((PerformanceRecord.was_winner == True, 1), else_=0)).label("wins"),
                            func.sum(func.case((PerformanceRecord.was_winner == False, 1), else_=0)).label("losses"),
                            func.sum(func.case((PerformanceRecord.was_winner == True, PerformanceRecord.profit), else_=0)).label("total_profit"),
                            func.sum(func.case((PerformanceRecord.was_winner == False, func.abs(PerformanceRecord.profit)), else_=0)).label("total_loss"),
                            func.avg(PerformanceRecord.profit).label("avg_profit")
                        ).where(
                            column.isnot(None)
                        ).group_by(column)
                    )
                    
                    rows = result.fetchall()
                    
                    for row in rows:
                        value = str(row[0]) if row[0] is not None else "unknown"
                        total = row[1] or 0
                        wins = row[2] or 0
                        losses = row[3] or 0
                        total_profit = float(row[4] or 0)
                        total_loss = float(row[5] or 0)
                        avg_profit = float(row[6] or 0)
                        
                        if total > 0:
                            results[value] = {
                                "dimension_value": value,
                                "total_trades": total,
                                "wins": wins,
                                "losses": losses,
                                "win_rate": wins / total,
                                "total_profit": total_profit,
                                "total_loss": total_loss,
                                "profit_factor": total_profit / total_loss if total_loss > 0 else float('inf'),
                                "avg_profit_per_trade": avg_profit,
                                "net_profit": total_profit - total_loss
                            }
        
        return results
    
    async def get_category_confidence_multipliers(self, min_trades: int = 10) -> Dict[str, float]:
        """
        L2: Compute per-category confidence multipliers from historical performance.

        Queries performance_records grouped by market_category. Categories with
        fewer than min_trades are excluded (cold-start guard).

        Formula (profit-weighted per reviewer feedback):
            win_rate = wins / total
            avg_pnl_sign = sign(avg_profit) * min(1, |avg_profit| / 50)
            mult = 0.8 + 0.4 * win_rate * (1 + avg_pnl_sign * 0.2)
            Clamped to [0.8, 1.2]

        Returns:
            Dict[str, float]: category -> multiplier (1.0 = neutral)
        """
        if not self.db.session_factory:
            return {}
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import select, func, case
                col = PerformanceRecord.market_category
                result = await session.execute(
                    select(
                        col,
                        func.count(PerformanceRecord.id).label("total"),
                        func.sum(case((PerformanceRecord.was_winner == True, 1), else_=0)).label("wins"),
                        func.avg(PerformanceRecord.profit).label("avg_profit"),
                    ).where(col.isnot(None))
                    .group_by(col)
                    .having(func.count(PerformanceRecord.id) >= min_trades)
                )
                mults: Dict[str, float] = {}
                for row in result.fetchall():
                    category = str(row[0]).lower() if row[0] else "unknown"
                    total = int(row[1] or 0)
                    wins = int(row[2] or 0)
                    avg_profit = float(row[3] or 0.0)
                    if total < min_trades:
                        continue
                    win_rate = wins / total
                    # Profit-weighted formula (reviewer feedback: not binary-only)
                    import math
                    avg_pnl_sign = math.copysign(1, avg_profit) * min(1.0, abs(avg_profit) / 50.0) if avg_profit != 0 else 0.0
                    mult = 0.8 + 0.4 * win_rate * (1.0 + avg_pnl_sign * 0.2)
                    mult = max(0.8, min(1.2, mult))
                    mults[category] = round(mult, 3)
                if mults:
                    logger.debug("L2 category multipliers: %s", mults)
                return mults
        except Exception as e:
            logger.debug("get_category_confidence_multipliers failed: %s", e)
            return {}

    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Get all data for Pattern Analysis dashboard."""
        return {
            "by_market_type": await self.get_performance_by_dimension("market_category"),
            "by_price_range": await self.get_performance_by_dimension("entry_price_range"),
            "by_liquidity": await self.get_performance_by_dimension("liquidity_level"),
            "by_bot": await self.get_performance_by_dimension("bot_strategy"),
            "by_signal": await self.get_performance_by_dimension("signal_source"),
            "by_regime": await self.get_performance_by_dimension("market_regime"),
            "by_day_of_week": await self.get_performance_by_dimension("day_of_week"),
            "by_hour_of_day": await self.get_performance_by_dimension("hour_of_day"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
