"""
Feature Engineering Automation - Auto-generate and select optimal features.

Features:
- Auto-generate features from raw data
- Feature selection (remove irrelevant features)
- Feature interaction discovery
- Time-based feature engineering
"""
from typing import Dict, List, Optional, Any
import numpy as np
from structlog import get_logger

logger = get_logger()


class FeatureEngineer:
    """
    Automated feature engineering system.
    
    Generates and selects optimal features for ML models.
    """
    
    def __init__(self):
        self.feature_cache: Dict[str, List[float]] = {}
        self.feature_importance: Dict[str, float] = {}
    
    def generate_features(
        self,
        market_data: Dict[str, Any],
        price_history: List[float]
    ) -> Dict[str, float]:
        """
        Generate features from market data and price history.
        
        Args:
            market_data: Market metadata
            price_history: Historical prices
        
        Returns:
            Dictionary of feature names to values
        """
        features = {}
        
        if not price_history or len(price_history) < 2:
            return features
        
        # Price-based features
        features["current_price"] = price_history[-1]
        features["price_change"] = price_history[-1] - price_history[0] if len(price_history) > 1 else 0.0
        features["price_change_pct"] = (
            (price_history[-1] - price_history[0]) / price_history[0]
            if price_history[0] > 0 and len(price_history) > 1 else 0.0
        )
        
        # Volatility features
        if len(price_history) >= 2:
            denom = np.array(price_history[:-1], dtype=float)
            denom = np.where(denom == 0, 1e-10, denom)  # avoid divide-by-zero → NaN
            returns = np.diff(price_history) / denom
            # Clamp extreme returns to prevent inf/nan propagation
            returns = np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)
            features["volatility"] = float(np.std(returns)) if len(returns) > 0 else 0.0
            features["mean_return"] = float(np.mean(returns)) if len(returns) > 0 else 0.0
        
        # Moving averages
        if len(price_history) >= 5:
            features["ma_5"] = float(np.mean(price_history[-5:]))
        if len(price_history) >= 10:
            features["ma_10"] = float(np.mean(price_history[-10:]))
        if len(price_history) >= 20:
            features["ma_20"] = float(np.mean(price_history[-20:]))
        
        # Price position features
        if len(price_history) >= 20:
            recent_prices = price_history[-20:]
            features["price_percentile"] = float(
                np.sum(np.array(recent_prices) <= price_history[-1]) / len(recent_prices)
            )
            features["price_vs_high"] = (
                price_history[-1] / max(recent_prices) if max(recent_prices) > 0 else 1.0
            )
            features["price_vs_low"] = (
                price_history[-1] / min(recent_prices) if min(recent_prices) > 0 else 1.0
            )
        
        # Market metadata features
        if market_data:
            features["liquidity"] = float(market_data.get("liquidity", 0.0))
            features["volume"] = float(market_data.get("volume", 0.0))
            features["has_category"] = 1.0 if market_data.get("category") else 0.0
        
        return features
    
    def select_features(
        self,
        features: Dict[str, float],
        feature_importance: Optional[Dict[str, float]] = None,
        min_importance: float = 0.01
    ) -> Dict[str, float]:
        """
        Select important features.
        
        Args:
            features: All features
            feature_importance: Feature importance scores
            min_importance: Minimum importance threshold
        
        Returns:
            Selected features
        """
        if feature_importance is None:
            feature_importance = self.feature_importance
        
        if not feature_importance:
            # No importance data, return all features
            return features
        
        selected = {
            name: value
            for name, value in features.items()
            if feature_importance.get(name, 0.0) >= min_importance
        }
        
        return selected
    
    def discover_feature_interactions(
        self,
        features: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """
        Discover feature interactions.
        
        Args:
            features: Feature dictionary
        
        Returns:
            List of discovered interactions
        """
        interactions = []
        
        feature_names = list(features.keys())
        
        # Generate pairwise interactions
        for i, name1 in enumerate(feature_names):
            for name2 in feature_names[i+1:]:
                value1 = features.get(name1, 0.0)
                value2 = features.get(name2, 0.0)
                
                # Create interaction features
                interaction = {
                    "feature1": name1,
                    "feature2": name2,
                    "product": value1 * value2,
                    "ratio": value1 / value2 if value2 != 0 else 0.0,
                    "sum": value1 + value2,
                    "difference": abs(value1 - value2)
                }
                
                interactions.append(interaction)
        
        return interactions
    
    def update_feature_importance(
        self,
        feature_importance: Dict[str, float]
    ):
        """Update feature importance scores."""
        self.feature_importance.update(feature_importance)
    
    def get_feature_importance(self) -> Dict[str, float]:
        """Get current feature importance scores."""
        return self.feature_importance.copy()


# ── L8: Technical Analysis helpers ──────────────────────────────────────

def compute_rsi(prices: List[float], period: int = 14) -> float:
    """
    Compute Relative Strength Index (RSI) from price series.

    RSI = 100 - (100 / (1 + RS)), where RS = avg_gain / avg_loss over `period`.
    Returns 0.5 (neutral) when insufficient data.
    """
    if not prices or len(prices) < period + 1:
        return 0.5  # neutral default
    try:
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        # Use last `period` deltas
        recent = deltas[-period:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0
        if avg_loss == 0:
            return 1.0 if avg_gain > 0 else 0.5
        rs = avg_gain / avg_loss
        rsi = 1.0 - (1.0 / (1.0 + rs))  # normalized to [0, 1]
        return max(0.0, min(1.0, rsi))
    except Exception:
        return 0.5


def compute_bollinger_position(prices: List[float], period: int = 20) -> float:
    """
    Compute where the current price sits within the Bollinger Bands.

    Returns (price - lower_band) / (upper_band - lower_band), normalized to [0, 1].
    0.5 = at the middle band (SMA), 0 = at lower band, 1 = at upper band.
    Returns 0.5 (neutral) when insufficient data.
    """
    if not prices or len(prices) < period:
        return 0.5
    try:
        window = prices[-period:]
        sma = sum(window) / len(window)
        std_dev = float(np.std(window))
        if std_dev < 1e-10:
            return 0.5
        upper = sma + 2 * std_dev
        lower = sma - 2 * std_dev
        band_width = upper - lower
        if band_width < 1e-10:
            return 0.5
        position = (prices[-1] - lower) / band_width
        return max(0.0, min(1.0, position))
    except Exception:
        return 0.5


def compute_atr_normalized(prices: List[float], period: int = 14) -> float:
    """
    Compute normalized Average True Range (ATR).

    In prediction markets, we approximate TR as |high - low| per period window,
    since we only have close prices. We use |max - min| over rolling windows.
    Returns ATR / mean_price (normalized), clamped to [0, 1].
    Returns 0.0 when insufficient data.
    """
    if not prices or len(prices) < period + 1:
        return 0.0
    try:
        # Compute true ranges using rolling windows
        trs = []
        for i in range(1, len(prices)):
            tr = abs(prices[i] - prices[i - 1])
            trs.append(tr)
        if len(trs) < period:
            return 0.0
        # Use last `period` true ranges
        atr = sum(trs[-period:]) / period
        mean_price = sum(prices[-period:]) / period
        if mean_price < 1e-10:
            return 0.0
        normalized = atr / mean_price
        return max(0.0, min(1.0, normalized))
    except Exception:
        return 0.0
