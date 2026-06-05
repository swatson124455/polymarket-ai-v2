"""
Advanced Order Types - Stop-loss, take-profit, trailing stops, iceberg orders.

Provides:
- Stop-Loss Orders - Auto-exit on loss threshold
- Take-Profit Orders - Auto-exit on profit target
- Trailing Stop - Dynamic stop-loss that follows price
- Iceberg Orders - Large orders split into smaller
"""
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from enum import Enum
from structlog import get_logger
from bots.weather.engine.base_engine.execution.execution_engine import ExecutionEngine
from bots.weather.engine.base_engine.data.polymarket_client import PolymarketClient

logger = get_logger()


class OrderType(Enum):
    """Advanced order types."""
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    ICEBERG = "iceberg"
    MARKET = "market"
    LIMIT = "limit"


class AdvancedOrder:
    """Represents an advanced order."""
    
    def __init__(
        self,
        order_id: str,
        order_type: OrderType,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        trailing_distance: Optional[float] = None,
        iceberg_chunk_size: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.order_id = order_id
        self.order_type = order_type
        self.market_id = market_id
        self.token_id = token_id
        self.side = side
        self.size = size
        self.price = price
        self.stop_price = stop_price
        self.take_profit_price = take_profit_price
        self.trailing_distance = trailing_distance
        self.iceberg_chunk_size = iceberg_chunk_size
        self.metadata = metadata or {}
        
        self.status = "pending"
        self.created_at = datetime.now(timezone.utc)
        self.executed_at: Optional[datetime] = None
        self.executed_size = 0.0
        self.highest_price = price or 0.0  # For trailing stop
        self.lowest_price = price or 1.0  # For trailing stop
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "order_type": self.order_type.value,
            "market_id": self.market_id,
            "token_id": self.token_id,
            "side": self.side,
            "size": self.size,
            "price": self.price,
            "stop_price": self.stop_price,
            "take_profit_price": self.take_profit_price,
            "trailing_distance": self.trailing_distance,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "executed_size": self.executed_size,
            "metadata": self.metadata
        }


class AdvancedOrderManager:
    """
    Manages advanced order types.
    
    Monitors and executes:
    - Stop-loss orders
    - Take-profit orders
    - Trailing stops
    - Iceberg orders
    """
    
    def __init__(
        self,
        execution_engine: ExecutionEngine,
        client: Optional[PolymarketClient] = None
    ):
        self.execution_engine = execution_engine
        self.order_gateway = None
        self.client = client
        self.orders: Dict[str, AdvancedOrder] = {}

    def set_order_gateway(self, gateway):
        """Route orders through gateway (kill switch + coordinator)."""
        self.order_gateway = gateway
        self.monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.check_interval_seconds = 5.0
    
    async def place_stop_loss_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        entry_price: float,
        stop_loss_price: float,
        bot_name: str = "system"
    ) -> str:
        """
        Place a stop-loss order.
        
        Args:
            market_id: Market ID
            token_id: Token ID
            side: Order side (YES/NO)
            size: Position size
            entry_price: Entry price
            stop_loss_price: Stop-loss price
            bot_name: Bot name
        
        Returns:
            Order ID
        """
        order_id = f"stop_loss_{market_id}_{token_id}_{datetime.now(timezone.utc).timestamp()}"
        
        order = AdvancedOrder(
            order_id=order_id,
            order_type=OrderType.STOP_LOSS,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=entry_price,
            stop_price=stop_loss_price,
            metadata={"bot_name": bot_name, "entry_price": entry_price}
        )
        
        self.orders[order_id] = order
        
        # Start monitoring if not already running
        if not self.monitoring:
            await self.start_monitoring()
        
        logger.info(f"Stop-loss order placed: {order_id} at {stop_loss_price}")
        return order_id
    
    async def place_take_profit_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        entry_price: float,
        take_profit_price: float,
        bot_name: str = "system"
    ) -> str:
        """Place a take-profit order."""
        order_id = f"take_profit_{market_id}_{token_id}_{datetime.now(timezone.utc).timestamp()}"
        
        order = AdvancedOrder(
            order_id=order_id,
            order_type=OrderType.TAKE_PROFIT,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=entry_price,
            take_profit_price=take_profit_price,
            metadata={"bot_name": bot_name, "entry_price": entry_price}
        )
        
        self.orders[order_id] = order
        
        if not self.monitoring:
            await self.start_monitoring()
        
        logger.info(f"Take-profit order placed: {order_id} at {take_profit_price}")
        return order_id
    
    async def place_trailing_stop_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        entry_price: float,
        trailing_distance: float,
        bot_name: str = "system"
    ) -> str:
        """Place a trailing stop order."""
        order_id = f"trailing_stop_{market_id}_{token_id}_{datetime.now(timezone.utc).timestamp()}"
        
        order = AdvancedOrder(
            order_id=order_id,
            order_type=OrderType.TRAILING_STOP,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=entry_price,
            trailing_distance=trailing_distance,
            metadata={"bot_name": bot_name, "entry_price": entry_price}
        )
        
        order.highest_price = entry_price
        order.lowest_price = entry_price
        
        self.orders[order_id] = order
        
        if not self.monitoring:
            await self.start_monitoring()
        
        logger.info(f"Trailing stop order placed: {order_id} with distance {trailing_distance}")
        return order_id
    
    async def place_iceberg_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        total_size: float,
        price: float,
        chunk_size: float,
        bot_name: str = "system"
    ) -> str:
        """Place an iceberg order (large order split into smaller chunks)."""
        order_id = f"iceberg_{market_id}_{token_id}_{datetime.now(timezone.utc).timestamp()}"
        
        order = AdvancedOrder(
            order_id=order_id,
            order_type=OrderType.ICEBERG,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=total_size,
            price=price,
            iceberg_chunk_size=chunk_size,
            metadata={"bot_name": bot_name}
        )
        
        self.orders[order_id] = order
        
        # Execute first chunk immediately
        await self._execute_iceberg_chunk(order)
        
        logger.info(f"Iceberg order placed: {order_id} ({total_size} total, {chunk_size} per chunk)")
        return order_id
    
    async def start_monitoring(self):
        """Start monitoring orders for execution."""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_task = asyncio.create_task(self._monitor_orders())
        logger.info("Advanced order monitoring started")
    
    async def stop_monitoring(self):
        """Stop monitoring orders."""
        self.monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Advanced order monitoring stopped")
    
    async def _monitor_orders(self):
        """Monitor all orders and execute when conditions are met."""
        while self.monitoring:
            try:
                for order_id, order in list(self.orders.items()):
                    if order.status == "executed":
                        continue
                    
                    if order.order_type == OrderType.STOP_LOSS:
                        await self._check_stop_loss(order)
                    elif order.order_type == OrderType.TAKE_PROFIT:
                        await self._check_take_profit(order)
                    elif order.order_type == OrderType.TRAILING_STOP:
                        await self._check_trailing_stop(order)
                    elif order.order_type == OrderType.ICEBERG:
                        await self._check_iceberg(order)
                
                await asyncio.sleep(self.check_interval_seconds)
            except Exception as e:
                logger.error(f"Error monitoring orders: {str(e)}", exc_info=True)
                await asyncio.sleep(self.check_interval_seconds)
    
    async def _check_stop_loss(self, order: AdvancedOrder):
        """Check if stop-loss should trigger."""
        if not self.client or not order.stop_price:
            return
        
        try:
            # Get current price
            current_price = await self._get_current_price(order.market_id, order.token_id)
            
            if not current_price:
                return
            
            # Check if stop-loss should trigger
            should_trigger = False
            if order.side == "SELL":
                # Stop-loss for long position: exit if price drops below stop
                should_trigger = current_price <= order.stop_price
            else:
                # Stop-loss for short position: exit if price rises above stop
                should_trigger = current_price >= order.stop_price
            
            if should_trigger:
                await self._execute_order(order, current_price)
        except Exception as e:
            logger.warning(f"Error checking stop-loss for {order.order_id}: {str(e)}")
    
    async def _check_take_profit(self, order: AdvancedOrder):
        """Check if take-profit should trigger."""
        if not self.client or not order.take_profit_price:
            return
        
        try:
            current_price = await self._get_current_price(order.market_id, order.token_id)
            
            if not current_price:
                return
            
            should_trigger = False
            if order.side == "SELL":
                # Take-profit for long: exit if price rises above target
                should_trigger = current_price >= order.take_profit_price
            else:
                # Take-profit for short: exit if price drops below target
                should_trigger = current_price <= order.take_profit_price
            
            if should_trigger:
                await self._execute_order(order, current_price)
        except Exception as e:
            logger.warning(f"Error checking take-profit for {order.order_id}: {str(e)}")
    
    async def _check_trailing_stop(self, order: AdvancedOrder):
        """Check and update trailing stop."""
        if not self.client or not order.trailing_distance:
            return
        
        try:
            current_price = await self._get_current_price(order.market_id, order.token_id)
            
            if not current_price:
                return
            
            # Update highest/lowest price
            if order.side == "SELL":  # Long position
                if current_price > order.highest_price:
                    order.highest_price = current_price
                    # Update stop price
                    order.stop_price = order.highest_price - order.trailing_distance
                
                # Check if trailing stop triggered
                if current_price <= (order.stop_price or 0):
                    await self._execute_order(order, current_price)
            else:  # Short position
                if current_price < order.lowest_price:
                    order.lowest_price = current_price
                    order.stop_price = order.lowest_price + order.trailing_distance
                
                if current_price >= (order.stop_price or 1):
                    await self._execute_order(order, current_price)
        except Exception as e:
            logger.warning(f"Error checking trailing stop for {order.order_id}: {str(e)}")
    
    async def _check_iceberg(self, order: AdvancedOrder):
        """Check if next iceberg chunk should execute."""
        if order.executed_size >= order.size:
            order.status = "executed"
            return
        
        remaining = order.size - order.executed_size
        if remaining > 0 and order.iceberg_chunk_size:
            await self._execute_iceberg_chunk(order)
    
    async def _execute_iceberg_chunk(self, order: AdvancedOrder):
        """Execute next chunk of iceberg order."""
        if not order.iceberg_chunk_size:
            return
        
        remaining = order.size - order.executed_size
        chunk_size = min(order.iceberg_chunk_size, remaining)
        
        if chunk_size <= 0:
            return
        
        try:
            place = self.order_gateway or self.execution_engine
            result = await place.place_order(
                bot_name=order.metadata.get("bot_name", "system"),
                market_id=order.market_id,
                token_id=order.token_id,
                side=order.side,
                size=chunk_size,
                price=order.price or 0.5,
                confidence=1.0
            )
            
            if result.get("success"):
                order.executed_size += chunk_size
                if order.executed_size >= order.size:
                    order.status = "executed"
                    order.executed_at = datetime.now(timezone.utc)
                    logger.info(f"Iceberg order {order.order_id} fully executed")
        except Exception as e:
            logger.warning(f"Error executing iceberg chunk: {str(e)}")
    
    async def _execute_order(self, order: AdvancedOrder, current_price: float):
        """Execute an order."""
        try:
            place = self.order_gateway or self.execution_engine
            result = await place.place_order(
                bot_name=order.metadata.get("bot_name", "system"),
                market_id=order.market_id,
                token_id=order.token_id,
                side=order.side,
                size=order.size - order.executed_size,
                price=current_price,
                confidence=1.0
            )
            
            if result.get("success"):
                order.status = "executed"
                order.executed_at = datetime.now(timezone.utc)
                order.executed_size = order.size
                logger.info(f"Order {order.order_id} executed at {current_price}")
        except Exception as e:
            logger.error(f"Error executing order {order.order_id}: {str(e)}", exc_info=True)
    
    async def _get_current_price(self, market_id: str, token_id: str) -> Optional[float]:
        """Get current price for a token via CLOB order book midpoint or market data."""
        if not self.client:
            return None
        try:
            # Try order book first (best bid/ask midpoint)
            if hasattr(self.client, "get_order_book"):
                book = await self.client.get_order_book(market_id)
                if book and not book.get("error"):
                    bids = book.get("bids") or []
                    asks = book.get("asks") or []
                    if bids and asks:
                        best_bid = float(bids[0].get("price", bids[0][0]) if isinstance(bids[0], dict) else bids[0][0])
                        best_ask = float(asks[0].get("price", asks[0][0]) if isinstance(asks[0], dict) else asks[0][0])
                        midpoint = (best_bid + best_ask) / 2
                        if 0 < midpoint < 1:
                            return midpoint
            # Fallback: get_market price from API
            if hasattr(self.client, "get_market"):
                market = await self.client.get_market(market_id)
                if market and isinstance(market, dict):
                    tokens = market.get("tokens", [])
                    for t in tokens:
                        if isinstance(t, dict) and t.get("tokenId") == token_id:
                            p = t.get("price") or t.get("outcomePrice")
                            if p is not None:
                                return float(p)
            return None
        except Exception as e:
            logger.warning(f"Error getting current price: {str(e)}")
            return None
    
    def get_order(self, order_id: str) -> Optional[AdvancedOrder]:
        """Get order by ID."""
        return self.orders.get(order_id)
    
    def get_active_orders(self) -> List[AdvancedOrder]:
        """Get all active (non-executed) orders."""
        return [o for o in self.orders.values() if o.status != "executed"]
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        if order_id in self.orders:
            self.orders[order_id].status = "cancelled"
            logger.info(f"Order {order_id} cancelled")
            return True
        return False
