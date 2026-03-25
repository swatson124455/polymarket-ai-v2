"""
Market Regime Detection - Detect market regimes and adapt strategies.

Regimes:
- Bull market
- Bear market
- High volatility
- Low volatility
- Trending
- Mean-reverting
"""
import numpy as np
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from enum import Enum
from structlog import get_logger
from base_engine.data.database import Database

logger = get_logger()


class MarketRegime(Enum):
    """Market regime types."""
    BULL = "bull"
    BEAR = "bear"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    UNKNOWN = "unknown"


class MarketRegimeDetector:
    """
    Detect market regimes and adapt strategies accordingly.
    
    Analyzes price movements, volatility, and trends to identify current regime.
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
        self.regime_history: Dict[str, List[Dict[str, Any]]] = {}
        self.max_history = 1000
    
    async def detect_regime(
        self,
        market_id: str,
        lookback_days: int = 30
    ) -> Dict[str, Any]:
        """
        Detect current market regime.
        
        Args:
            market_id: Market ID
            lookback_days: Number of days to analyze
        
        Returns:
            Dictionary with regime analysis
        """
        if not self.db or not self.db.session_factory:
            return {
                "regime": MarketRegime.UNKNOWN.value,
                "confidence": 0.0,
                "message": "Database not available"
            }
        
        async with self.db.get_session() as session:
            from sqlalchemy import text

            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).replace(tzinfo=None)
            
            # Get price history
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
            
            if len(rows) < 10:
                return {
                    "regime": MarketRegime.UNKNOWN.value,
                    "confidence": 0.0,
                    "message": "Insufficient data for regime detection"
                }
            
            prices = [float(row.price) for row in rows]
            timestamps = [row.timestamp for row in rows]
            
            # Calculate metrics
            price_changes = np.diff(prices)
            returns = np.diff(prices) / prices[:-1]
            
            volatility = np.std(returns) if len(returns) > 0 else 0.0
            mean_return = np.mean(returns) if len(returns) > 0 else 0.0
            
            # Detect trends
            trend_strength = self._calculate_trend_strength(prices)
            
            # Detect mean reversion
            mean_reversion_strength = self._calculate_mean_reversion(prices)
            
            # Determine regime
            regime_scores = {
                MarketRegime.BULL: 0.0,
                MarketRegime.BEAR: 0.0,
                MarketRegime.HIGH_VOLATILITY: 0.0,
                MarketRegime.LOW_VOLATILITY: 0.0,
                MarketRegime.TRENDING: 0.0,
                MarketRegime.MEAN_REVERTING: 0.0
            }
            
            # Bull/Bear detection
            if mean_return > 0.01:  # > 1% average return
                regime_scores[MarketRegime.BULL] = min(1.0, mean_return * 100)
            elif mean_return < -0.01:  # < -1% average return
                regime_scores[MarketRegime.BEAR] = min(1.0, abs(mean_return) * 100)
            
            # Volatility detection
            if volatility > 0.1:  # > 10% volatility
                regime_scores[MarketRegime.HIGH_VOLATILITY] = min(1.0, volatility * 10)
            elif volatility < 0.05:  # < 5% volatility
                regime_scores[MarketRegime.LOW_VOLATILITY] = min(1.0, (0.05 - volatility) * 20)
            
            # Trend detection
            if trend_strength > 0.7:
                regime_scores[MarketRegime.TRENDING] = trend_strength
            elif mean_reversion_strength > 0.7:
                regime_scores[MarketRegime.MEAN_REVERTING] = mean_reversion_strength
            
            # Find dominant regime
            dominant_regime = max(regime_scores.items(), key=lambda x: x[1])
            confidence = dominant_regime[1]
            
            if confidence < 0.3:
                detected_regime = MarketRegime.UNKNOWN
            else:
                detected_regime = dominant_regime[0]
            
            result = {
                "regime": detected_regime.value,
                "confidence": round(confidence, 3),
                "scores": {k.value: round(v, 3) for k, v in regime_scores.items()},
                "metrics": {
                    "volatility": round(volatility, 4),
                    "mean_return": round(mean_return, 4),
                    "trend_strength": round(trend_strength, 3),
                    "mean_reversion_strength": round(mean_reversion_strength, 3),
                    "data_points": len(prices)
                }
            }
            
            # Store in history
            if market_id not in self.regime_history:
                self.regime_history[market_id] = []
            self.regime_history[market_id].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **result
            })
            if len(self.regime_history[market_id]) > self.max_history:
                self.regime_history[market_id].pop(0)
            
            return result
    
    def _calculate_trend_strength(self, prices: List[float]) -> float:
        """Calculate trend strength (0.0 to 1.0)."""
        if len(prices) < 2:
            return 0.0
        
        # Simple linear regression slope
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        
        # Normalize to 0-1
        max_slope = max(abs(slope), 0.01)  # Avoid division by zero
        trend_strength = min(1.0, abs(slope) / max_slope)
        
        return trend_strength
    
    def _calculate_mean_reversion(self, prices: List[float]) -> float:
        """Calculate mean reversion strength (0.0 to 1.0)."""
        if len(prices) < 10:
            return 0.0
        
        # Calculate autocorrelation of returns
        returns = np.diff(prices) / prices[:-1]
        
        if len(returns) < 2:
            return 0.0
        
        # Negative autocorrelation indicates mean reversion
        autocorr = np.corrcoef(returns[:-1], returns[1:])[0, 1]
        
        # Convert to 0-1 scale (negative autocorr = mean reversion)
        mean_reversion_strength = max(0.0, -autocorr) if autocorr < 0 else 0.0
        
        return mean_reversion_strength
    
    def get_regime_history(self, market_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get regime detection history for a market."""
        return self.regime_history.get(market_id, [])[-limit:]
