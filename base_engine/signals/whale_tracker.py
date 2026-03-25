"""
Whale Tracker - Track smart money movements in real-time.

Features:
- Real-time large trade detection
- Smart money ranking
- Wallet clustering
- Category-specific performance tracking
"""
import asyncio
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from structlog import get_logger
from base_engine.data.database import Database, Trade, User, WhaleMovement, _naive_utc
from base_engine.data.redis_cache import RedisCache
from base_engine.data.polymarket_client import PolymarketClient

logger = get_logger()


class WhaleTracker:
    """
    Track smart money movements in real-time.
    Build institutional-grade wallet intelligence.
    """
    
    def __init__(
        self,
        client: PolymarketClient,
        db: Database,
        cache: RedisCache,
        min_whale_size_usd: float = 10000.0
    ):
        self.client = client
        self.db = db
        self.cache = cache
        self.min_whale_size_usd = min_whale_size_usd
        self.running = False
        self.monitoring_task = None
    
    async def start_monitoring(self):
        """Start monitoring for whale movements."""
        if self.running:
            return
        
        self.running = True
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info("Whale tracker monitoring started")
    
    async def stop_monitoring(self):
        """Stop monitoring."""
        self.running = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("Whale tracker monitoring stopped")
    
    async def _monitoring_loop(self):
        """Monitor for large trades."""
        # Initial delay: let feature precompute and bot warm scans complete first.
        # Precompute starts at t+150s; 120s here avoids DB pool pressure during cold start.
        await asyncio.sleep(120)
        _consecutive_failures = 0
        while self.running:
            try:
                # Get recent trades
                recent_trades = await self._get_recent_large_trades()

                for trade in recent_trades:
                    await self._process_whale_trade(trade)

                _consecutive_failures = 0  # Reset on success
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                _consecutive_failures += 1
                _backoff = min(30 * (2 ** (_consecutive_failures - 1)), 300)  # 30, 60, 120, 240, 300s cap
                logger.warning(
                    "Whale monitoring error (failure %d/10, backoff %ds): %s",
                    _consecutive_failures, _backoff, str(e)
                )
                if _consecutive_failures >= 10:
                    logger.error("WhaleTracker: 10 consecutive failures — suspending monitoring loop")
                    return
                await asyncio.sleep(_backoff)
    
    async def _get_recent_large_trades(self) -> List[Dict[str, Any]]:
        """Get recent trades above whale threshold."""
        if not self.db.session_factory:
            return []
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, and_
            from sqlalchemy import func
            
            # Get trades from last 5 minutes (naive UTC for PG TIMESTAMP WITHOUT TZ)
            cutoff = _naive_utc(datetime.now(timezone.utc) - timedelta(minutes=5))
            result = await session.execute(
                select(Trade).where(
                    and_(
                        Trade.timestamp >= cutoff,
                        func.abs(Trade.size * Trade.price) >= self.min_whale_size_usd
                    )
                ).order_by(Trade.timestamp.desc())
            )
            trades = result.scalars().all()
            
            return [
                {
                    "id": t.id,
                    "user_address": t.user_address,
                    "market_id": t.market_id,
                    "token_id": t.token_id,
                    "side": t.side,
                    "size": t.size,
                    "price": t.price,
                    "value_usd": t.size * t.price,
                    "timestamp": t.timestamp
                }
                for t in trades
            ]
    
    async def handle_streaming_trade(self, record: dict) -> None:
        """
        Phase 5 fast-path: called <1ms after WebSocket tick from StreamingPersister.
        No DB round-trip — just publishes to Redis whale_alerts immediately.
        Full processing (smart_money_rank, cluster, DB write) happens in normal _monitoring_loop.
        """
        try:
            size = float(record.get("size") or 0)
            price = float(record.get("price") or 0)
            value_usd = size * price
            if value_usd < self.min_whale_size_usd:
                return
            market_id = str(record.get("market_id") or record.get("market") or "")
            user_address = str(record.get("user_address") or "")
            side = str(record.get("side") or "YES")
            logger.info(
                "Whale fast-path detected",
                market_id=market_id,
                value_usd=round(value_usd, 0),
                side=side,
            )
            if self.cache.redis:
                await self.cache.redis.publish(
                    "whale_alerts",
                    json.dumps({
                        "user_address": user_address,
                        "market_id": market_id,
                        "side": side,
                        "size": size,
                        "value_usd": value_usd,
                        "smart_money_rank": None,  # unknown until full processing
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "source": "streaming_fast_path",
                    })
                )
        except Exception as _e:
            logger.debug("Whale fast-path error (non-fatal): %s", _e)

    async def _process_whale_trade(self, trade: Dict[str, Any]):
        """Process a whale trade and store movement."""
        try:
            # Check if already processed
            if not self.db.session_factory:
                return
            
            async with self.db.get_session() as session:
                from sqlalchemy import select
                
                # Check if already recorded
                result = await session.execute(
                    select(WhaleMovement).where(WhaleMovement.trade_id == trade["id"])
                )
                existing = result.scalar_one_or_none()
                
                if existing:
                    return  # Already processed
                
                # Get smart money rank
                smart_money_rank = await self._get_smart_money_rank(trade["user_address"])
                
                # Get category accuracy — market lookup may fail if market_id is a
                # condition_id (hex hash) that Gamma API doesn't recognize (returns 422).
                # Gracefully default to "unknown" category instead of aborting.
                try:
                    market = await self.client.get_market(trade["market_id"])
                except Exception:
                    market = None
                market_category = market.get("category", "unknown") if market else "unknown"
                category_accuracy = await self._get_category_accuracy(
                    trade["user_address"],
                    market_category
                )
                
                # Check if part of cluster
                cluster_id = await self._get_cluster_id(trade["user_address"])
                
                # Create whale movement record
                movement = WhaleMovement(
                    trade_id=trade["id"],
                    user_address=trade["user_address"],
                    market_id=trade["market_id"],
                    token_id=trade["token_id"],
                    side=trade["side"],
                    size=trade["size"],
                    price=trade["price"],
                    value_usd=trade["value_usd"],
                    timestamp=trade["timestamp"],
                    smart_money_rank=smart_money_rank,
                    trader_category_accuracy=category_accuracy,
                    is_clustered=cluster_id is not None,
                    cluster_id=cluster_id
                )
                
                session.add(movement)
                await session.commit()
                
                # Publish to Redis for real-time alerts
                if self.cache.redis:
                    await self.cache.redis.publish(
                        "whale_alerts",
                        json.dumps({
                            "user_address": trade["user_address"],
                            "market_id": trade["market_id"],
                            "side": trade["side"],
                            "size": trade["size"],
                            "value_usd": trade["value_usd"],
                            "smart_money_rank": smart_money_rank,
                            "timestamp": trade["timestamp"].isoformat() if hasattr(trade["timestamp"], "isoformat") else str(trade["timestamp"])
                        })
                    )
                
                logger.info(
                    f"Whale movement detected: {trade['user_address']}",
                    market_id=trade["market_id"],
                    value_usd=trade["value_usd"],
                    smart_money_rank=smart_money_rank
                )
                
        except Exception as e:
            logger.error(f"Error processing whale trade: {str(e)}", exc_info=True)
    
    async def build_smart_money_index(self):
        """
        Build ranked list of smart money wallets.
        Updated daily with rolling performance.
        """
        if not self.db.session_factory:
            return
        
        logger.info("Building smart money index...")
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, func
            
            # Get all traders with significant volume
            result = await session.execute(
                select(
                    Trade.user_address,
                    func.sum(Trade.size * Trade.price).label("total_volume"),
                    func.count(Trade.id).label("total_trades")
                ).where(
                    Trade.timestamp >= datetime.now(timezone.utc) - timedelta(days=90)
                ).group_by(Trade.user_address).having(
                    func.sum(Trade.size * Trade.price) >= 100000  # $100k+ volume
                )
            )
            traders = result.fetchall()
            
            smart_money_scores = {}
            
            for trader_address, total_volume, total_trades in traders:
                metrics = await self._calculate_trader_metrics(trader_address)
                score = self._calculate_smart_money_score(metrics)
                smart_money_scores[trader_address] = score
            
            # Store in Redis sorted set
            if self.cache.redis:
                for address, score in smart_money_scores.items():
                    await self.cache.redis.zadd("smart_money:ranked", {address: score})

            # H9 FIX: Compute and write per-category accuracy to Redis so
            # _get_category_accuracy() returns real values instead of always 0.5.
            # Key: trader_accuracy:{category} → hash field: address → win_rate
            if self.cache.redis and traders:
                try:
                    from sqlalchemy import text as sa_text
                    async with self.db.get_session() as _sess:
                        cat_rows = (await _sess.execute(sa_text(
                            "SELECT pt.user_address, m.category, "
                            "  AVG(CASE WHEN pt.realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate "
                            "FROM paper_trades pt "
                            "JOIN markets m ON CAST(pt.market_id AS TEXT) = CAST(m.id AS TEXT) "
                            "WHERE pt.side = 'SELL' AND m.category IS NOT NULL "
                            "  AND pt.created_at > NOW() - INTERVAL '90 days' "
                            "GROUP BY pt.user_address, m.category "
                            "HAVING COUNT(*) >= 5"
                        ))).fetchall()
                    pipe = self.cache.redis.pipeline()
                    _seen_cats = set()
                    for row in cat_rows:
                        pipe.hset(f"trader_accuracy:{row.category}", row.user_address, round(float(row.win_rate), 4))
                        _seen_cats.add(row.category)
                    for _cat in _seen_cats:
                        pipe.expire(f"trader_accuracy:{_cat}", 86400)  # 24h TTL
                    await pipe.execute()
                    logger.info("Whale: wrote category accuracy for %d trader×category pairs", len(cat_rows))
                except Exception as _e:
                    logger.debug("Whale: category accuracy write failed: %s", _e)

            logger.info(f"Smart money index built: {len(smart_money_scores)} traders")
    
    async def _calculate_trader_metrics(self, address: str) -> Dict[str, Any]:
        """Calculate comprehensive trader performance metrics."""
        if not self.db.session_factory:
            return {}
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, func, and_
            
            # Get trades from last 90 days (naive UTC for PG TIMESTAMP WITHOUT TZ)
            cutoff = _naive_utc(datetime.now(timezone.utc) - timedelta(days=90))
            result = await session.execute(
                select(Trade).where(
                    and_(
                        Trade.user_address == address,
                        Trade.timestamp >= cutoff
                    )
                )
            )
            trades = result.scalars().all()
            
            if not trades:
                return {}
            
            # Calculate basic metrics
            total_trades = len(trades)
            total_volume = sum(t.size * t.price for t in trades)
            
            # Win rate (would need position resolution data - placeholder)
            # For now, use User table data if available
            user_result = await session.execute(
                select(User).where(User.address == address)
            )
            user = user_result.scalar_one_or_none()
            
            win_rate = user.win_rate if user else 0.5
            roi = user.roi if user else 0.0
            
            return {
                "address": address,
                "total_trades": total_trades,
                "total_volume": total_volume,
                "win_rate": win_rate,
                "roi": roi,
                "is_elite": user.is_elite if user else False
            }
    
    def _calculate_smart_money_score(self, metrics: Dict[str, Any]) -> float:
        """Calculate composite smart money score (0.0 to 1.0)."""
        score = 0.0
        
        # Win rate contributes 40%
        score += metrics.get("win_rate", 0.5) * 0.4
        
        # ROI contributes 30%
        roi = metrics.get("roi", 0.0)
        normalized_roi = min(1.0, max(0.0, (roi + 1.0) / 2.0))  # Normalize -1 to 1 -> 0 to 1
        score += normalized_roi * 0.3
        
        # Volume contributes 20% (more volume = more reliable)
        volume = metrics.get("total_volume", 0.0)
        normalized_volume = min(1.0, volume / 1000000.0)  # Normalize to $1M
        score += normalized_volume * 0.2
        
        # Elite status contributes 10%
        if metrics.get("is_elite", False):
            score += 0.1
        
        return min(1.0, score)
    
    async def _get_smart_money_rank(self, address: str) -> float:
        """Get smart money rank for an address."""
        if self.cache.redis:
            rank = await self.cache.redis.zscore("smart_money:ranked", address)
            return rank if rank is not None else 0.0
        return 0.0
    
    async def _get_category_accuracy(
        self,
        address: str,
        category: str
    ) -> float:
        """Get trader's accuracy in a specific category.

        Uses Redis cache to avoid opening a second get_session() while
        _process_whale_trade already holds one (would consume 2 semaphore slots).
        Falls back to 0.5 (neutral) if Redis is unavailable.
        """
        # L1: Redis cache (avoids nested semaphore acquire while outer session is open)
        if self.cache.redis:
            try:
                cached = await self.cache.redis.hget(f"trader_accuracy:{category}", address)
                if cached is not None:
                    return float(cached)
            except Exception:
                pass
        # L2: Default neutral — trade-off: avoids DB session nesting
        # Full accuracy calculation runs in build_smart_money_index() (daily, not per-trade)
        return 0.5
    
    async def _get_cluster_id(self, address: str) -> Optional[str]:
        """Get cluster ID for an address (if part of wallet cluster)."""
        if self.cache.redis:
            cluster_id = await self.cache.redis.hget("wallet_clusters", address)
            return cluster_id.decode() if cluster_id else None
        return None
    
    async def get_whale_movements(
        self,
        min_size: Optional[float] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get recent whale movements."""
        if not self.db.session_factory:
            return []
        
        min_size = min_size or self.min_whale_size_usd
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, and_
            
            result = await session.execute(
                select(WhaleMovement).where(
                    WhaleMovement.value_usd >= min_size
                ).order_by(WhaleMovement.timestamp.desc()).limit(limit)
            )
            movements = result.scalars().all()
            
            return [
                {
                    "trade_id": m.trade_id,
                    "user_address": m.user_address,
                    "market_id": m.market_id,
                    "side": m.side,
                    "size": m.size,
                    "value_usd": m.value_usd,
                    "smart_money_rank": m.smart_money_rank,
                    "trader_category_accuracy": m.trader_category_accuracy,
                    "timestamp": m.timestamp.isoformat() if m.timestamp else None
                }
                for m in movements
            ]

    async def classify_trader_type(self, address: str) -> Dict[str, Any]:
        """
        Classify a trader as: market_maker, arbitrageur, copy_trader, informed, or retail.
        Uses K-means clustering on trading feature vectors.
        """
        metrics = await self._calculate_trader_metrics(address)
        if not metrics or not metrics.get("total_trades"):
            return {"address": address, "type": "unknown", "confidence": 0.0}

        # Build feature vector for classification
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import select, func, and_
                cutoff = _naive_utc(datetime.now(timezone.utc) - timedelta(days=90))
                result = await session.execute(
                    select(Trade).where(
                        and_(Trade.user_address == address, Trade.timestamp >= cutoff)
                    ).order_by(Trade.timestamp.asc())
                )
                trades = result.scalars().all()
        except Exception:
            trades = []

        if len(trades) < 5:
            return {"address": address, "type": "retail", "confidence": 0.3}

        # Compute behavioral features
        sizes = [t.size * t.price for t in trades]
        avg_size = sum(sizes) / len(sizes)
        size_std = (sum((s - avg_size) ** 2 for s in sizes) / len(sizes)) ** 0.5

        # Time between trades (seconds)
        timestamps = [t.timestamp for t in trades if t.timestamp]
        if len(timestamps) >= 2:
            deltas = [(timestamps[i] - timestamps[i - 1]).total_seconds() for i in range(1, len(timestamps))]
            avg_delta = sum(deltas) / len(deltas) if deltas else 86400
        else:
            avg_delta = 86400

        # Market diversity
        unique_markets = len(set(t.market_id for t in trades if t.market_id))

        # Heuristic classification (production: use sklearn KMeans/DBSCAN)
        if avg_delta < 30 and size_std / max(avg_size, 0.01) < 0.3:
            trader_type = "market_maker"
            conf = 0.75
        elif unique_markets > 20 and avg_delta < 300:
            trader_type = "arbitrageur"
            conf = 0.70
        elif size_std / max(avg_size, 0.01) < 0.2 and avg_delta < 120:
            trader_type = "copy_trader"
            conf = 0.60
        elif metrics.get("win_rate", 0.5) > 0.60 and metrics.get("roi", 0) > 0.05:
            trader_type = "informed"
            conf = 0.65
        else:
            trader_type = "retail"
            conf = 0.50

        return {
            "address": address,
            "type": trader_type,
            "confidence": conf,
            "features": {
                "avg_trade_size": round(avg_size, 2),
                "size_cv": round(size_std / max(avg_size, 0.01), 3),
                "avg_trade_interval_s": round(avg_delta, 1),
                "unique_markets": unique_markets,
                "total_trades": len(trades),
                "win_rate": metrics.get("win_rate", 0.5),
                "roi": metrics.get("roi", 0.0),
            },
        }

    async def fingerprint_order_flow(self, address: str) -> Dict[str, Any]:
        """
        Create an order flow fingerprint for wallet behavior clustering.
        Returns feature vector suitable for K-means/DBSCAN grouping.
        """
        classification = await self.classify_trader_type(address)
        features = classification.get("features", {})
        return {
            "address": address,
            "fingerprint": [
                features.get("avg_trade_size", 0),
                features.get("size_cv", 0),
                features.get("avg_trade_interval_s", 0),
                features.get("unique_markets", 0),
                features.get("total_trades", 0),
                features.get("win_rate", 0.5),
                features.get("roi", 0),
            ],
            "trader_type": classification.get("type", "unknown"),
        }
