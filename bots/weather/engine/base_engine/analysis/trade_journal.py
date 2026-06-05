"""
Trade Journal Generator
=======================
Automatic trade journaling with analysis.
"""
from typing import Dict, List, Optional
from datetime import datetime, timezone
from structlog import get_logger
from bots.weather.engine.base_engine.data.database import Database, PerformanceRecord, Trade

logger = get_logger()


class TradeJournal:
    """
    Automatic trade journaling with analysis.
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
    
    async def generate_journal_entry(self, trade_id: str) -> Dict:
        """
        Generate journal entry for a trade.
        
        Args:
            trade_id: Trade ID
        
        Returns:
            Dict with journal entry
        """
        if not self.db or not self.db.session_factory:
            return {"error": "Database not available"}
        
        async with self.db.get_session() as session:
            from sqlalchemy import select
            
            # Get trade
            result = await session.execute(
                select(Trade).where(Trade.id == trade_id)
            )
            trade = result.scalar_one_or_none()
            
            if not trade:
                return {"error": "Trade not found"}
            
            # Get performance record if available
            perf_result = await session.execute(
                select(PerformanceRecord).where(PerformanceRecord.market_id == trade.market_id)
                .order_by(PerformanceRecord.entry_time.desc())
                .limit(1)
            )
            perf = perf_result.scalar_one_or_none()
            
            entry = {
                "trade_id": trade_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade": {
                    "market_id": trade.market_id,
                    "side": trade.side,
                    "size": float(trade.size or 0),
                    "price": float(trade.price or 0),
                    "timestamp": trade.timestamp.isoformat() if trade.timestamp else None
                },
                "outcome": None,
                "lessons": [],
                "rating": None
            }
            
            if perf:
                entry["outcome"] = {
                    "profit": float(perf.profit or 0),
                    "profit_pct": float(perf.profit_pct or 0),
                    "was_winner": perf.was_winner,
                    "hold_time_hours": float(perf.hold_time_hours or 0)
                }
                
                # Generate lessons
                entry["lessons"] = self._generate_lessons(trade, perf)
                entry["rating"] = self._rate_trade(trade, perf)
            
            return entry
    
    def _generate_lessons(self, trade: Trade, perf: PerformanceRecord) -> List[str]:
        """Generate lessons learned from trade"""
        lessons = []
        
        if perf.was_winner:
            if perf.profit_pct and perf.profit_pct > 0.5:
                lessons.append("High-profit trade - consider scaling up similar setups")
            if perf.hold_time_hours and perf.hold_time_hours < 24:
                lessons.append("Quick win - good entry timing")
        else:
            if perf.profit_pct and perf.profit_pct < -0.3:
                lessons.append("Large loss - review risk management")
            if perf.hold_time_hours and perf.hold_time_hours > 168:  # > 1 week
                lessons.append("Long hold time - consider earlier exit signals")
        
        return lessons
    
    def _rate_trade(self, trade: Trade, perf: PerformanceRecord) -> str:
        """Rate trade quality (A-F)"""
        if not perf.was_winner:
            if perf.profit_pct and perf.profit_pct < -0.2:
                return "F"
            return "D"
        
        if perf.profit_pct and perf.profit_pct > 0.3:
            return "A"
        elif perf.profit_pct and perf.profit_pct > 0.1:
            return "B"
        else:
            return "C"
    
    async def generate_period_journal(
        self,
        start_date: str,
        end_date: str
    ) -> Dict:
        """
        Generate journal for a time period.
        
        Returns:
            Dict with summary and all entries
        """
        if not self.db or not self.db.session_factory:
            return {"error": "Database not available"}
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, func

            start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            
            result = await session.execute(
                select(Trade)
                .where(Trade.timestamp >= start)
                .where(Trade.timestamp <= end)
                .order_by(Trade.timestamp.desc())
            )
            trades = result.scalars().all()
            
            entries = []
            for trade in trades:
                entry = await self.generate_journal_entry(trade.id)
                if "error" not in entry:
                    entries.append(entry)
            
            # Summary
            total_trades = len(entries)
            winners = sum(1 for e in entries if e.get("outcome", {}).get("was_winner"))
            total_profit = sum(e.get("outcome", {}).get("profit", 0) for e in entries)
            
            return {
                "period": {
                    "start": start_date,
                    "end": end_date
                },
                "summary": {
                    "total_trades": total_trades,
                    "winners": winners,
                    "losers": total_trades - winners,
                    "win_rate": winners / total_trades if total_trades > 0 else 0.0,
                    "total_profit": total_profit
                },
                "entries": entries
            }
