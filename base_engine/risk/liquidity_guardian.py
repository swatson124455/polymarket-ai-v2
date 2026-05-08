"""
Liquidity Guardian
==================
Ensures trades only happen in sufficiently liquid markets.
Prevents excessive slippage.
"""
from typing import Dict, Optional
from structlog import get_logger

logger = get_logger()


class LiquidityGuardian:
    """
    Ensures trades only happen in sufficiently liquid markets.
    Prevents trades that would cause excessive slippage.
    """
    
    def __init__(self, client, orderbook_tracker):
        self.client = client
        self.orderbook_tracker = orderbook_tracker
        self.min_liquidity_usd = 1000.0  # Minimum $1000 liquidity
        self.max_slippage_pct = 0.03  # Max 3% slippage
    
    async def check_liquidity(
        self,
        market_id: str,
        token_id: str,
        trade_size: float,
        side: str,
        condition_id: str = "",
        depth_multiplier: float = 0.0,
    ) -> Dict:
        """
        Check if trade can be executed without excessive slippage.

        Args:
            market_id: Market ID
            token_id: Token ID
            trade_size: Size of trade in shares
            side: "BUY" or "SELL"
            condition_id: Optional condition_id for orderbook lookup
            depth_multiplier: 2H per-bot depth gate. If > 0, trade_size must be
                < (sum of top-5 level sizes) / depth_multiplier. 0 disables the
                check. See settings.LIQUIDITY_DEPTH_MULT_{WB,MB,EB,DEFAULT}.

        Returns:
            Dict with:
                - can_execute: bool
                - avg_price: Average execution price
                - best_price: Best available price
                - slippage: Slippage percentage
                - slippage_cost: Cost of slippage in USD
                - recommendation: "proceed", "reduce_size", or "abort"
        """
        # Get order book
        book = await self.orderbook_tracker.snapshot_order_book(token_id, condition_id=condition_id)
        
        if "error" in book:
            return {
                "can_execute": False,
                "reason": "no_orderbook_data",
                "recommendation": "abort"
            }
        
        # Calculate available liquidity at each price level
        # YES/NO both mean "buy that token" → consume asks on that token's book.
        # Only SELL means "sell a token" → consume bids.
        if side in ("BUY", "YES", "NO"):
            levels = book.get("asks", [])
        else:
            levels = book.get("bids", [])
        
        if not levels:
            return {
                "can_execute": False,
                "reason": "no_liquidity",
                "recommendation": "abort"
            }

        # 2H: Per-bot depth gate — fail fast when trade is too large relative to
        # top-5 book depth. Separate from the slippage check below. Disabled when
        # depth_multiplier == 0.
        if depth_multiplier > 0:
            _top5_depth = sum(float(lvl.get("size", 0)) for lvl in levels[:5])
            # S215: distinguish "no orderbook data" from "real depth_exceeded".
            # Pre-fix, an empty top-5 (top5_depth=0 → max_safe=0) returned
            # reason=depth_exceeded with max_safe=0, conflating two failure modes:
            # (a) book is genuinely too thin for our size [recoverable via soft-clamp]
            # (b) book has no usable depth at all [not recoverable].
            # The 8h post-Phase-2 sample showed 12/12 hard-rejects were case (b),
            # masking the Phase-2 soft-clamp. Splitting them lets the soft-clamp
            # fire on (a) and lets case (b) be diagnosed clearly upstream
            # (typically a missing _market_index entry → empty orderbook fetch).
            if _top5_depth == 0:
                return {
                    "can_execute": False,
                    "reason": "no_orderbook_data",
                    "trade_size": trade_size,
                    "liquidity_depth": 0.0,
                    "max_safe": 0.0,
                    "depth_multiplier": depth_multiplier,
                    "recommendation": "abort",
                }
            _max_safe = _top5_depth / depth_multiplier if depth_multiplier > 0 else float("inf")
            if trade_size > _max_safe:
                return {
                    "can_execute": False,
                    "reason": "depth_exceeded",
                    "trade_size": trade_size,
                    "liquidity_depth": _top5_depth,
                    "max_safe": _max_safe,
                    "depth_multiplier": depth_multiplier,
                    "recommendation": "reduce_size",
                }

        # Simulate execution
        remaining = trade_size
        total_cost = 0.0
        prices_touched = []
        
        for level in levels:
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
            
            if remaining <= 0:
                break
            
            fill_size = min(remaining, size)
            total_cost += fill_size * price
            remaining -= fill_size
            prices_touched.append(price)
        
        if remaining > 0:
            return {
                "can_execute": False,
                "reason": "insufficient_liquidity",
                "available": trade_size - remaining,
                "missing": remaining,
                "recommendation": "reduce_size"
            }
        
        # Calculate slippage
        avg_price = total_cost / trade_size
        best_price = prices_touched[0] if prices_touched else 0
        slippage = abs(avg_price - best_price) / best_price if best_price > 0 else 0
        
        # Check if slippage is acceptable
        can_execute = slippage <= self.max_slippage_pct
        
        recommendation = "proceed" if slippage < 0.01 else "reduce_size" if slippage < self.max_slippage_pct else "abort"
        
        return {
            "can_execute": can_execute,
            "avg_price": avg_price,
            "best_price": best_price,
            "slippage": slippage,
            "slippage_pct": slippage * 100,
            "slippage_cost": slippage * trade_size * best_price,  # Cost in USD
            "levels_touched": len(prices_touched),
            "recommendation": recommendation,
            "liquidity_depth": sum(float(level.get("size", 0)) for level in levels[:5])
        }
    
    async def get_max_safe_size(
        self,
        market_id: str,
        token_id: str,
        side: str,
        max_slippage_pct: float = 0.02,
        condition_id: str = "",
    ) -> float:
        """
        Calculate maximum trade size that won't exceed max_slippage_pct.
        
        Returns:
            Maximum safe trade size in shares
        """
        book = await self.orderbook_tracker.snapshot_order_book(token_id, condition_id=condition_id)

        if "error" in book:
            return 0.0

        _is_buy = side in ("BUY", "YES", "NO")
        if _is_buy:
            levels = book.get("asks", [])
        else:
            levels = book.get("bids", [])

        if not levels:
            return 0.0

        best_price = float(levels[0].get("price", 0.5))
        max_price = best_price * (1 + max_slippage_pct) if _is_buy else best_price * (1 - max_slippage_pct)

        # Calculate how much we can buy/sell before exceeding max_price
        total_size = 0.0
        total_cost = 0.0

        for level in levels:
            price = float(level.get("price", 0))

            if _is_buy and price > max_price:
                break
            if not _is_buy and price < max_price:
                break
            
            size = float(level.get("size", 0))
            total_size += size
            total_cost += size * price
        
        return total_size
