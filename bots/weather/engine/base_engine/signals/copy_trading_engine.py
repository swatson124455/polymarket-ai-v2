"""
Copy Trading Engine
==================
Automatically copies trades from selected traders.
"""
import asyncio
import random
from typing import Dict, List, Optional
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class CopyTradingEngine:
    """
    Automatically copies trades from selected traders.
    """
    
    def __init__(self, execution_engine, whale_tracker, config: Optional[Dict] = None):
        self.execution_engine = execution_engine
        self.order_gateway = None
        self.whale_tracker = whale_tracker
        
        if config is None:
            config = {}
        
        self.followed_traders = config.get("followed_traders", [])
        self.copy_ratio = config.get("copy_ratio", 0.1)  # 10% of their size
        self.max_position = config.get("max_position", 100)
        self.delay_seconds = config.get("delay_seconds", 30)  # Slight delay
        self.category_filters = config.get("categories", None)
        self.min_whale_rank = config.get("min_whale_rank", 0.7)  # Top 30% of smart money
        self.running = False

    def set_order_gateway(self, gateway):
        """Route orders through gateway (kill switch + coordinator)."""
        self.order_gateway = gateway
    
    async def start(self):
        """Start copy trading"""
        if self.running:
            return
        
        self.running = True
        logger.info(
            f"Copy trading started",
            followed_traders=len(self.followed_traders),
            copy_ratio=self.copy_ratio
        )
    
    async def stop(self):
        """Stop copy trading"""
        self.running = False
        logger.info("Copy trading stopped")
    
    async def process_trader_trade(self, trade: Dict) -> Optional[Dict]:
        """
        Process a trade from a followed trader.
        
        Args:
            trade: Trade dict with trader_address, market_id, side, size, etc.
        
        Returns:
            Copy trade result if executed, None if skipped
        """
        if not self.running:
            return None
        
        trader = trade.get("trader_address") or trade.get("user_address")
        
        if not trader:
            return None
        
        # Check if trader is in followed list
        if trader not in self.followed_traders:
            return None
        
        # Check whale rank if available
        whale_rank = trade.get("smart_money_rank") or trade.get("whale_rank")
        if whale_rank and whale_rank < self.min_whale_rank:
            logger.debug(f"Skipping trade from trader {trader[:8]}... (rank {whale_rank} < {self.min_whale_rank})")
            return None
        
        # Get market to check category
        market_id = trade.get("market_id")
        if not market_id:
            return None
        
        try:
            # Get market for category check
            # Note: Would need market data - for now, skip category filter if market not available
            market_category = None
            if self.category_filters:
                # Would fetch market here if needed
                # For now, allow all if category_filters is set but market unavailable
                pass
            
            if self.category_filters and market_category and market_category not in self.category_filters:
                logger.debug(f"Skipping trade - category {market_category} not in filters")
                return None
            
            # Calculate copy size
            original_size = trade.get("size", 0)
            copy_size = min(
                original_size * self.copy_ratio,
                self.max_position
            )
            
            if copy_size < 1.0:  # Minimum $1
                logger.debug(f"Copy size too small: {copy_size}")
                return None
            
            # Delay to avoid front-running detection
            delay = self.delay_seconds + random.uniform(0, 10)
            logger.info(
                f"Copying trade from {trader[:8]}...",
                market_id=market_id,
                side=trade.get("side"),
                original_size=original_size,
                copy_size=copy_size,
                delay_seconds=delay
            )
            
            await asyncio.sleep(delay)
            
            # Execute copy trade (via gateway when set: kill switch + coordinator)
            place = (self.order_gateway or self.execution_engine).place_order
            result = await place(
                bot_name="copy_trading",
                market_id=market_id,
                token_id=trade.get("token_id"),
                side=trade.get("side"),
                size=copy_size,
                price=trade.get("price"),
                confidence=0.7  # Moderate confidence for copied trades
            )
            
            if result and result.get("success"):
                logger.info(
                    f"Copy trade executed",
                    market_id=market_id,
                    side=trade.get("side"),
                    size=copy_size,
                    copied_from=trader[:8]
                )
                
                return {
                    "success": True,
                    "copied_from": trader,
                    "original_size": original_size,
                    "copy_size": copy_size,
                    "result": result
                }
            else:
                logger.warning(f"Copy trade failed: {result.get('error') if result else 'No result'}")
                return {
                    "success": False,
                    "error": result.get("error") if result else "No result"
                }
                
        except Exception as e:
            logger.error(f"Error processing copy trade: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    async def add_followed_trader(self, trader_address: str, min_rank: Optional[float] = None):
        """Add a trader to follow"""
        if trader_address not in self.followed_traders:
            self.followed_traders.append(trader_address)
            logger.info(f"Added trader to follow list: {trader_address[:8]}...")
    
    async def remove_followed_trader(self, trader_address: str):
        """Remove a trader from follow list"""
        if trader_address in self.followed_traders:
            self.followed_traders.remove(trader_address)
            logger.info(f"Removed trader from follow list: {trader_address[:8]}...")
    
    def get_followed_traders(self) -> List[str]:
        """Get list of followed traders"""
        return self.followed_traders.copy()
