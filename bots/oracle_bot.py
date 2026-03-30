"""
OracleBot — UMA ProposePrice pre-resolution edge.

Event-driven bot that listens to the EventBus for ``proposed_outcome`` events
emitted by the UMA proposal monitor. When a proposal fires, the bot has ~2 hours
before resolution. 98.5% of proposals go undisputed.

Logic:
  - Position aligns with proposal → hold (tighten take-profit)
  - Position contradicts proposal → exit immediately
  - No position → buy the proposed outcome at any price below $0.97

This bot does NOT use the standard scan loop; it registers as an EventBus listener.
"""
from typing import Any, Dict, List, Optional
from structlog import get_logger
from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()

# Max price to buy proposed outcome (accounting for ~1.5% dispute rate)
MAX_ENTRY_PRICE = 0.97


class OracleBot(BaseBot):
    """
    Event-driven bot reacting to UMA ProposePrice events.

    Overrides the standard scan loop to be purely event-driven.
    """

    def __init__(self, base_engine):
        super().__init__("OracleBot", base_engine)
        self._max_entry_price = float(getattr(settings, "ORACLE_BOT_MAX_ENTRY_PRICE", MAX_ENTRY_PRICE))
        self._max_position_size = float(getattr(settings, "ORACLE_BOT_MAX_POSITION", 200))

    async def start(self):
        """Register as EventBus listener instead of starting scan loop."""
        self.running = True
        event_bus = getattr(self.base_engine, "event_bus", None)
        if event_bus:
            event_bus.subscribe("proposed_outcome", self._on_proposed_outcome)
            logger.info("OracleBot subscribed to proposed_outcome events")
        else:
            logger.warning("OracleBot: no EventBus available — running in scan-loop fallback mode")
            await super().start()

    async def stop(self):
        event_bus = getattr(self.base_engine, "event_bus", None)
        if event_bus:
            try:
                event_bus.unsubscribe("proposed_outcome", self._on_proposed_outcome)
            except Exception as e:
                logger.debug("event bus unsubscribe failed: %s", e)
        self.running = False
        await super().stop()

    async def _on_proposed_outcome(self, event: Dict[str, Any]) -> None:
        """Handle a proposed_outcome event from the UMA proposal monitor."""
        if not self.running:
            return
        try:
            market_id = event.get("market_id")
            proposed_outcome = event.get("proposed_outcome")  # 0 or 1
            assertion_id = event.get("assertionId", "")

            if market_id is None or proposed_outcome is None:
                logger.debug("OracleBot: incomplete proposed_outcome event")
                return

            proposed_side = "YES" if proposed_outcome == 1 else "NO"
            logger.info(
                "OracleBot: proposal detected",
                market_id=market_id, proposed=proposed_side, assertion_id=assertion_id,
            )

            # Check existing positions
            positions = await self._get_positions_on_market(market_id)

            if positions:
                await self._handle_existing_positions(market_id, proposed_side, positions)
            else:
                await self._buy_proposed_outcome(market_id, proposed_side)

        except Exception as e:
            logger.warning("OracleBot: error handling proposed_outcome: %s", e, exc_info=True)

    async def _get_positions_on_market(self, market_id: str) -> List[Dict]:
        """Get open positions on a market (any bot)."""
        db = getattr(self.base_engine, "db", None)
        if not db or not getattr(db, "session_factory", None):
            return []
        try:
            from sqlalchemy import select
            from base_engine.data.database import Position
            async with db.get_session() as session:
                r = await session.execute(
                    select(Position).where(
                        Position.market_id == market_id,
                        Position.status == "open",
                    )
                )
                return [
                    {"id": p.id, "side": p.side, "size": p.size, "entry_price": p.entry_price, "bot_id": p.bot_id}
                    for p in r.scalars().all()
                ]
        except Exception as e:
            logger.debug("get positions on market failed: %s", e)
            return []

    async def _handle_existing_positions(self, market_id: str, proposed_side: str, positions: List[Dict]):
        """React to proposal when we have existing positions."""
        for pos in positions:
            pos_side = (pos.get("side") or "").upper()
            if pos_side == proposed_side:
                # Position aligns: hold confidently
                logger.info(
                    "OracleBot: position aligns with proposal — holding",
                    market_id=market_id, side=pos_side, size=pos.get("size"),
                )
            else:
                # Position contradicts: EXIT immediately
                logger.warning(
                    "OracleBot: position CONTRADICTS proposal — exiting",
                    market_id=market_id, pos_side=pos_side, proposed=proposed_side,
                )
                rm = getattr(self.base_engine, "risk_manager", None)
                if rm and hasattr(rm, "close_position"):
                    try:
                        await rm.close_position(pos.get("bot_id", self.bot_name), market_id, 0.0)
                    except Exception as e:
                        logger.error("OracleBot: exit failed: %s", e)

    async def _buy_proposed_outcome(self, market_id: str, proposed_side: str):
        """Buy the proposed outcome if price is below max entry price."""
        try:
            market = await self.base_engine.get_market(market_id)
            if not market:
                return

            tokens = market.get("tokens", [])
            if not tokens:
                return

            # Find the token for the proposed side
            target_token = None
            target_price = None
            for t in tokens:
                if not isinstance(t, dict):
                    continue
                outcome = (t.get("outcome") or "").upper()
                if outcome == proposed_side:
                    target_token = t
                    break
            # Fallback: first token for YES, second for NO
            if not target_token:
                if proposed_side == "YES" and len(tokens) >= 1:
                    target_token = tokens[0]
                elif proposed_side == "NO" and len(tokens) >= 2:
                    target_token = tokens[1]

            if not target_token:
                return

            token_id = target_token.get("tokenId") or target_token.get("token_id")
            price_raw = target_token.get("outcomePrice") or target_token.get("price")
            if not token_id or price_raw is None:
                return

            price = float(price_raw)
            if price <= 0 or price > self._max_entry_price:
                logger.debug("OracleBot: price %.3f above max entry %.3f — skipping", price, self._max_entry_price)
                return

            size = min(self._max_position_size, await self.calculate_bot_position_size(0.95, price))
            if size <= 0:
                return

            # S145: Populate signal meta for auto-store in place_order()
            self._pending_signal_meta[str(market_id)] = {
                "signal_direction": proposed_side,
                "signal_confidence": 0.95,
                "signal_source": "oracle_feed",
                "signal_multiplier": None,
                "order_flow_direction": None,
                "order_flow_multiplier": None,
                "trends_signal": None,
                "trends_multiplier": None,
            }

            order = await self.place_order(
                market_id=market_id,
                token_id=str(token_id),
                side="BUY",
                size=size,
                price=price,
                confidence=0.95,
            )
            if order.get("success"):
                logger.info(
                    "OracleBot: bought proposed outcome %s @ %.3f (expected ~%.1f%% return)",
                    proposed_side, price, (1.0 - price) * 100,
                    market_id=market_id, side=proposed_side, price=price,
                )
        except Exception as e:
            logger.warning("OracleBot: buy proposed outcome failed: %s", e)

    async def scan_and_trade(self):
        """Fallback scan loop (only used if EventBus unavailable)."""
        pass  # OracleBot is event-driven; this is a no-op

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        return None  # Event-driven only
