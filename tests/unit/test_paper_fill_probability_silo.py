"""Silo-side tests for the WB-port of the directional slippage fix.

Mirrors the 3 tests added in MB's slippage commit b9f3082 against the
silo'd PaperTradingEngine at bots.weather.engine.base_engine.execution.
paper_trading. The silo is a clone of base_engine/, so this file exists
to give the WB tree direct coverage of its own copy of the fix.

If the silo drifts from canonical base_engine/, the canonical tests in
test_paper_fill_probability.py keep covering the original; these stay
locked to the silo.
"""
import pytest
from unittest.mock import patch

from bots.weather.engine.base_engine.execution.paper_trading import PaperTradingEngine


class TestSiloDirectionalSlippage:
    def _make_engine(self):
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        return engine

    @pytest.mark.asyncio
    async def test_sell_favorable_book_walk_passes(self):
        """SELL with VWAP > original (stale-mid scenario) must succeed — favorable, not adverse."""
        engine = self._make_engine()
        engine.positions[("paper_trader", "mkt_stale")] = {
            "size": 50.0, "avg_price": 0.20, "token_id": "tok_stale",
            "side": "NO", "entry_fee": 0.0,
        }
        with patch("bots.weather.engine.base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="mkt_stale", token_id="tok_stale", side="SELL",
                size=50.0, price=0.12, bot_name="paper_trader",
                bid=0.12, ask=0.13, confidence=0.80,
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.20,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.0,
                    "_shadow_best_bid": 0.20,
                },
            )
        assert result["success"] is True
        assert abs(result["price"] - 0.20) < 0.001

    @pytest.mark.asyncio
    async def test_sell_adverse_book_walk_rejected(self):
        """SELL with VWAP << original (collapsed book) must reject as adverse slippage."""
        engine = self._make_engine()
        engine.positions[("paper_trader", "mkt_collapse")] = {
            "size": 50.0, "avg_price": 0.50, "token_id": "tok_collapse",
            "side": "YES", "entry_fee": 0.0,
        }
        with patch("bots.weather.engine.base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="mkt_collapse", token_id="tok_collapse", side="SELL",
                size=50.0, price=0.50, bot_name="paper_trader",
                bid=0.50, ask=0.51, confidence=0.80,
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.40,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.10,
                    "_shadow_best_bid": 0.50,
                },
            )
        assert result["success"] is False
        assert result["fail_code"] == "slippage"
        assert "Adverse slippage" in result["error"]
        assert "side=SELL" in result["error"]

    @pytest.mark.asyncio
    async def test_buy_adverse_book_walk_rejected(self):
        """BUY with VWAP >> original (book ran away) must still reject as adverse slippage."""
        engine = self._make_engine()
        with patch("bots.weather.engine.base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="m_runaway", token_id="t_runaway", side="BUY",
                size=10.0, price=0.50, bot_name="test",
                bid=0.49, ask=0.50, confidence=0.60,
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.60,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.10,
                    "_shadow_best_ask": 0.50,
                },
            )
        assert result["success"] is False
        assert result["fail_code"] == "slippage"
        assert "Adverse slippage" in result["error"]
        assert "side=BUY" in result["error"]
