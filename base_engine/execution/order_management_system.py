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
  OPEN → UNCERTAIN (live only: no WS confirmation + poll exhausted)
  UNCERTAIN → FILLED | FAILED (reconciliation resolves)

Paper trading: orders transition PENDING → FILLED instantly (no CLOB).
Live trading: orders transition through SUBMITTED → OPEN as CLOB confirms.
  Fill confirmation polling (A8) activates ONLY in live mode.
"""
import asyncio
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
    UNCERTAIN = "uncertain"          # Live only: fill status unknown after poll exhaustion


class OrderManagementSystem:
    """
    Comprehensive order tracking and management.
    """
    
    # Fill polling constants (live mode only)
    POLL_INTERVAL_SECONDS = 10
    POLL_MAX_RETRIES = 3
    WS_CONFIRMATION_TIMEOUT_SECONDS = 30
    UNCERTAIN_RECONCILE_HOURS = 4

    def __init__(self, execution_engine, client, simulation_mode=True):
        self.execution_engine = execution_engine
        self.client = client
        self.simulation_mode = simulation_mode
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

            # A8: In live mode, schedule fill confirmation polling as WS fallback
            if not self.simulation_mode and order_record.get("exchange_id"):
                asyncio.ensure_future(self._wait_then_poll(order_id))

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

    # ------------------------------------------------------------------
    # A8: Fill confirmation polling fallback (live mode only)
    # ------------------------------------------------------------------

    async def _wait_then_poll(self, order_id: str) -> None:
        """
        Wait for WebSocket fill confirmation, then fall back to REST polling.

        Only called in live mode. Waits WS_CONFIRMATION_TIMEOUT_SECONDS for
        the order to leave open_orders (i.e. WS confirmed fill/cancel). If
        still open after the timeout, triggers _poll_for_fill().
        """
        await asyncio.sleep(self.WS_CONFIRMATION_TIMEOUT_SECONDS)

        order = self.open_orders.get(order_id)
        if order is None:
            # Already resolved (WS confirmed fill or cancel) — nothing to do
            return

        logger.warning(
            "No WS fill confirmation within timeout, starting REST poll",
            order_id=order_id,
            exchange_id=order.get("exchange_id"),
            timeout_s=self.WS_CONFIRMATION_TIMEOUT_SECONDS,
        )
        await self._poll_for_fill(order_id)

    async def _poll_for_fill(self, order_id: str) -> None:
        """
        Poll CLOB REST API for order fill status.

        Only runs in live mode. Retries up to POLL_MAX_RETRIES times with
        POLL_INTERVAL_SECONDS between attempts. If the order is confirmed
        filled, updates state to FILLED. If exhausted without confirmation,
        marks the order UNCERTAIN for later reconciliation.
        """
        if self.simulation_mode:
            return

        order = self.open_orders.get(order_id)
        if order is None:
            return

        exchange_id = order.get("exchange_id")
        if not exchange_id:
            return

        for attempt in range(1, self.POLL_MAX_RETRIES + 1):
            logger.info(
                "Polling CLOB for fill confirmation",
                order_id=order_id,
                exchange_id=exchange_id,
                attempt=attempt,
                max_retries=self.POLL_MAX_RETRIES,
            )

            try:
                # client.get_order() is the py-clob-client method for order lookup
                clob_order = await asyncio.get_event_loop().run_in_executor(
                    None, self.client.get_order, exchange_id
                )

                if clob_order:
                    status = None
                    if isinstance(clob_order, dict):
                        status = clob_order.get("status", "").upper()
                    elif hasattr(clob_order, "status"):
                        status = str(clob_order.status).upper()

                    if status in ("MATCHED", "FILLED"):
                        fill_price = None
                        fill_size = order["size"]
                        if isinstance(clob_order, dict):
                            fill_price = float(clob_order.get("price", order.get("price", 0)))
                            fill_size = float(clob_order.get("size_matched", order["size"]))
                        elif hasattr(clob_order, "price"):
                            fill_price = float(clob_order.price)

                        order["status"] = "filled"
                        order["filled"] = fill_size
                        order["avg_fill_price"] = fill_price
                        order["filled_at"] = datetime.now(timezone.utc)
                        order["fill_source"] = "rest_poll"

                        self.order_history.append(order)
                        self.fills.append({
                            "order_id": order_id,
                            "market_id": order["market_id"],
                            "side": order["side"],
                            "filled": fill_size,
                            "price": fill_price,
                            "timestamp": datetime.now(timezone.utc),
                        })
                        del self.open_orders[order_id]

                        logger.info(
                            "Fill confirmed via REST poll",
                            order_id=order_id,
                            exchange_id=exchange_id,
                            attempt=attempt,
                            fill_price=fill_price,
                        )
                        return

                    if status in ("CANCELLED", "CANCELED"):
                        order["status"] = "cancelled"
                        order["cancelled_at"] = datetime.now(timezone.utc)
                        order["fill_source"] = "rest_poll"
                        self.order_history.append(order)
                        del self.open_orders[order_id]
                        logger.info(
                            "Order confirmed cancelled via REST poll",
                            order_id=order_id,
                            attempt=attempt,
                        )
                        return

            except Exception as e:
                logger.warning(
                    "CLOB poll attempt failed",
                    order_id=order_id,
                    attempt=attempt,
                    error=str(e),
                )

            if attempt < self.POLL_MAX_RETRIES:
                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

        # Exhausted retries — mark UNCERTAIN
        order["status"] = "uncertain"
        order["uncertain_since"] = datetime.now(timezone.utc)
        order["fill_source"] = "poll_exhausted"
        logger.error(
            "Order fill status UNCERTAIN after poll exhaustion",
            order_id=order_id,
            exchange_id=exchange_id,
            retries=self.POLL_MAX_RETRIES,
        )

    async def _resolve_uncertain_orders(self) -> Dict[str, str]:
        """
        Reconcile UNCERTAIN orders older than UNCERTAIN_RECONCILE_HOURS.

        Called externally (not self-scheduled). For each qualifying order,
        checks exchange positions to determine if the fill actually occurred.

        Returns:
            Dict mapping order_id → resolution ("filled" or "failed")
        """
        if self.simulation_mode:
            return {}

        now = datetime.now(timezone.utc)
        cutoff_seconds = self.UNCERTAIN_RECONCILE_HOURS * 3600
        resolutions: Dict[str, str] = {}

        uncertain = [
            (oid, o) for oid, o in list(self.open_orders.items())
            if o.get("status") == "uncertain"
        ]

        for order_id, order in uncertain:
            uncertain_since = order.get("uncertain_since")
            if uncertain_since is None:
                continue

            age_seconds = (now - uncertain_since).total_seconds()
            if age_seconds < cutoff_seconds:
                continue

            exchange_id = order.get("exchange_id")
            logger.info(
                "Reconciling UNCERTAIN order",
                order_id=order_id,
                exchange_id=exchange_id,
                age_hours=round(age_seconds / 3600, 1),
            )

            resolved_status = "failed"  # default if we can't confirm fill
            try:
                clob_order = await asyncio.get_event_loop().run_in_executor(
                    None, self.client.get_order, exchange_id
                )

                if clob_order:
                    status = None
                    if isinstance(clob_order, dict):
                        status = clob_order.get("status", "").upper()
                    elif hasattr(clob_order, "status"):
                        status = str(clob_order.status).upper()

                    if status in ("MATCHED", "FILLED"):
                        resolved_status = "filled"
                        fill_price = None
                        if isinstance(clob_order, dict):
                            fill_price = float(clob_order.get("price", order.get("price", 0)))
                        elif hasattr(clob_order, "price"):
                            fill_price = float(clob_order.price)
                        order["avg_fill_price"] = fill_price
                        order["filled"] = order["size"]
                        order["filled_at"] = datetime.now(timezone.utc)
            except Exception as e:
                logger.warning(
                    "Reconciliation API call failed, marking FAILED",
                    order_id=order_id,
                    error=str(e),
                )

            order["status"] = resolved_status
            order["fill_source"] = "reconciliation"
            order["reconciled_at"] = datetime.now(timezone.utc)
            self.order_history.append(order)
            del self.open_orders[order_id]

            resolutions[order_id] = resolved_status
            logger.info(
                "UNCERTAIN order reconciled",
                order_id=order_id,
                resolution=resolved_status,
            )

        if resolutions:
            logger.info(
                "Uncertain order reconciliation complete",
                total=len(resolutions),
                filled=sum(1 for v in resolutions.values() if v == "filled"),
                failed=sum(1 for v in resolutions.values() if v == "failed"),
            )

        return resolutions
