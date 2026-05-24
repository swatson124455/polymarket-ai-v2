"""
Trade Flow Analyzer
==================
Analyzes individual trades for smart money signals.
Detects volume acceleration, buy/sell pressure, large trades.
"""
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
import statistics
from structlog import get_logger

logger = get_logger()


class TradeFlowAnalyzer:
    """
    Analyzes trade-by-trade data for signals.
    Detects volume acceleration, buy/sell pressure, large trades.
    """
    
    def __init__(self, db, client):
        self.db = db
        self.client = client
    
    async def analyze_recent_trades(
        self,
        token_id: str,
        minutes: int = 60
    ) -> Dict:
        """
        Analyze recent trades for a token.
        
        Args:
            token_id: CLOB token ID
            minutes: Lookback window in minutes
        
        Returns:
            Dict with:
                - total_volume
                - buy_volume
                - sell_volume
                - large_trades: List of trades > threshold
                - trade_count
                - avg_trade_size
                - volume_weighted_price (VWAP)
                - buy_sell_ratio
                - acceleration: Volume acceleration metric
        """
        try:
            # Get recent trades from database
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            
            async with self.db.get_session() as session:
                from sqlalchemy import select, and_
                from bots.weather.engine.base_engine.data.database import Trade
                
                result = await session.execute(
                    select(Trade).where(
                        and_(
                            Trade.token_id == token_id,
                            Trade.timestamp >= cutoff
                        )
                    ).order_by(Trade.timestamp.desc())
                )
                trades = result.scalars().all()
            
            if not trades:
                return {
                    "token_id": token_id,
                    "total_volume": 0,
                    "buy_volume": 0,
                    "sell_volume": 0,
                    "large_trades": [],
                    "trade_count": 0,
                    "avg_trade_size": 0,
                    "volume_weighted_price": 0,
                    "buy_sell_ratio": 0,
                    "acceleration": 0
                }
            
            # Convert to dict format
            trade_list = [
                {
                    "side": trade.side,
                    "size": trade.size,
                    "price": trade.price,
                    "timestamp": trade.timestamp
                }
                for trade in trades
            ]
            
            # Calculate metrics
            total_volume = sum(t["size"] for t in trade_list)
            buy_volume = sum(t["size"] for t in trade_list if t["side"] == "BUY")
            sell_volume = sum(t["size"] for t in trade_list if t["side"] == "SELL")
            
            # Large trades (top 10% by size)
            sorted_by_size = sorted(trade_list, key=lambda t: t["size"], reverse=True)
            large_threshold = sorted_by_size[len(sorted_by_size) // 10]["size"] if sorted_by_size else 0
            large_trades = [t for t in trade_list if t["size"] >= large_threshold]
            
            # VWAP
            vwap = self._vwap(trade_list)
            
            # Buy/sell ratio
            buy_sell_ratio = buy_volume / sell_volume if sell_volume > 0 else (buy_volume if buy_volume > 0 else 0)
            
            # Acceleration
            acceleration = self._volume_acceleration(trade_list, minutes)
            
            return {
                "token_id": token_id,
                "total_volume": total_volume,
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "large_trades": large_trades[:10],  # Top 10
                "trade_count": len(trade_list),
                "avg_trade_size": statistics.mean(t["size"] for t in trade_list) if trade_list else 0,
                "volume_weighted_price": vwap,
                "buy_sell_ratio": buy_sell_ratio,
                "acceleration": acceleration,
                "large_trade_threshold": large_threshold
            }
            
        except Exception as e:
            logger.warning(f"Failed to analyze trade flow for {token_id}: {str(e)}")
            return {
                "token_id": token_id,
                "error": str(e)
            }
    
    def _vwap(self, trades: List[Dict]) -> float:
        """
        Calculate Volume Weighted Average Price.
        
        Args:
            trades: List of {price, size} dicts
        
        Returns:
            VWAP
        """
        if not trades:
            return 0.0
        
        total_value = sum(t["price"] * t["size"] for t in trades)
        total_volume = sum(t["size"] for t in trades)
        
        return total_value / total_volume if total_volume > 0 else 0.0
    
    def _volume_acceleration(self, trades: List[Dict], window_minutes: int) -> float:
        """
        Detect if trading is accelerating (often precedes big moves).
        Compare recent 25% of window to prior 75%.
        
        Args:
            trades: List of trades
            window_minutes: Total window size
        
        Returns:
            Acceleration ratio (>1 = accelerating, <1 = decelerating)
        """
        if len(trades) < 4:
            return 0.0
        
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(minutes=window_minutes * 0.25)
        earlier_cutoff = now - timedelta(minutes=window_minutes * 0.75)

        def _aware(ts):
            """Ensure timestamp is timezone-aware for comparison."""
            if ts is not None and getattr(ts, "tzinfo", None) is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts

        recent = [t for t in trades if _aware(t["timestamp"]) >= recent_cutoff]
        earlier = [t for t in trades if earlier_cutoff <= _aware(t["timestamp"]) < recent_cutoff]
        
        recent_volume = sum(t["size"] for t in recent)
        earlier_volume = sum(t["size"] for t in earlier)
        
        recent_time = (window_minutes * 0.25) * 60  # Convert to seconds
        earlier_time = (window_minutes * 0.5) * 60  # Middle 50% of window
        
        recent_rate = recent_volume / recent_time if recent_time > 0 else 0
        earlier_rate = earlier_volume / earlier_time if earlier_time > 0 else 0
        
        if earlier_rate == 0:
            return 0.0
        
        return recent_rate / earlier_rate

    def calculate_vpin(self, trades: List[Dict], n_buckets: int = 50) -> float:
        """
        Volume-Synchronized Probability of Informed Trading (VPIN).
        VPIN = (1/n) * Σ|V_buy - V_sell| / bucket_volume

        Higher VPIN = more toxic flow (informed traders active).
        Returns 0-1, where >0.7 is toxic. Returns 0 if insufficient data.
        """
        if not trades or len(trades) < n_buckets:
            return 0.0
        total_vol = sum(t.get("size", 0) for t in trades)
        if total_vol <= 0:
            return 0.0
        bucket_vol = total_vol / n_buckets
        if bucket_vol <= 0:
            return 0.0

        vpin_sum = 0.0
        bucket_buy = 0.0
        bucket_sell = 0.0
        bucket_filled = 0.0
        buckets_completed = 0

        for t in trades:
            size = t.get("size", 0)
            side = t.get("side", "BUY")
            remaining = size
            while remaining > 0 and buckets_completed < n_buckets:
                space = bucket_vol - bucket_filled
                fill = min(remaining, space)
                if side == "BUY":
                    bucket_buy += fill
                else:
                    bucket_sell += fill
                bucket_filled += fill
                remaining -= fill
                if bucket_filled >= bucket_vol - 1e-10:
                    vpin_sum += abs(bucket_buy - bucket_sell)
                    buckets_completed += 1
                    bucket_buy = 0.0
                    bucket_sell = 0.0
                    bucket_filled = 0.0

        if buckets_completed == 0:
            return 0.0
        return round(vpin_sum / (buckets_completed * bucket_vol), 4)

    async def get_vpin(self, token_id: str, minutes: int = 60) -> Dict:
        """Get VPIN toxicity metric for a token."""
        analysis = await self.analyze_recent_trades(token_id, minutes=minutes)
        if "error" in analysis or analysis.get("trade_count", 0) < 20:
            return {"vpin": 0.0, "toxic": False, "trade_count": analysis.get("trade_count", 0)}
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            async with self.db.get_session() as session:
                from sqlalchemy import select, and_
                from bots.weather.engine.base_engine.data.database import Trade
                result = await session.execute(
                    select(Trade).where(
                        and_(Trade.token_id == token_id, Trade.timestamp >= cutoff)
                    ).order_by(Trade.timestamp.asc())
                )
                trades = [{"side": t.side, "size": t.size, "price": t.price} for t in result.scalars().all()]
        except Exception:
            trades = []
        vpin = self.calculate_vpin(trades)
        return {"vpin": vpin, "toxic": vpin > 0.7, "trade_count": len(trades)}

    async def get_flow_signal(self, token_id: str) -> Optional[Dict]:
        """
        Get trading signal from trade flow analysis.
        
        Returns:
            Dict with signal direction and confidence, or None
        """
        analysis = await self.analyze_recent_trades(token_id, minutes=60)
        
        if "error" in analysis:
            return None
        
        # Strong buy pressure
        if analysis["buy_sell_ratio"] > 2.0 and analysis["acceleration"] > 1.5:
            return {
                "direction": "bullish",
                "confidence": min(analysis["buy_sell_ratio"] / 3.0, 1.0),
                "reason": "trade_flow",
                "token_id": token_id,
                "metrics": {
                    "buy_sell_ratio": analysis["buy_sell_ratio"],
                    "acceleration": analysis["acceleration"]
                }
            }
        
        # Strong sell pressure
        elif analysis["buy_sell_ratio"] < 0.5 and analysis["acceleration"] > 1.5:
            return {
                "direction": "bearish",
                "confidence": min((1.0 / analysis["buy_sell_ratio"]) / 3.0, 1.0),
                "reason": "trade_flow",
                "token_id": token_id,
                "metrics": {
                    "buy_sell_ratio": analysis["buy_sell_ratio"],
                    "acceleration": analysis["acceleration"]
                }
            }
        
        return None
