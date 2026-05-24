"""
Order Flow Analysis - Analyze order flow for better predictions.

Features:
- Order book imbalance analysis
- Large order detection
- Market maker activity tracking
- Flow-based trading signals
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from structlog import get_logger
from bots.weather.engine.base_engine.data.database import Database
from bots.weather.engine.base_engine.data.polymarket_client import PolymarketClient

logger = get_logger()


class OrderFlowAnalyzer:
    """
    Analyze order flow for trading signals.
    
    Analyzes:
    - Order book imbalance
    - Large orders
    - Trade flow
    - Market maker activity
    """
    
    def __init__(
        self,
        db: Optional[Database] = None,
        client: Optional[PolymarketClient] = None
    ):
        self.db = db
        self.client = client
        self.large_order_threshold_usd = 1000.0
    
    async def analyze_order_flow(
        self,
        market_id: str,
        lookback_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        Analyze order flow for a market.
        
        Args:
            market_id: Market ID
            lookback_minutes: Number of minutes to analyze
        
        Returns:
            Order flow analysis
        """
        if not self.db or not self.db.session_factory:
            return {
                "error": "Database not available",
                "signals": {}
            }
        
        async with self.db.get_session() as session:
            from sqlalchemy import text
            
            cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
            
            # Get recent trades
            query = text("""
                SELECT side, size, price, timestamp
                FROM trades
                WHERE market_id = :market_id
                AND timestamp >= :cutoff_time
                ORDER BY timestamp DESC
            """)
            
            result = await session.execute(query, {
                "market_id": market_id,
                "cutoff_time": cutoff_time
            })
            trades = result.fetchall()
            
            if len(trades) < 5:
                return {
                    "signals": {},
                    "message": "Insufficient trade data"
                }
            
            # Analyze flow
            buy_volume = sum(float(t.size) * float(t.price) for t in trades if t.side in ["YES", "BUY"])
            sell_volume = sum(float(t.size) * float(t.price) for t in trades if t.side in ["NO", "SELL"])
            
            total_volume = buy_volume + sell_volume
            buy_ratio = buy_volume / total_volume if total_volume > 0 else 0.5
            
            # Detect large orders
            large_orders = []
            for trade in trades:
                trade_value = float(trade.size) * float(trade.price)
                if trade_value >= self.large_order_threshold_usd:
                    large_orders.append({
                        "side": trade.side,
                        "size": float(trade.size),
                        "price": float(trade.price),
                        "value_usd": trade_value,
                        "timestamp": trade.timestamp.isoformat() if hasattr(trade.timestamp, 'isoformat') else str(trade.timestamp)
                    })
            
            # Calculate flow imbalance
            imbalance = buy_ratio - 0.5  # -0.5 to 0.5
            
            # Generate signals
            signals = {}
            
            if imbalance > 0.2:  # Strong buy flow
                signals["flow_signal"] = "bullish"
                signals["flow_confidence"] = min(1.0, imbalance * 2)
            elif imbalance < -0.2:  # Strong sell flow
                signals["flow_signal"] = "bearish"
                signals["flow_confidence"] = min(1.0, abs(imbalance) * 2)
            else:
                signals["flow_signal"] = "neutral"
                signals["flow_confidence"] = 0.3
            
            if large_orders:
                signals["large_orders_detected"] = True
                signals["large_order_count"] = len(large_orders)
                # Analyze large order direction
                buy_large = sum(1 for o in large_orders if o["side"] in ["YES", "BUY"])
                sell_large = len(large_orders) - buy_large
                
                if buy_large > sell_large:
                    signals["large_order_signal"] = "bullish"
                elif sell_large > buy_large:
                    signals["large_order_signal"] = "bearish"
                else:
                    signals["large_order_signal"] = "neutral"
            else:
                signals["large_orders_detected"] = False
            
            return {
                "market_id": market_id,
                "analysis_period_minutes": lookback_minutes,
                "metrics": {
                    "total_volume_usd": round(total_volume, 2),
                    "buy_volume_usd": round(buy_volume, 2),
                    "sell_volume_usd": round(sell_volume, 2),
                    "buy_ratio": round(buy_ratio, 3),
                    "imbalance": round(imbalance, 3),
                    "trade_count": len(trades),
                    "large_order_count": len(large_orders)
                },
                "signals": signals,
                "large_orders": large_orders[:10],  # Top 10 large orders
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def detect_order_book_imbalance(
        self,
        market_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Detect order book imbalance (requires real-time order book data).
        
        Args:
            market_id: Market ID
        
        Returns:
            Order book imbalance analysis
        """
        # This would require WebSocket order book data
        # Placeholder for future implementation
        return {
            "market_id": market_id,
            "message": "Order book analysis requires real-time WebSocket data",
            "available": False
        }
