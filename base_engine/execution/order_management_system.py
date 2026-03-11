"""
Order Management System
=======================
Comprehensive order tracking and management.
Tracks all orders, fills, and order history.

Order lifecycle states:
  PENDING → SUBMITTED → OPEN → FILLED (happy path)
  PENDING → FAILED (pre-trade check rejected)
  OPEN → CANCELLED (user/kill-switch cancellation)
  OPEN → PARTIALLY_FILLED → FILLED (gradual fill)

Paper trading: orders transition PENDING → FILLED instantly (no CLOB).
Live trading: orders transition through SUBMITTED → OPEN as CLOB confirms.
"""
import uuid
from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime, timezone
from collections import defaultdict
from structlog import get_logger

logger = get_logger()


class OrderState(Enum):
    """Order lifecycle states."""
    PENDING = "pending"
    SUBMITTED = "submitted"      # Sent to CLOB, awaiting acknowledgement
    OPEN = "open"                # CLOB confirmed, on the book
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OrderManagementSystem:
    """
    Comprehensive order tracking and management.
    """
    
    def __init__(self, execution_engine, client):
        self.execution_engine = execution_engine
        self.client = client
        self.order_gateway = None
        self.open_orders: Dict[str, Dict] = {}
        self.order_history: List[Dict] = []
        self.fills: List[Dict] = []
        self.monitoring_task = None
        self.running = False

    def set_order_gateway(self, gateway) -> None:
        """Route orders through gateway (kill switch + coordinator)."""
        self.order_gateway = gateway
    
    async def submit_order(self, order: Dict) -> str:
        """
        Submit order and track it.
        
        Args:
            order: Dict with market_id, token_id, side, size, price, type
        
        Returns:
            Order ID
        """
        order_id = str(uuid.uuid4())
        
        order_record = {
            "id": order_id,
            "market_id": order["market_id"],
            "token_id": order.get("token_id"),
            "side": order["side"],
            "size": order["size"],
            "price": order.get("price"),
            "type": order.get("type", "limit"),
            "status": "pending",
            "submitted_at": datetime.now(timezone.utc),
            "filled": 0.0,
            "avg_fill_price": None,
            "exchange_id": None,
        }
        
        try:
            place = self.order_gateway or self.execution_engine
            result = await place.place_order(
                bot_name=order.get("bot_name", "order_manager"),
                market_id=order["market_id"],
                token_id=order.get("token_id"),
                side=order["side"],
                size=order["size"],
                price=order.get("price"),
                confidence=order.get("confidence", 0.5)
            )
            
            if result and result.get("success"):
                order_record["exchange_id"] = result.get("order_id")
                order_record["status"] = "open"
                order_record["submitted_at"] = datetime.now(timezone.utc)
            else:
                order_record["status"] = "failed"
                order_record["error"] = result.get("error", "Unknown error") if result else "No response"
                self.order_history.append(order_record)
                return order_id
            
            self.open_orders[order_id] = order_record
            
            logger.info(
                f"Order submitted",
                order_id=order_id,
                market_id=order["market_id"],
                side=order["side"],
                size=order["size"]
            )
            
            return order_id
            
        except Exception as e:
            order_record["status"] = "failed"
            order_record["error"] = str(e)
            self.order_history.append(order_record)
            logger.error(f"Order submission failed: {str(e)}")
            return order_id
    
    async def start_monitoring(self):
        """Start continuous order monitoring"""
        if self.running:
            return
        
        self.running = True
        # Note: In a real implementation, this would poll exchange for order status
        # For now, we'll track orders that are submitted through our system
        logger.info("Order monitoring started")
    
    async def stop_monitoring(self):
        """Stop order monitoring"""
        self.running = False
        logger.info("Order monitoring stopped")
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: Internal order ID
        
        Returns:
            True if cancelled, False if not found or already filled
        """
        order = self.open_orders.get(order_id)
        if not order:
            logger.warning(f"Order {order_id} not found in open orders")
            return False
        
        if order["status"] != "open":
            logger.warning(f"Order {order_id} is not open (status: {order['status']})")
            return False
        
        try:
            # Cancel on exchange if we have exchange_id
            if order.get("exchange_id"):
                # Would call exchange cancel API here
                pass
            
            order["status"] = "cancelled"
            order["cancelled_at"] = datetime.now(timezone.utc)
            
            self.order_history.append(order)
            del self.open_orders[order_id]
            
            logger.info(f"Order {order_id} cancelled")
            return True
            
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {str(e)}")
            return False
    
    async def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """
        Cancel all open orders, optionally filtered by market.
        
        Args:
            market_id: Optional market ID to filter by
        
        Returns:
            Number of orders cancelled
        """
        cancelled = 0
        
        for order_id, order in list(self.open_orders.items()):
            if market_id is None or order["market_id"] == market_id:
                if await self.cancel_order(order_id):
                    cancelled += 1
        
        logger.info(f"Cancelled {cancelled} orders", market_id=market_id)
        return cancelled
    
    def get_open_orders(self, market_id: Optional[str] = None) -> List[Dict]:
        """Get all open orders, optionally filtered by market"""
        if market_id:
            return [o for o in self.open_orders.values() if o["market_id"] == market_id]
        return list(self.open_orders.values())
    
    def get_order_history(self, market_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get order history, optionally filtered by market"""
        history = self.order_history[-limit:] if limit else self.order_history
        
        if market_id:
            history = [o for o in history if o["market_id"] == market_id]
        
        return history
    
    def get_fills(self, market_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get fills, optionally filtered by market"""
        fills = self.fills[-limit:] if limit else self.fills
        
        if market_id:
            fills = [f for f in fills if f.get("market_id") == market_id]
        
        return fills
    
    def record_fill(self, order_id: str, filled: float, price: float):
        """Record a fill for an order"""
        order = self.open_orders.get(order_id)
        if not order:
            return
        
        order["filled"] = filled
        if order["avg_fill_price"] is None:
            order["avg_fill_price"] = price
        else:
            # Weighted average
            total_filled = order.get("total_filled", 0) + filled
            if total_filled > 0:
                current_avg = order["avg_fill_price"]
                order["avg_fill_price"] = (current_avg * order.get("total_filled", 0) + price * filled) / total_filled
            order["total_filled"] = total_filled
        
        # Check if fully filled
        if order["filled"] >= order["size"] * 0.99:  # 99% filled = complete
            order["status"] = "filled"
            order["filled_at"] = datetime.now(timezone.utc)
            self.order_history.append(order)
            del self.open_orders[order_id]
            
            self.fills.append({
                "order_id": order_id,
                "market_id": order["market_id"],
                "side": order["side"],
                "filled": filled,
                "price": price,
                "timestamp": datetime.now(timezone.utc)
            })
