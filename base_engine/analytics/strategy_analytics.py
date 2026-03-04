"""
Strategy Performance Analytics

Comprehensive analytics for trading strategies including:
- Real-time P&L tracking per strategy
- Win rate, Sharpe ratio, max drawdown
- Strategy comparison
- Automated strategy ranking
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import math
from structlog import get_logger
from base_engine.data.database import Database, Position, Trade
from config.settings import settings

logger = get_logger()


class StrategyAnalytics:
    """
    Comprehensive analytics for trading strategies.
    
    Metrics tracked:
    - Total P&L
    - Win rate
    - Sharpe ratio
    - Max drawdown
    - Average win/loss
    - Profit factor
    - Strategy ranking
    """
    
    def __init__(self, db: Database):
        self.db = db
        self.analytics_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl = 60  # 1 minute cache
    
    async def get_strategy_performance(
        self,
        bot_name: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get comprehensive performance metrics for a strategy.
        
        Args:
            bot_name: Name of the bot/strategy
            days: Number of days to analyze
            
        Returns:
            Dictionary with performance metrics
        """
        cache_key = f"{bot_name}:{days}"
        if cache_key in self.analytics_cache:
            cached = self.analytics_cache[cache_key]
            age = (datetime.now(timezone.utc) - cached.get("timestamp", datetime.min.replace(tzinfo=timezone.utc))).total_seconds()
            if age < self.cache_ttl:
                return cached.get("data")
        
        if not self.db or not self.db.session_factory:
            return self._empty_metrics()
        
        try:
            from sqlalchemy import select, func, and_
            from datetime import datetime, timedelta, timezone
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            async with self.db.get_session() as session:
                # Get all positions for this bot (prefer source_bot for per-bot P&L attribution)
                positions_query = select(Position).where(
                    and_(
                        (Position.bot_id == bot_name) | (Position.source_bot == bot_name),
                        Position.opened_at >= cutoff_date
                    )
                )
                positions_result = await session.execute(positions_query)
                positions = positions_result.scalars().all()
                
                # Get all trades for this bot (prefer bot_id; fallback user_address for legacy)
                trades_query = select(Trade).where(
                    and_(
                        (Trade.bot_id == bot_name) | (Trade.user_address == bot_name),
                        Trade.timestamp >= cutoff_date
                    )
                )
                trades_result = await session.execute(trades_query)
                trades = trades_result.scalars().all()
                
                metrics = self._calculate_metrics(positions, trades, days)
                
                # Cache result
                self.analytics_cache[cache_key] = {
                    "data": metrics,
                    "timestamp": datetime.now(timezone.utc)
                }
                
                return metrics
        except Exception as e:
            logger.error(f"Error calculating strategy performance: {str(e)}", exc_info=True)
            return self._empty_metrics()
    
    def _calculate_metrics(
        self,
        positions: List[Position],
        trades: List[Trade],
        days: int
    ) -> Dict[str, Any]:
        """Calculate performance metrics from positions and trades."""
        if not positions and not trades:
            return self._empty_metrics()
        
        # Calculate P&L
        total_pnl = sum(p.unrealized_pnl or 0 for p in positions if p.unrealized_pnl)
        total_pnl += sum((t.price * t.size) if t.side == "BUY" else -(t.price * t.size) for t in trades)
        
        # Calculate win/loss
        winning_trades = [p for p in positions if p.unrealized_pnl and p.unrealized_pnl > 0]
        losing_trades = [p for p in positions if p.unrealized_pnl and p.unrealized_pnl < 0]
        
        win_rate = len(winning_trades) / len(positions) if positions else 0.0
        
        avg_win = sum(p.unrealized_pnl for p in winning_trades) / len(winning_trades) if winning_trades else 0.0
        avg_loss = abs(sum(p.unrealized_pnl for p in losing_trades) / len(losing_trades)) if losing_trades else 0.0
        
        profit_factor = avg_win / avg_loss if avg_loss > 0 else (avg_win if avg_win > 0 else 0.0)
        
        # Calculate Sharpe ratio (simplified)
        returns = [p.unrealized_pnl for p in positions if p.unrealized_pnl]
        if len(returns) > 1:
            mean_return = sum(returns) / len(returns)
            variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
            std_dev = math.sqrt(variance) if variance > 0 else 0.0
            sharpe_ratio = (mean_return / std_dev) if std_dev > 0 else 0.0
        else:
            sharpe_ratio = 0.0
        
        # Calculate max drawdown
        cumulative_pnl = 0
        peak = 0
        max_drawdown = 0
        for p in sorted(positions, key=lambda x: x.opened_at or getattr(x, "timestamp", None) or datetime.min.replace(tzinfo=timezone.utc)):
            cumulative_pnl += p.unrealized_pnl or 0
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = peak - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        return {
            "bot_name": (getattr(positions[0], "source_bot", None) or positions[0].bot_id or positions[0].bot_name) if positions else "unknown",
            "period_days": days,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "total_trades": len(positions),
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def _empty_metrics(self) -> Dict[str, Any]:
        """Return empty metrics structure."""
        return {
            "bot_name": "unknown",
            "period_days": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def compare_strategies(
        self,
        bot_names: List[str],
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Compare multiple strategies.
        
        Args:
            bot_names: List of bot names to compare
            days: Number of days to analyze
            
        Returns:
            List of performance metrics, sorted by total P&L
        """
        results = []
        for bot_name in bot_names:
            metrics = await self.get_strategy_performance(bot_name, days)
            results.append(metrics)
        
        # Sort by total P&L (descending)
        results.sort(key=lambda x: x.get("total_pnl", 0), reverse=True)
        
        # Add ranking
        for i, result in enumerate(results):
            result["rank"] = i + 1
        
        return results
    
    async def get_top_strategies(
        self,
        limit: int = 10,
        days: int = 30,
        metric: str = "total_pnl"
    ) -> List[Dict[str, Any]]:
        """
        Get top performing strategies.
        
        Args:
            limit: Number of top strategies to return
            days: Number of days to analyze
            metric: Metric to rank by (total_pnl, sharpe_ratio, win_rate)
            
        Returns:
            List of top strategies
        """
        # Get all bot names from database
        if not self.db or not self.db.session_factory:
            return []
        
        try:
            from sqlalchemy import select, distinct
            
            async with self.db.get_session() as session:
                # Include both bot_id and source_bot for per-bot P&L attribution
                q1 = select(distinct(Position.bot_id)).where(Position.bot_id.isnot(None))
                q2 = select(distinct(Position.source_bot)).where(Position.source_bot.isnot(None))
                r1 = await session.execute(q1)
                r2 = await session.execute(q2)
                bot_names = list({row[0] for row in r1 if row[0]} | {row[0] for row in r2 if row[0]})
            
            # Get performance for all bots
            all_performance = await self.compare_strategies(bot_names, days)
            
            # Sort by specified metric
            if metric == "sharpe_ratio":
                all_performance.sort(key=lambda x: x.get("sharpe_ratio", 0), reverse=True)
            elif metric == "win_rate":
                all_performance.sort(key=lambda x: x.get("win_rate", 0), reverse=True)
            else:  # total_pnl
                all_performance.sort(key=lambda x: x.get("total_pnl", 0), reverse=True)
            
            return all_performance[:limit]
        except Exception as e:
            logger.error(f"Error getting top strategies: {str(e)}", exc_info=True)
            return []
