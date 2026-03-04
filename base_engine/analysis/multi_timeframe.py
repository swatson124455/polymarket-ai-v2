"""
Multi-Timeframe Analysis - Analyze multiple timeframes simultaneously.

Features:
- Short-term (minutes)
- Medium-term (hours)
- Long-term (days)
- Multi-timeframe signals
- Timeframe alignment
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from enum import Enum
from structlog import get_logger
from base_engine.data.database import Database

logger = get_logger()


class Timeframe(Enum):
    """Timeframe types."""
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"
    WEEK_1 = "1w"


class MultiTimeframeAnalyzer:
    """
    Analyze markets across multiple timeframes.
    
    Provides signals from different timeframes and combines them.
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
        self.timeframes = [
            Timeframe.MINUTE_15,
            Timeframe.HOUR_1,
            Timeframe.HOUR_4,
            Timeframe.DAY_1
        ]
    
    async def analyze_multi_timeframe(
        self,
        market_id: str,
        timeframes: Optional[List[Timeframe]] = None
    ) -> Dict[str, Any]:
        """
        Analyze market across multiple timeframes.
        
        Args:
            market_id: Market ID
            timeframes: Optional list of timeframes (defaults to self.timeframes)
        
        Returns:
            Multi-timeframe analysis
        """
        if timeframes is None:
            timeframes = self.timeframes
        
        if not self.db or not self.db.session_factory:
            return {
                "error": "Database not available",
                "timeframes": {}
            }
        
        async with self.db.get_session() as session:
            from sqlalchemy import text
            
            timeframe_analysis = {}
            
            for timeframe in timeframes:
                analysis = await self._analyze_timeframe(session, market_id, timeframe)
                timeframe_analysis[timeframe.value] = analysis
            
            # Combine signals
            combined_signal = self._combine_signals(timeframe_analysis)
            
            return {
                "market_id": market_id,
                "timeframes": timeframe_analysis,
                "combined_signal": combined_signal,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def _analyze_timeframe(
        self,
        session,
        market_id: str,
        timeframe: Timeframe
    ) -> Dict[str, Any]:
        """Analyze a single timeframe."""
        # Calculate lookback period
        lookback_map = {
            Timeframe.MINUTE_1: timedelta(hours=1),
            Timeframe.MINUTE_5: timedelta(hours=6),
            Timeframe.MINUTE_15: timedelta(days=1),
            Timeframe.HOUR_1: timedelta(days=7),
            Timeframe.HOUR_4: timedelta(days=30),
            Timeframe.DAY_1: timedelta(days=90),
            Timeframe.WEEK_1: timedelta(days=365)
        }
        
        lookback = lookback_map.get(timeframe, timedelta(days=30))
        cutoff_date = datetime.now(timezone.utc) - lookback
        
        # Get price data
        query = text("""
            SELECT price, timestamp
            FROM market_prices
            WHERE market_id = :market_id
            AND timestamp >= :cutoff_date
            ORDER BY timestamp ASC
        """)
        
        result = await session.execute(query, {
            "market_id": market_id,
            "cutoff_date": cutoff_date
        })
        rows = result.fetchall()
        
        if len(rows) < 2:
            return {
                "timeframe": timeframe.value,
                "signal": "neutral",
                "confidence": 0.0,
                "message": "Insufficient data"
            }
        
        prices = [float(row.price) for row in rows]
        
        # Calculate signals
        trend = self._calculate_trend(prices)
        momentum = self._calculate_momentum(prices)
        volatility = self._calculate_volatility(prices)
        
        # Determine signal
        if trend > 0.6 and momentum > 0.5:
            signal = "bullish"
            confidence = (trend + momentum) / 2
        elif trend < -0.6 and momentum < -0.5:
            signal = "bearish"
            confidence = (abs(trend) + abs(momentum)) / 2
        else:
            signal = "neutral"
            confidence = 0.3
        
        return {
            "timeframe": timeframe.value,
            "signal": signal,
            "confidence": round(confidence, 3),
            "metrics": {
                "trend": round(trend, 3),
                "momentum": round(momentum, 3),
                "volatility": round(volatility, 4),
                "data_points": len(prices)
            }
        }
    
    def _calculate_trend(self, prices: List[float]) -> float:
        """Calculate trend (-1.0 to 1.0)."""
        if len(prices) < 2:
            return 0.0
        
        import numpy as np
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        
        # Normalize to -1 to 1
        max_price = max(prices)
        normalized_slope = slope / max_price if max_price > 0 else 0.0
        
        return max(-1.0, min(1.0, normalized_slope * len(prices)))
    
    def _calculate_momentum(self, prices: List[float]) -> float:
        """Calculate momentum (-1.0 to 1.0)."""
        if len(prices) < 10:
            return 0.0
        
        # Compare recent vs older prices
        recent = prices[-5:]
        older = prices[-10:-5] if len(prices) >= 10 else prices[:-5]
        
        if not older:
            return 0.0
        
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        
        if older_avg == 0:
            return 0.0
        
        momentum = (recent_avg - older_avg) / older_avg
        
        return max(-1.0, min(1.0, momentum * 10))
    
    def _calculate_volatility(self, prices: List[float]) -> float:
        """Calculate volatility."""
        if len(prices) < 2:
            return 0.0
        
        import numpy as np
        returns = np.diff(prices) / prices[:-1]
        return float(np.std(returns)) if len(returns) > 0 else 0.0
    
    def _combine_signals(self, timeframe_analysis: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Combine signals from multiple timeframes."""
        signals = []
        confidences = []
        
        for tf, analysis in timeframe_analysis.items():
            signal = analysis.get("signal")
            confidence = analysis.get("confidence", 0.0)
            
            if signal and signal != "neutral":
                signals.append(signal)
                confidences.append(confidence)
        
        if not signals:
            return {
                "signal": "neutral",
                "confidence": 0.0,
                "alignment": "none"
            }
        
        # Count bullish vs bearish
        bullish_count = sum(1 for s in signals if s == "bullish")
        bearish_count = sum(1 for s in signals if s == "bearish")
        
        # Determine combined signal
        if bullish_count > bearish_count:
            combined_signal = "bullish"
            alignment = "bullish" if bullish_count == len(signals) else "mixed"
        elif bearish_count > bullish_count:
            combined_signal = "bearish"
            alignment = "bearish" if bearish_count == len(signals) else "mixed"
        else:
            combined_signal = "neutral"
            alignment = "mixed"
        
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        
        return {
            "signal": combined_signal,
            "confidence": round(avg_confidence, 3),
            "alignment": alignment,
            "timeframe_count": len(signals),
            "bullish_count": bullish_count,
            "bearish_count": bearish_count
        }
