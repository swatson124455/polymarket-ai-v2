"""
FeatureStore - Pre-compute ML features from market_prices and trades; store in ml_features.
Speeds up backtesting and training by computing once and reading from store.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
from structlog import get_logger

logger = get_logger()


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class FeatureStore:
    """
    Pre-compute features (momentum, volatility, volume, etc.) from market_prices and trades;
    upsert into ml_features for fast backtesting and optional PredictionEngine use.
    """

    def __init__(self, db: Any):
        self.db = db

    async def compute_market_features(
        self, market_id: str, lookback_days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Compute features for one market from market_prices and trades; upsert to ml_features.
        Returns the computed features dict or None on failure.
        """
        if not self.db or not self.db.session_factory:
            return None
        try:
            now = datetime.now(timezone.utc)
            since = now - timedelta(days=lookback_days)
            since_24h = now - timedelta(hours=24)
            prices = await self.db.get_prices_for_market_since(
                market_id=market_id, since=since, limit=5000
            )
            trade_vol_24h = await self.db.get_trade_volume_by_market_since(since=since_24h)
            market_basic = await self.db.get_market_basic(market_id)
        except Exception as e:
            logger.warning("FeatureStore: failed to load data for %s: %s", market_id, e)
            return None

        features: Dict[str, Any] = {}
        now = datetime.now(timezone.utc)
        now_naive = _naive_utc(now)

        if not prices:
            features["price_current"] = 0.5
            features["momentum_1h"] = features["momentum_4h"] = features["momentum_24h"] = 0.0
            features["volatility_7d"] = 0.0
            features["price_max_7d"] = features["price_min_7d"] = 0.5
        else:
            prices_sorted = sorted(
                prices,
                key=lambda x: x["timestamp"] if x.get("timestamp") else datetime.min,
            )
            pr = [float(p.get("price", 0.5)) for p in prices_sorted if p.get("price") is not None]
            if not pr:
                pr = [0.5]
            features["price_current"] = float(pr[-1])
            features["price_max_7d"] = float(max(pr[-168:]) if len(pr) >= 168 else max(pr))
            features["price_min_7d"] = float(min(pr[-168:]) if len(pr) >= 168 else min(pr))
            features["volatility_7d"] = float(np.std(pr[-168:])) if len(pr) >= 2 else 0.0
            n = len(pr)
            def _momentum(hours: int) -> float:
                idx = max(0, n - max(1, hours))
                if idx >= n - 1 or pr[idx] == 0:
                    return 0.0
                return float((pr[-1] - pr[idx]) / pr[idx])
            features["momentum_1h"] = _momentum(1)
            features["momentum_4h"] = _momentum(4)
            features["momentum_24h"] = _momentum(24)

        vol_info = trade_vol_24h.get(market_id) or {}
        features["volume_24h"] = float(vol_info.get("volume_usd", 0.0))
        features["trade_count_24h"] = int(vol_info.get("count", 0))

        if market_basic and market_basic.get("end_date_iso"):
            end = market_basic["end_date_iso"]
            if hasattr(end, "tzinfo") and end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            try:
                delta = end - now if hasattr(end, "__sub__") else None
                features["days_until_close"] = (
                    delta.total_seconds() / 86400.0 if delta is not None else 999.0
                )
            except Exception:
                features["days_until_close"] = 999.0
        else:
            features["days_until_close"] = 999.0

        computed_at = now_naive or _naive_utc(now)
        try:
            await self.db.upsert_ml_features(
                market_id=market_id, computed_at=computed_at, features=features
            )
        except Exception as e:
            logger.warning("FeatureStore: upsert_ml_features failed for %s: %s", market_id, e)
            return None
        return features

    async def bulk_compute_features(
        self, batch_size: int = 10, limit_markets: int = 500
    ) -> int:
        """
        Compute features for all active markets (by liquidity/volume). Returns count computed.
        """
        if not self.db or not self.db.session_factory:
            return 0
        try:
            markets = await self.db.get_softest_markets(limit=limit_markets)
        except Exception as e:
            logger.warning("FeatureStore: get_softest_markets failed: %s", e)
            return 0
        if not markets:
            return 0
        market_ids = [str(m["id"]) for m in markets if m.get("id") is not None]
        count = 0
        for i, market_id in enumerate(market_ids):
            try:
                await self.compute_market_features(market_id)
                count += 1
                if (i + 1) % batch_size == 0:
                    logger.info("FeatureStore: processed %s/%s markets", i + 1, len(market_ids))
            except Exception as e:
                logger.warning("FeatureStore: compute failed for %s: %s", market_id, e)
        logger.info("FeatureStore: bulk compute complete", computed=count, total=len(market_ids))
        return count
