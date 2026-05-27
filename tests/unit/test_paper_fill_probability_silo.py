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

    # ── S231: anchor-source regression (synthetic bid/ask vs real shadow top-of-book) ──

    @pytest.mark.asyncio
    async def test_s231_buy_anchor_uses_real_shadow_over_synthetic_bid_ask(self):
        """S231 (Helsinki pattern from S230 audit): when _shadow_best_ask is present,
        the slippage anchor uses it instead of the synthetic bid/ask params from
        order_gateway. Pre-fix: synthetic ask=0.40 vs real VWAP=0.65 = 62% adverse
        → REJECT (all 11 post-fix Helsinki attempts failed this way). Post-fix:
        anchor = _shadow_best_ask=0.65 matches walk → no adverse slippage → SUCCESS."""
        engine = self._make_engine()
        with patch("bots.weather.engine.base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="m_helsinki", token_id="t_helsinki", side="BUY",
                size=10.0, price=0.595, bot_name="test", original_side="NO",
                bid=0.39, ask=0.40, confidence=0.88,  # synthetic Helsinki-pattern
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.65,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.0,
                    "_shadow_best_ask": 0.65,  # REAL top-of-book
                    "_shadow_best_bid": 0.60,
                },
            )
        assert result["success"] is True, f"expected success after S231 fix, got {result}"
        assert abs(result["price"] - 0.65) < 0.001  # fill at real VWAP

    @pytest.mark.asyncio
    async def test_s231_sell_anchor_uses_real_shadow_over_synthetic_bid_ask(self):
        """S231 SELL mirror: synthetic bid=0.60 vs real _shadow_best_bid=0.35,
        real VWAP=0.35. Pre-fix: SELL adverse_abs = max(0, 0.60-0.35) = 0.25
        → 42% adverse → REJECT. Post-fix: anchor = 0.35 → adverse_abs = 0 → PASS."""
        engine = self._make_engine()
        engine.positions[("test", "m_thin_sell")] = {
            "size": 50.0, "avg_price": 0.50, "token_id": "t_thin_sell",
            "side": "YES", "entry_fee": 0.0,
        }
        with patch("bots.weather.engine.base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="m_thin_sell", token_id="t_thin_sell", side="SELL",
                size=10.0, price=0.50, bot_name="test",
                bid=0.60, ask=0.61, confidence=0.80,  # synthetic, far from real
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.35,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.0,
                    "_shadow_best_bid": 0.35,  # REAL top-of-book
                    "_shadow_best_ask": 0.40,
                },
            )
        assert result["success"] is True, f"expected success after S231 fix, got {result}"
        assert abs(result["price"] - 0.35) < 0.001

    @pytest.mark.asyncio
    async def test_s231_anchor_falls_back_to_bid_ask_when_shadow_absent(self):
        """S231 backward-compat: without _shadow_best_ask/_shadow_best_bid in event_data,
        anchor falls back to bid/ask params (legacy callers). Same scenario as
        test_buy_adverse_book_walk_rejected but explicitly omits shadow_best keys
        to lock in the fallback path. Original=ask=0.50, VWAP=0.60, slip=20% → REJECT."""
        engine = self._make_engine()
        with patch("bots.weather.engine.base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="m_no_shadow", token_id="t_no_shadow", side="BUY",
                size=10.0, price=0.50, bot_name="test",
                bid=0.49, ask=0.50, confidence=0.60,
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.60,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.10,
                    # _shadow_best_ask / _shadow_best_bid intentionally OMITTED
                },
            )
        assert result["success"] is False
        assert result["fail_code"] == "slippage"
        assert "original=0.5000" in result["error"]  # anchor from ask param (fallback)
