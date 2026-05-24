"""
Performance Tracker - Tracks execution quality and slippage per bot.
"""
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from structlog import get_logger
from bots.weather.engine.base_engine.monitoring.metrics_collector import metrics_collector

logger = get_logger()


class PerformanceTracker:
    """Tracks trade execution quality metrics."""
    
    def __init__(self, db):
        self.db = db
        self.in_memory_quality: Dict[str, List[Dict]] = {}
        self.max_memory_size = 1000
    
    async def record_execution(
        self, 
        bot_name: str,
        market_id: str,
        expected_price: float,
        actual_price: float,
        size: float
    ):
        """
        Record execution quality.
        
        Tracks slippage: (actual - expected) / expected
        Negative slippage = better than expected (good)
        Positive slippage = worse than expected (bad)
        """
        try:
            slippage = (actual_price - expected_price) / expected_price if expected_price > 0 else 0
            
            execution_data = {
                'bot_name': bot_name,
                'market_id': market_id,
                'expected_price': expected_price,
                'actual_price': actual_price,
                'slippage': slippage,
                'size': size,
                'executed_at': datetime.now(timezone.utc)
            }
            
            # Store in memory
            if bot_name not in self.in_memory_quality:
                self.in_memory_quality[bot_name] = []
            
            self.in_memory_quality[bot_name].append(execution_data)
            
            # Trim if too large
            if len(self.in_memory_quality[bot_name]) > self.max_memory_size:
                self.in_memory_quality[bot_name] = self.in_memory_quality[bot_name][-self.max_memory_size:]
            
            # Record metric
            metrics_collector.record_trade(
                bot_name=bot_name,
                side="BUY" if slippage < 0 else "SELL",
                success=True,
                latency=0
            )
            
            # Persist to DB
            if self.db.session_factory:
                await self._persist_execution(execution_data)
            
            logger.info(
                f"Execution quality tracked: {bot_name} | "
                f"Market: {market_id[:20]} | "
                f"Slippage: {slippage*100:.2f}% | "
                f"Price: {expected_price:.4f} → {actual_price:.4f}"
            )
            
        except Exception as e:
            logger.warning(f"Failed to record execution quality: {e}")
    
    async def _persist_execution(self, execution_data: Dict):
        """Persist execution to database."""
        try:
            from sqlalchemy import insert
            from bots.weather.engine.base_engine.data.database import ExecutionQuality
            
            async with self.db.get_session() as session:
                stmt = insert(ExecutionQuality).values(execution_data)
                await session.execute(stmt)
                await session.commit()
                
        except Exception as e:
            logger.debug(f"Failed to persist execution: {e}")
    
    async def get_bot_slippage_stats(self, bot_name: str, days: int = 7) -> Dict:
        """
        Get slippage statistics for a bot.
        
        Returns:
            {
                'avg_slippage': float,
                'median_slippage': float,
                'worst_slippage': float,
                'best_slippage': float,
                'total_trades': int
            }
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Get from DB
            from sqlalchemy import select, func
            from bots.weather.engine.base_engine.data.database import ExecutionQuality
            
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(ExecutionQuality).where(
                        ExecutionQuality.bot_name == bot_name,
                        ExecutionQuality.executed_at >= cutoff
                    ).order_by(ExecutionQuality.executed_at.desc())
                )
                executions = result.scalars().all()
            
            if not executions:
                return {
                    'avg_slippage': 0,
                    'median_slippage': 0,
                    'worst_slippage': 0,
                    'best_slippage': 0,
                    'total_trades': 0
                }
            
            slippages = sorted([e.slippage for e in executions])
            
            return {
                'avg_slippage': sum(slippages) / len(slippages),
                'median_slippage': slippages[len(slippages) // 2],
                'worst_slippage': max(slippages),
                'best_slippage': min(slippages),
                'total_trades': len(slippages)
            }
            
        except Exception as e:
            logger.warning(f"Failed to get slippage stats: {e}")
            return {}
    
    async def get_market_execution_quality(self, market_id: str) -> Dict:
        """Get execution quality for a specific market."""
        try:
            from sqlalchemy import select
            from bots.weather.engine.base_engine.data.database import ExecutionQuality
            
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(ExecutionQuality).where(
                        ExecutionQuality.market_id == market_id
                    ).order_by(ExecutionQuality.executed_at.desc()).limit(100)
                )
                executions = result.scalars().all()
            
            if not executions:
                return {'avg_slippage': 0, 'count': 0}
            
            slippages = [e.slippage for e in executions]
            
            return {
                'avg_slippage': sum(slippages) / len(slippages),
                'median_slippage': sorted(slippages)[len(slippages) // 2],
                'count': len(slippages)
            }
            
        except Exception as e:
            logger.warning(f"Failed to get market execution quality: {e}")
            return {}
    
    def get_recent_executions(self, bot_name: str, limit: int = 10) -> List[Dict]:
        """Get recent executions from memory."""
        return self.in_memory_quality.get(bot_name, [])[-limit:]
