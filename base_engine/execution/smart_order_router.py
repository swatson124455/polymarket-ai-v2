"""
Smart Order Router
==================
Optimizes order execution for best price and lowest impact.
Implements TWAP, limit passive/aggressive strategies.
"""
import asyncio
import random
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class SmartOrderRouter:
    """
    Optimizes order execution for best price and lowest impact.
    """
    
    def __init__(self, client, execution_engine, orderbook_tracker=None):
        self.client = client
        self.execution_engine = execution_engine
        self.order_gateway = None
        self.orderbook_tracker = orderbook_tracker
        self.open_orders: Dict[str, Dict] = {}

    def set_order_gateway(self, gateway):
        """Route orders through gateway (kill switch + coordinator)."""
        self.order_gateway = gateway
    
    async def execute_smart(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        urgency: str = "normal"
    ) -> Dict[str, Any]:
        """
        Execute order with optimal strategy.
        
        Args:
            market_id: Market ID
            token_id: Token ID
            side: "BUY" or "SELL"
            size: Order size
            urgency: "immediate", "normal", or "patient"
        
        Returns:
            Dict with execution results
        """
        # Analyze market conditions
        # Use OrderBookTracker if available, otherwise try client method
        if self.orderbook_tracker:
            book = await self.orderbook_tracker.snapshot_order_book(token_id)
        else:
            # Fallback to client method (if it exists)
            try:
                book = await self.client.get_orderbook(market_id, token_id)
            except AttributeError:
                # Client doesn't have get_orderbook, fallback to simple execution
                return await self._execute_simple(market_id, token_id, side, size)
        
        if not book or "error" in book:
            # Fallback to simple execution
            return await self._execute_simple(market_id, token_id, side, size)
        
        spread = self._calculate_spread(book)
        depth = self._calculate_depth(book, side)
        volatility = await self._get_recent_volatility(market_id)
        
        # Choose execution strategy
        if urgency == "immediate":
            strategy = "market"
        elif size > depth * 0.1:  # Large order relative to depth
            strategy = "twap"
        elif spread > 0.02:  # Wide spread
            strategy = "limit_passive"
        else:
            strategy = "limit_aggressive"
        
        logger.info(
            f"Smart order routing",
            market_id=market_id,
            strategy=strategy,
            size=size,
            spread=spread,
            depth=depth
        )
        
        # Execute based on strategy
        if strategy == "market":
            return await self._execute_market(market_id, token_id, side, size)
        elif strategy == "twap":
            return await self._execute_twap(market_id, token_id, side, size)
        elif strategy == "limit_passive":
            return await self._execute_limit_passive(market_id, token_id, side, size, book)
        else:
            return await self._execute_limit_aggressive(market_id, token_id, side, size, book)
    
    def _calculate_spread(self, book: Dict[str, Any]) -> float:
        """Calculate bid-ask spread"""
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        
        if not bids or not asks:
            return 0.0
        
        best_bid = float(bids[0].get("price", 0))
        best_ask = float(asks[0].get("price", 1))
        
        return best_ask - best_bid
    
    def _calculate_depth(self, book: Dict[str, Any], side: str) -> float:
        """Calculate available liquidity depth"""
        if side == "BUY":
            levels = book.get("asks", [])
        else:
            levels = book.get("bids", [])
        
        # Sum top 5 levels
        depth = sum(float(level.get("size", 0)) for level in levels[:5])
        return depth
    
    async def _get_recent_volatility(self, market_id: str) -> float:
        """Get recent volatility (simplified)"""
        # Could use price history to calculate actual volatility
        # For now, return default
        return 0.05
    
    async def _execute_market(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float
    ) -> Dict[str, Any]:
        """Execute market order immediately"""
        try:
            place = self.order_gateway or self.execution_engine
            result = await place.place_order(
                bot_name="smart_router",
                market_id=market_id,
                token_id=token_id,
                side=side,
                size=size,
                price=0.5,  # Market order fallback (gateway/execution expect float)
                confidence=1.0
            )
            
            return {
                "strategy": "market",
                "success": True,
                "result": result
            }
        except Exception as e:
            logger.error(f"Market order execution failed: {str(e)}")
            return {
                "strategy": "market",
                "success": False,
                "error": str(e)
            }
    
    async def _execute_twap(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        duration_minutes: int = 30
    ) -> Dict[str, Any]:
        """
        Execute large order over time to minimize impact.
        Time-Weighted Average Price (TWAP) strategy.
        """
        slices = 10
        slice_size = size / slices
        interval = (duration_minutes * 60) / slices
        
        executions = []
        
        logger.info(
            f"Executing TWAP order",
            market_id=market_id,
            total_size=size,
            slices=slices,
            duration_minutes=duration_minutes
        )
        
        for i in range(slices):
            try:
                result = await self._execute_limit_aggressive(
                    market_id, token_id, side, slice_size
                )
                
                if result.get("success"):
                    executions.append(result)
                    logger.debug(f"TWAP slice {i+1}/{slices} executed")
                
                if i < slices - 1:
                    # Add randomness to avoid detection
                    jitter = random.uniform(-interval * 0.2, interval * 0.2)
                    await asyncio.sleep(interval + jitter)
                    
            except Exception as e:
                logger.warning(f"TWAP slice {i+1} failed: {str(e)}")
                continue
        
        if not executions:
            return {
                "strategy": "twap",
                "success": False,
                "error": "All slices failed"
            }
        
        # Calculate average price
        total_filled = sum(e.get("filled", 0) for e in executions)
        total_cost = sum(e.get("price", 0) * e.get("filled", 0) for e in executions)
        avg_price = total_cost / total_filled if total_filled > 0 else 0
        
        return {
            "strategy": "twap",
            "success": True,
            "total_size": size,
            "slices": len(executions),
            "filled": total_filled,
            "avg_price": avg_price,
            "executions": executions
        }
    
    async def _execute_limit_passive(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        book: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute limit order at passive price (better price, may not fill)"""
        # Get best bid/ask
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        
        if side == "BUY" and asks:
            # Buy at best ask (aggressive) or below (passive)
            price = float(asks[0].get("price", 0.5))
            # Passive: price slightly below best ask
            price = price * 0.999  # 0.1% better
        elif side == "SELL" and bids:
            # Sell at best bid (aggressive) or above (passive)
            price = float(bids[0].get("price", 0.5))
            # Passive: price slightly above best bid
            price = price * 1.001  # 0.1% better
        else:
            return {"success": False, "error": "No liquidity"}
        
        try:
            place = self.order_gateway or self.execution_engine
            result = await place.place_order(
                bot_name="smart_router",
                market_id=market_id,
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                confidence=0.8
            )
            
            return {
                "strategy": "limit_passive",
                "success": True,
                "price": price,
                "result": result
            }
        except Exception as e:
            return {
                "strategy": "limit_passive",
                "success": False,
                "error": str(e)
            }
    
    async def _execute_limit_aggressive(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        book: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute limit order at aggressive price (worse price, likely to fill)"""
        if not book:
            if self.orderbook_tracker:
                book = await self.orderbook_tracker.snapshot_order_book(token_id)
            else:
                try:
                    book = await self.client.get_orderbook(market_id, token_id)
                except AttributeError:
                    # Fallback to market order
                    return await self._execute_market(market_id, token_id, side, size)
        
        if not book:
            # Fallback to market order
            return await self._execute_market(market_id, token_id, side, size)
        
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        
        if side == "BUY" and asks:
            # Buy at best ask (aggressive)
            price = float(asks[0].get("price", 0.5))
        elif side == "SELL" and bids:
            # Sell at best bid (aggressive)
            price = float(bids[0].get("price", 0.5))
        else:
            return {"success": False, "error": "No liquidity"}
        
        try:
            place = self.order_gateway or self.execution_engine
            result = await place.place_order(
                bot_name="smart_router",
                market_id=market_id,
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                confidence=0.9
            )
            
            return {
                "strategy": "limit_aggressive",
                "success": True,
                "price": price,
                "filled": size,  # Assume full fill for limit aggressive
                "result": result
            }
        except Exception as e:
            return {
                "strategy": "limit_aggressive",
                "success": False,
                "error": str(e)
            }
    
    async def _execute_simple(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float
    ) -> Dict:
        """Simple execution fallback"""
        try:
            place = self.order_gateway or self.execution_engine
            result = await place.place_order(
                bot_name="smart_router",
                market_id=market_id,
                token_id=token_id,
                side=side,
                size=size,
                price=0.5,
                confidence=0.7
            )
            
            return {
                "strategy": "simple",
                "success": True,
                "result": result
            }
        except Exception as e:
            return {
                "strategy": "simple",
                "success": False,
                "error": str(e)
            }
