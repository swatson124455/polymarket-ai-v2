"""
Order Book Depth Tracker
========================
Maintains local order book state for liquidity analysis.
Tracks full order book, not just midpoint prices.
"""
from typing import Dict, List, Optional
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class OrderBookTracker:
    """
    Maintains local order book state for liquidity analysis.
    Tracks full order book depth, not just midpoint prices.
    """
    
    def __init__(self, client, cache=None):
        self.client = client
        self.cache = cache
        self.order_books = {}  # token_id -> order book snapshot
    
    async def snapshot_order_book(self, token_id: str, condition_id: str = "") -> Dict:
        """
        Get current order book depth snapshot.

        Args:
            token_id: CLOB token ID
            condition_id: CLOB condition/market ID (required for Polymarket API)

        Returns:
            Dict with:
                - token_id
                - timestamp
                - bids: Aggregated bid levels
                - asks: Aggregated ask levels
                - spread: Bid-ask spread
                - depth_1pct: Liquidity within 1% of mid
                - depth_5pct: Liquidity within 5% of mid
                - imbalance: Order book imbalance (-1 to 1)
        """
        try:
            book = await self.client.get_orderbook(market_id=condition_id, token_id=token_id)
            
            if not book:
                return {
                    "token_id": token_id,
                    "timestamp": datetime.now(timezone.utc),
                    "error": "No order book data"
                }
            
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            
            # Calculate spread
            spread = 0.0
            if bids and asks:
                best_bid = float(bids[0].get("price", 0)) if bids else 0
                best_ask = float(asks[0].get("price", 1)) if asks else 1
                spread = best_ask - best_bid
            
            # Aggregate levels
            bid_levels = self._aggregate_levels(bids)
            ask_levels = self._aggregate_levels(asks)
            
            # Calculate depth within percentage of mid
            mid_price = (float(bids[0].get("price", 0)) + float(asks[0].get("price", 1))) / 2 if bids and asks else 0.5
            depth_1pct = self._depth_within_pct(bids, asks, mid_price, 0.01)
            depth_5pct = self._depth_within_pct(bids, asks, mid_price, 0.05)
            
            # Calculate imbalance
            imbalance = self._calculate_imbalance(bids, asks)
            
            snapshot = {
                "token_id": token_id,
                "timestamp": datetime.now(timezone.utc),
                "bids": bid_levels,
                "asks": ask_levels,
                "spread": spread,
                "mid_price": mid_price,
                "depth_1pct": depth_1pct,
                "depth_5pct": depth_5pct,
                "imbalance": imbalance,
            }
            
            # Cache snapshot
            self.order_books[token_id] = snapshot
            if self.cache:
                try:
                    await self.cache.set(
                        f"orderbook:{token_id}",
                        snapshot,
                        ttl=10  # 10 second TTL for order books
                    )
                except Exception as e:
                    logger.debug(f"Failed to cache order book: {e}")
            
            return snapshot
            
        except Exception as e:
            logger.warning(f"Failed to snapshot order book for {token_id}: {str(e)}")
            return {
                "token_id": token_id,
                "timestamp": datetime.now(timezone.utc),
                "error": str(e)
            }
    
    def _aggregate_levels(self, levels: List[Dict]) -> List[Dict]:
        """
        Aggregate order book levels by price.
        
        Args:
            levels: List of {price, size} dicts
        
        Returns:
            Aggregated levels
        """
        if not levels:
            return []
        
        aggregated = {}
        for level in levels:
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
            
            # Round price to 4 decimal places for aggregation
            price_key = round(price, 4)
            
            if price_key in aggregated:
                aggregated[price_key]["size"] += size
            else:
                aggregated[price_key] = {
                    "price": price,
                    "size": size
                }
        
        # Sort by price (descending for bids, ascending for asks)
        return sorted(aggregated.values(), key=lambda x: x["price"], reverse=True)
    
    def _depth_within_pct(
        self,
        bids: List[Dict],
        asks: List[Dict],
        mid_price: float,
        pct: float
    ) -> float:
        """
        Calculate total liquidity within pct% of mid price.
        
        Args:
            bids: Bid levels
            asks: Ask levels
            mid_price: Midpoint price
            pct: Percentage (e.g., 0.01 for 1%)
        
        Returns:
            Total liquidity (sum of sizes)
        """
        depth = 0.0
        price_range = mid_price * pct
        
        for level in bids + asks:
            price = float(level.get("price", 0))
            if abs(price - mid_price) <= price_range:
                depth += float(level.get("size", 0))
        
        return depth
    
    def _calculate_imbalance(self, bids: List[Dict], asks: List[Dict]) -> float:
        """
        Calculate order book imbalance.
        
        Positive = more bids = bullish pressure
        Negative = more asks = bearish pressure
        
        Returns:
            Imbalance from -1.0 (all asks) to 1.0 (all bids)
        """
        if not bids and not asks:
            return 0.0
        
        bid_volume = sum(float(b.get("size", 0)) for b in bids[:5])  # Top 5 levels
        ask_volume = sum(float(a.get("size", 0)) for a in asks[:5])  # Top 5 levels
        
        total_volume = bid_volume + ask_volume
        
        if total_volume == 0:
            return 0.0
        
        return (bid_volume - ask_volume) / total_volume
    
    def get_imbalance_signal(self, token_id: str) -> Optional[Dict]:
        """
        Get imbalance-based trading signal.
        
        Returns:
            Dict with signal direction and strength, or None
        """
        snapshot = self.order_books.get(token_id)
        if not snapshot:
            return None
        
        imbalance = snapshot.get("imbalance", 0)
        
        # Strong imbalance = trading signal
        if imbalance > 0.3:
            return {
                "direction": "bullish",
                "strength": min(imbalance, 1.0),
                "reason": "order_book_imbalance",
                "token_id": token_id
            }
        elif imbalance < -0.3:
            return {
                "direction": "bearish",
                "strength": min(abs(imbalance), 1.0),
                "reason": "order_book_imbalance",
                "token_id": token_id
            }
        
        return None
