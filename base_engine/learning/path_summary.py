"""
Path summary and regime features for prediction training.

Provides:
- get_path_summary_from_prices: summary stats from a price path (min, max, vol, drawdown, etc.)
- get_regime_features_from_prices: trend/vol features from a price series
- get_path_summary: async load prices from DB and return path summary
- path_summary_to_feature_list / regime_features_to_list: fixed-order feature vectors
"""
from typing import Dict, List, Optional
import math
from structlog import get_logger

logger = get_logger()

PATH_SUMMARY_DEFAULTS = {
    "path_min": 0.0,
    "path_max": 0.0,
    "path_final": 0.0,
    "path_vol": 0.0,
    "path_drawdown": 0.0,
    "time_above_entry": 0.5,
    "max_run_up": 0.0,
    "max_run_down": 0.0,
}
REGIME_DEFAULTS = {"regime_trend": 0.0, "regime_vol": 0.0}

PATH_SUMMARY_KEYS = [
    "path_min", "path_max", "path_final", "path_vol", "path_drawdown",
    "time_above_entry", "max_run_up", "max_run_down",
]


def get_path_summary_from_prices(
    prices: List[float],
    entry_price: float,
) -> Optional[Dict[str, float]]:
    """Compute path summary from ordered prices. Returns None if len(prices) < 2."""
    if not prices or len(prices) < 2:
        return None
    try:
        arr = [float(p) for p in prices]
    except (TypeError, ValueError):
        return None
    path_min = min(arr)
    path_max = max(arr)
    path_final = arr[-1]
    entry = float(entry_price)
    if entry <= 0 or entry > 1:
        entry = arr[0]
    returns = [(arr[i] - arr[i - 1]) / arr[i - 1] for i in range(1, len(arr)) if arr[i - 1] > 0]
    path_vol = float(math.sqrt(sum(r * r for r in returns) / len(returns))) if returns else 0.0
    peak = arr[0]
    max_dd = 0.0
    for p in arr:
        if p > peak:
            peak = p
        dd = (peak - p) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    above = sum(1 for p in arr if p >= entry)
    time_above_entry = above / len(arr) if arr else 0.5
    return {
        "path_min": path_min,
        "path_max": path_max,
        "path_final": path_final,
        "path_vol": path_vol,
        "path_drawdown": max_dd,
        "time_above_entry": time_above_entry,
        "max_run_up": max(0.0, path_max - entry),
        "max_run_down": max(0.0, entry - path_min),
    }


def get_regime_features_from_prices(prices: List[float]) -> Dict[str, float]:
    """Regime-like features: regime_trend (~[-1,1]), regime_vol (~[0,1])."""
    if not prices or len(prices) < 2:
        return dict(REGIME_DEFAULTS)
    try:
        arr = [float(p) for p in prices]
    except (TypeError, ValueError):
        return dict(REGIME_DEFAULTS)
    returns = [(arr[i] - arr[i - 1]) / arr[i - 1] for i in range(1, len(arr)) if arr[i - 1] > 0]
    if not returns:
        return dict(REGIME_DEFAULTS)
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    vol = math.sqrt(variance) if variance > 0 else 0.0
    trend = max(-1.0, min(1.0, mean_return * 50.0))
    regime_vol = min(1.0, vol * 10.0)
    return {"regime_trend": trend, "regime_vol": regime_vol}


async def get_path_summary(
    session,
    market_id: str,
    token_id: str,
    start_dt,
    end_dt,
    entry_price: float,
) -> Optional[Dict[str, float]]:
    """Load price path from DB and return path summary. Returns None if no prices."""
    from sqlalchemy import select, and_
    from base_engine.data.database import MarketPrice

    if not token_id:
        return None
    result = await session.execute(
        select(MarketPrice.price).where(
            and_(
                MarketPrice.market_id == market_id,
                MarketPrice.token_id == token_id,
                MarketPrice.timestamp >= start_dt,
                MarketPrice.timestamp <= end_dt,
            )
        ).order_by(MarketPrice.timestamp)
    )
    rows = result.fetchall()
    if not rows:
        return None
    prices = [float(r[0]) for r in rows]
    return get_path_summary_from_prices(prices, entry_price)


def path_summary_to_feature_list(summary: Optional[Dict[str, float]]) -> List[float]:
    """Fixed-order list for path summary. Uses defaults if None."""
    if not summary:
        return [PATH_SUMMARY_DEFAULTS[k] for k in PATH_SUMMARY_KEYS]
    return [summary.get(k, PATH_SUMMARY_DEFAULTS[k]) for k in PATH_SUMMARY_KEYS]


def regime_features_to_list(regime: Dict[str, float]) -> List[float]:
    """[regime_trend, regime_vol]."""
    return [
        regime.get("regime_trend", REGIME_DEFAULTS["regime_trend"]),
        regime.get("regime_vol", REGIME_DEFAULTS["regime_vol"]),
    ]
