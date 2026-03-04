"""
Performance Attribution
========================
Breaks down P&L by dimensions (category, bot, strategy, etc.)
"""
from typing import Dict, List, Optional
from collections import defaultdict
from structlog import get_logger
from base_engine.data.database import Database, PerformanceRecord

logger = get_logger()


class PerformanceAttribution:
    """
    Breaks down P&L by dimensions.
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
    
    async def attribute_performance(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict:
        """
        Comprehensive performance attribution.
        
        Returns:
            Dict with breakdowns by:
                - category
                - bot_name
                - market_regime
                - entry_price_range
                - time_to_resolution
                - signal_source
        """
        if not self.db or not self.db.session_factory:
            return {}
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, func, and_
            from datetime import datetime, timezone
            
            query = select(PerformanceRecord)
            
            if start_date:
                start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                query = query.where(PerformanceRecord.entry_time >= start)
            
            if end_date:
                end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                query = query.where(PerformanceRecord.entry_time <= end)
            
            result = await session.execute(query)
            records = result.scalars().all()
            
            if not records:
                return {"total_trades": 0, "total_profit": 0.0}
            
            # Aggregate by dimensions
            by_category = defaultdict(lambda: {"profit": 0.0, "trades": 0, "wins": 0})
            by_bot = defaultdict(lambda: {"profit": 0.0, "trades": 0, "wins": 0})
            by_regime = defaultdict(lambda: {"profit": 0.0, "trades": 0, "wins": 0})
            by_price_range = defaultdict(lambda: {"profit": 0.0, "trades": 0, "wins": 0})
            by_signal = defaultdict(lambda: {"profit": 0.0, "trades": 0, "wins": 0})
            
            total_profit = 0.0
            total_trades = 0
            total_wins = 0
            
            for record in records:
                profit = float(record.profit or 0)
                total_profit += profit
                total_trades += 1
                if record.was_winner:
                    total_wins += 1
                
                # By category
                if record.market_category:
                    by_category[record.market_category]["profit"] += profit
                    by_category[record.market_category]["trades"] += 1
                    if record.was_winner:
                        by_category[record.market_category]["wins"] += 1
                
                # By bot
                if record.bot_name:
                    by_bot[record.bot_name]["profit"] += profit
                    by_bot[record.bot_name]["trades"] += 1
                    if record.was_winner:
                        by_bot[record.bot_name]["wins"] += 1
                
                # By regime
                if record.market_regime:
                    by_regime[record.market_regime]["profit"] += profit
                    by_regime[record.market_regime]["trades"] += 1
                    if record.was_winner:
                        by_regime[record.market_regime]["wins"] += 1
                
                # By price range
                if record.entry_price_range:
                    by_price_range[record.entry_price_range]["profit"] += profit
                    by_price_range[record.entry_price_range]["trades"] += 1
                    if record.was_winner:
                        by_price_range[record.entry_price_range]["wins"] += 1
                
                # By signal source
                if record.signal_source:
                    by_signal[record.signal_source]["profit"] += profit
                    by_signal[record.signal_source]["trades"] += 1
                    if record.was_winner:
                        by_signal[record.signal_source]["wins"] += 1
            
            # Calculate win rates and ROI
            def calc_metrics(d: Dict) -> Dict:
                return {
                    "profit": d["profit"],
                    "trades": d["trades"],
                    "wins": d["wins"],
                    "win_rate": d["wins"] / d["trades"] if d["trades"] > 0 else 0.0,
                    "avg_profit": d["profit"] / d["trades"] if d["trades"] > 0 else 0.0
                }
            
            return {
                "summary": {
                    "total_profit": total_profit,
                    "total_trades": total_trades,
                    "total_wins": total_wins,
                    "win_rate": total_wins / total_trades if total_trades > 0 else 0.0,
                    "avg_profit": total_profit / total_trades if total_trades > 0 else 0.0
                },
                "by_category": {k: calc_metrics(v) for k, v in by_category.items()},
                "by_bot": {k: calc_metrics(v) for k, v in by_bot.items()},
                "by_regime": {k: calc_metrics(v) for k, v in by_regime.items()},
                "by_price_range": {k: calc_metrics(v) for k, v in by_price_range.items()},
                "by_signal_source": {k: calc_metrics(v) for k, v in by_signal.items()}
            }
