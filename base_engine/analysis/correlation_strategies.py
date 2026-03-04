"""
Correlation-Based Strategies - Strategies based on market correlations.

Features:
- Pairs trading
- Market cluster analysis
- Correlation-based entry/exit
- Diversification optimization
"""
import time
import numpy as np
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone, timedelta
from structlog import get_logger
from base_engine.data.database import Database

logger = get_logger()


class CorrelationStrategy:
    """
    Correlation-based trading strategies.
    
    Strategies:
    - Pairs trading (trade correlated markets)
    - Market clusters (group related markets)
    - Correlation-based entry/exit
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
        self.correlation_cache: Dict[Tuple[str, str], Tuple[float, float]] = {}  # (corr_value, timestamp)
        self._cache_max_size = 5000
        self.cache_ttl_days = 7
    
    async def find_correlated_markets(
        self,
        market_id: str,
        min_correlation: float = 0.7,
        lookback_days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Find markets correlated with a given market.
        
        Args:
            market_id: Market ID
            min_correlation: Minimum correlation threshold
            lookback_days: Number of days to analyze
        
        Returns:
            List of correlated markets with correlation scores
        """
        if not self.db or not self.db.session_factory:
            return []
        
        async with self.db.get_session() as session:
            from sqlalchemy import text
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            
            # Get price data for target market
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
            target_prices = [float(row.price) for row in result.fetchall()]
            
            if len(target_prices) < 10:
                return []
            
            # Get all other markets
            query = text("""
                SELECT DISTINCT market_id
                FROM market_prices
                WHERE market_id != :market_id
                AND timestamp >= :cutoff_date
            """)
            
            result = await session.execute(query, {
                "market_id": market_id,
                "cutoff_date": cutoff_date
            })
            other_markets = [row[0] for row in result.fetchall()]
            
            correlated_markets = []
            
            for other_market_id in other_markets:
                # Check cache first (with TTL)
                cache_key = (market_id, other_market_id)
                entry = self.correlation_cache.get(cache_key)
                if entry:
                    val, ts = entry
                    if time.time() - ts > self.cache_ttl_days * 86400:
                        del self.correlation_cache[cache_key]
                        entry = None
                    else:
                        if abs(val) >= min_correlation:
                            correlated_markets.append({
                                "market_id": other_market_id,
                                "correlation": round(val, 3),
                                "strength": "strong" if abs(val) > 0.8 else "moderate"
                            })
                        continue

                # Get price data for other market
                query = text("""
                    SELECT price, timestamp
                    FROM market_prices
                    WHERE market_id = :market_id
                    AND timestamp >= :cutoff_date
                    ORDER BY timestamp ASC
                """)

                result = await session.execute(query, {
                    "market_id": other_market_id,
                    "cutoff_date": cutoff_date
                })
                other_prices = [float(row.price) for row in result.fetchall()]

                if len(other_prices) < 10:
                    continue

                # Calculate correlation
                correlation = self._calculate_correlation(target_prices, other_prices)

                # Store in cache
                self.correlation_cache[cache_key] = (correlation, time.time())
                if len(self.correlation_cache) > self._cache_max_size:
                    # Evict oldest 25%
                    _sorted = sorted(self.correlation_cache, key=lambda k: self.correlation_cache[k][1])
                    for k in _sorted[:len(_sorted) // 4]:
                        del self.correlation_cache[k]

                if abs(correlation) >= min_correlation:
                    correlated_markets.append({
                        "market_id": other_market_id,
                        "correlation": round(correlation, 3),
                        "strength": "strong" if abs(correlation) > 0.8 else "moderate"
                    })
            
            # Sort by absolute correlation
            correlated_markets.sort(key=lambda x: abs(x["correlation"]), reverse=True)

            return correlated_markets

    # ── PCA Factor Decomposition (P5 — Cluster Exposure Limits) ───────────────

    async def compute_pca_factors(
        self,
        market_ids: List[str],
        n_factors: int = 3,
        lookback_days: int = 30,
    ) -> Dict[str, Any]:
        """
        Compute PCA factor decomposition from market price correlations.

        Identifies latent factors (e.g., "Republican sweep", "Democrat sweep")
        from the covariance structure of market returns.

        UCLA/NBER (Chernov, Elenev, Song 2024): voter preferences have a
        two-factor structure. Failing to account for correlations can bias
        win probability by 10+ percentage points.

        Args:
            market_ids: List of market IDs to analyze
            n_factors: Number of principal components to extract (default 3)
            lookback_days: Days of price history to use

        Returns:
            factors: ndarray (n_markets × n_factors) — factor loadings
            explained_variance: list of variance explained per factor
            factor_labels: heuristic labels for each factor
            market_factor_map: dict mapping market_id → primary factor index
        """
        if not self.db or not self.db.session_factory or len(market_ids) < 3:
            return {"factors": None, "explained_variance": [], "market_factor_map": {}}

        async with self.db.get_session() as session:
            from sqlalchemy import text

            cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)

            # Fetch price series for all markets
            all_returns = {}
            for mid in market_ids:
                prices = await self._get_price_series(session, mid, cutoff_date)
                if len(prices) >= 10:
                    returns = np.diff(prices) / np.array(prices[:-1])
                    all_returns[mid] = returns

            if len(all_returns) < 3:
                return {"factors": None, "explained_variance": [], "market_factor_map": {}}

            # Align return series to common length
            min_len = min(len(r) for r in all_returns.values())
            ordered_ids = list(all_returns.keys())
            return_matrix = np.array([all_returns[mid][:min_len] for mid in ordered_ids])

            # Standardize
            means = return_matrix.mean(axis=1, keepdims=True)
            stds = return_matrix.std(axis=1, keepdims=True)
            stds[stds == 0] = 1.0
            standardized = (return_matrix - means) / stds

            # PCA via SVD
            try:
                U, S, Vt = np.linalg.svd(standardized, full_matrices=False)
                n_components = min(n_factors, len(S))
                factor_loadings = U[:, :n_components]
                explained_var = (S[:n_components] ** 2) / (S ** 2).sum()
            except np.linalg.LinAlgError:
                logger.debug("PCA SVD failed — returning empty")
                return {"factors": None, "explained_variance": [], "market_factor_map": {}}

            # Map each market to its primary factor (highest absolute loading)
            market_factor_map = {}
            for i, mid in enumerate(ordered_ids):
                primary_factor = int(np.argmax(np.abs(factor_loadings[i])))
                market_factor_map[mid] = {
                    "primary_factor": primary_factor,
                    "loading": round(float(factor_loadings[i, primary_factor]), 4),
                    "all_loadings": [round(float(x), 4) for x in factor_loadings[i]],
                }

            # Build factor clusters (markets grouped by primary factor)
            factor_clusters = {}
            for mid, info in market_factor_map.items():
                f = info["primary_factor"]
                if f not in factor_clusters:
                    factor_clusters[f] = []
                factor_clusters[f].append(mid)

            logger.info(
                "PCA factors computed",
                n_markets=len(ordered_ids),
                n_factors=n_components,
                explained_variance=[round(float(v), 4) for v in explained_var],
                cluster_sizes={k: len(v) for k, v in factor_clusters.items()},
            )

            return {
                "factors": factor_loadings,
                "explained_variance": [round(float(v), 4) for v in explained_var],
                "market_factor_map": market_factor_map,
                "factor_clusters": factor_clusters,
                "ordered_market_ids": ordered_ids,
            }

    def compute_factor_exposure(
        self,
        positions: List[Dict[str, Any]],
        market_factor_map: Dict[str, Dict[str, Any]],
    ) -> Dict[int, float]:
        """
        Compute total USD exposure per PCA factor.

        Args:
            positions: List of position dicts with market_id, size, price
            market_factor_map: Output from compute_pca_factors()

        Returns:
            Dict mapping factor_index → total USD exposure
        """
        factor_exposure: Dict[int, float] = {}

        for pos in positions:
            mid = pos.get("market_id", "")
            value_usd = float(pos.get("size", 0) or 0) * float(pos.get("price", 0) or 0)
            if mid in market_factor_map:
                factor = market_factor_map[mid]["primary_factor"]
                loading = abs(market_factor_map[mid]["loading"])
                # Exposure weighted by factor loading strength
                factor_exposure[factor] = factor_exposure.get(factor, 0) + value_usd * loading

        return factor_exposure

    def check_factor_limits(
        self,
        factor_exposure: Dict[int, float],
        max_factor_exposure_usd: float = 500.0,
    ) -> Dict[str, Any]:
        """
        Check if any factor exposure exceeds the limit.

        Recommended: 15-20% total bankroll per correlated cluster.

        Returns:
            allowed: bool
            violations: list of factor indices exceeding limit
        """
        violations = []
        for factor_idx, exposure in factor_exposure.items():
            if exposure > max_factor_exposure_usd:
                violations.append({
                    "factor": factor_idx,
                    "exposure_usd": round(exposure, 2),
                    "limit_usd": max_factor_exposure_usd,
                    "excess": round(exposure - max_factor_exposure_usd, 2),
                })

        return {
            "allowed": len(violations) == 0,
            "violations": violations,
            "factor_exposures": {k: round(v, 2) for k, v in factor_exposure.items()},
        }
    
    def _calculate_correlation(
        self,
        prices1: List[float],
        prices2: List[float]
    ) -> float:
        """Calculate correlation between two price series."""
        # Align lengths
        min_len = min(len(prices1), len(prices2))
        if min_len < 10:
            return 0.0
        
        prices1_aligned = prices1[:min_len]
        prices2_aligned = prices2[:min_len]
        
        # Calculate returns
        returns1 = np.diff(prices1_aligned) / prices1_aligned[:-1]
        returns2 = np.diff(prices2_aligned) / prices2_aligned[:-1]
        
        if len(returns1) < 2 or len(returns2) < 2:
            return 0.0
        
        # Calculate correlation
        correlation = np.corrcoef(returns1, returns2)[0, 1]
        
        return float(correlation) if not np.isnan(correlation) else 0.0
    
    async def find_pairs_trading_opportunity(
        self,
        market1_id: str,
        market2_id: str,
        lookback_days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Find pairs trading opportunity between two markets.
        
        Args:
            market1_id: First market ID
            market2_id: Second market ID
            lookback_days: Number of days to analyze
        
        Returns:
            Pairs trading opportunity if found
        """
        if not self.db or not self.db.session_factory:
            return None
        
        async with self.db.get_session() as session:
            from sqlalchemy import text
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            
            # Get prices for both markets
            prices1 = await self._get_price_series(session, market1_id, cutoff_date)
            prices2 = await self._get_price_series(session, market2_id, cutoff_date)
            
            if len(prices1) < 10 or len(prices2) < 10:
                return None
            
            # Calculate spread
            spread = [p1 - p2 for p1, p2 in zip(prices1, prices2)]
            spread_mean = np.mean(spread)
            spread_std = np.std(spread)
            
            current_spread = spread[-1]
            z_score = (current_spread - spread_mean) / spread_std if spread_std > 0 else 0.0
            
            # Pairs trading signal
            # If spread is too wide (z-score > 2), expect mean reversion
            if abs(z_score) > 2.0:
                if z_score > 2.0:
                    # Spread too wide - market1 overvalued relative to market2
                    signal = "short_spread"  # Short market1, long market2
                else:
                    # Spread too narrow - market1 undervalued relative to market2
                    signal = "long_spread"  # Long market1, short market2
                
                return {
                    "market1_id": market1_id,
                    "market2_id": market2_id,
                    "signal": signal,
                    "z_score": round(z_score, 3),
                    "current_spread": round(current_spread, 4),
                    "mean_spread": round(spread_mean, 4),
                    "std_spread": round(spread_std, 4),
                    "confidence": min(1.0, abs(z_score) / 3.0)
                }
            
            return None
    
    async def _get_price_series(
        self,
        session,
        market_id: str,
        cutoff_date: datetime
    ) -> List[float]:
        """Get price series for a market."""
        from sqlalchemy import text
        
        query = text("""
            SELECT price
            FROM market_prices
            WHERE market_id = :market_id
            AND timestamp >= :cutoff_date
            ORDER BY timestamp ASC
        """)
        
        result = await session.execute(query, {
            "market_id": market_id,
            "cutoff_date": cutoff_date
        })
        
        return [float(row.price) for row in result.fetchall()]
    
    async def find_market_clusters(
        self,
        min_correlation: float = 0.7,
        lookback_days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Find clusters of correlated markets.
        
        Args:
            min_correlation: Minimum correlation for cluster membership
            lookback_days: Number of days to analyze
        
        Returns:
            List of market clusters
        """
        if not self.db or not self.db.session_factory:
            return []
        
        async with self.db.get_session() as session:
            from sqlalchemy import text
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            
            # Get all active markets
            query = text("""
                SELECT DISTINCT m.id
                FROM markets m
                JOIN market_prices mp ON m.id = mp.market_id
                WHERE m.active = TRUE
                AND mp.timestamp >= :cutoff_date
            """)
            
            result = await session.execute(query, {"cutoff_date": cutoff_date})
            market_ids = [row[0] for row in result.fetchall()]
            
            if len(market_ids) < 2:
                return []
            
            # Build correlation matrix
            clusters = []
            processed = set()
            
            for market_id in market_ids:
                if market_id in processed:
                    continue
                
                # Find correlated markets
                correlated = await self.find_correlated_markets(
                    market_id,
                    min_correlation,
                    lookback_days
                )
                
                if correlated:
                    cluster_markets = [market_id] + [m["market_id"] for m in correlated]
                    processed.update(cluster_markets)
                    
                    clusters.append({
                        "cluster_id": f"cluster_{len(clusters)}",
                        "markets": cluster_markets,
                        "size": len(cluster_markets),
                        "avg_correlation": round(
                            sum(m["correlation"] for m in correlated) / len(correlated),
                            3
                        ) if correlated else 0.0
                    })
            
            return clusters
