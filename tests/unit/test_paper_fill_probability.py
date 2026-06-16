"""Tests for S115 shadow fill tracking and VWAP book walk fills."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from base_engine.execution.paper_trading import (
    _vwap_from_book,
    _vwap_from_bids,
    _parse_book_level,
    PaperTradingEngine,
)


class TestParseBookLevel:
    """S245 R2: the shared level-parse/phantom filter used by the walkers AND
    order_gateway's shadow best-ask/bid scan. Centralizing it is what stops the S232
    drift class — so its contract is pinned here."""

    def test_valid_level_price_size_keys(self):
        assert _parse_book_level({"price": "0.55", "size": "100"}) == (0.55, 100.0)

    def test_valid_level_p_s_fallback_keys(self):
        assert _parse_book_level({"p": 0.42, "s": 50.0}) == (0.42, 50.0)

    def test_size_zero_phantom_rejected(self):
        assert _parse_book_level({"price": 0.38, "size": 0}) is None

    def test_price_zero_rejected(self):
        assert _parse_book_level({"price": 0, "size": 100.0}) is None

    def test_negative_rejected(self):
        assert _parse_book_level({"price": -0.1, "size": 100.0}) is None
        assert _parse_book_level({"price": 0.5, "size": -5.0}) is None

    def test_string_garbage_rejected(self):
        assert _parse_book_level({"price": "bad", "size": 100.0}) is None

    def test_non_dict_rejected(self):
        assert _parse_book_level(None) is None
        assert _parse_book_level([0.5, 100]) is None


# ── _vwap_from_book() unit tests ────────────────────────────────────────


class TestVwapFromBook:
    def test_single_level_full_fill(self):
        """Single ask level with enough depth → fills at best ask."""
        asks = [{"price": "0.55", "size": "100"}]
        result = _vwap_from_book(asks, order_size_shares=50.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert abs(vwap - 0.55) < 0.001
        assert abs(fill_frac - 1.0) < 0.001
        assert abs(slippage) < 0.001

    def test_walks_multiple_levels(self):
        """Order exceeds best level → walks to second level, VWAP between levels."""
        asks = [
            {"price": "0.50", "size": "100"},
            {"price": "0.52", "size": "200"},
        ]
        result = _vwap_from_book(asks, order_size_shares=150.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        # 100 shares at 0.50 + 50 shares at 0.52 = (50 + 26) / 150 = 0.5067
        expected_vwap = (100 * 0.50 + 50 * 0.52) / 150
        assert abs(vwap - expected_vwap) < 0.001
        assert abs(fill_frac - 1.0) < 0.001
        assert slippage > 0  # VWAP > best ask

    def test_partial_fill_thin_book(self):
        """Book too thin → partial fill, fraction < 1.0."""
        asks = [{"price": "0.60", "size": "30"}]
        result = _vwap_from_book(asks, order_size_shares=100.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert abs(fill_frac - 0.30) < 0.01
        assert abs(vwap - 0.60) < 0.001

    def test_empty_book_returns_none(self):
        """Empty ask book → None."""
        assert _vwap_from_book([], order_size_shares=10.0) is None

    def test_zero_size_returns_none(self):
        """Zero order size → None."""
        asks = [{"price": "0.55", "size": "100"}]
        assert _vwap_from_book(asks, order_size_shares=0) is None

    def test_whale_consumes_liquidity(self):
        """Whale size subtracted from book before copier fills."""
        asks = [
            {"price": "0.50", "size": "100"},
            {"price": "0.52", "size": "200"},
        ]
        # Whale takes 80 shares at best level, copier gets 20 at 0.50 + rest at 0.52
        result = _vwap_from_book(asks, order_size_shares=50.0, whale_size_shares=80.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        # 20 shares at 0.50 + 30 shares at 0.52
        expected_vwap = (20 * 0.50 + 30 * 0.52) / 50
        assert abs(vwap - expected_vwap) < 0.001

    # ── S232: walker phantom-level filter contract (LIVE top-level walker) ──
    # These lock in the invariant the order_gateway S232 fix depends on: the
    # walker — and the gateway's _shadow_best_ask/_shadow_best_bid computation
    # that mirrors it — must skip size-0 phantom top-of-book levels. Originally
    # only verified against the silo copy (b9b25ce); WeatherBot runs the
    # top-level engine, so the invariant is asserted here on the live walker.

    def test_s232_vwap_from_book_skips_size_zero_phantom_top(self):
        """A size=0 level at the best ask is treated as non-existent; the first
        NON-PHANTOM level is the effective best_ask. Production book shape for
        0xe7b4ced4 (2026-05-28)."""
        asks = [
            {"price": 0.38, "size": 0},     # phantom — top of raw, but size=0
            {"price": 0.65, "size": 50.0},  # real first level
            {"price": 0.70, "size": 100.0},
        ]
        result = _vwap_from_book(asks, order_size_shares=10.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert abs(vwap - 0.65) < 1e-6
        assert abs(fill_frac - 1.0) < 1e-6
        assert abs(slippage) < 1e-6

    def test_s232_vwap_from_bids_skips_size_zero_phantom_top(self):
        """Walker contract mirror for the bid side."""
        bids = [
            {"price": 0.62, "size": 0},     # phantom
            {"price": 0.35, "size": 50.0},  # real first level
            {"price": 0.30, "size": 100.0},
        ]
        result = _vwap_from_bids(bids, order_size_shares=10.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert abs(vwap - 0.35) < 1e-6
        assert abs(fill_frac - 1.0) < 1e-6
        assert abs(slippage) < 1e-6

    def test_s232_vwap_from_book_skips_invalid_price_and_dust_size(self):
        """Walker filters anything that isn't p>0 and s>0 — same filter the
        order_gateway S232 fix uses, so the anchor and the walk stay symmetric."""
        asks = [
            {"price": 0, "size": 100.0},      # price=0 — filtered
            {"price": 0.38, "size": 0.0},     # size=0 — filtered
            {"price": -0.1, "size": 100.0},   # negative price — filtered
            {"price": 0.50, "size": -5.0},    # negative size — filtered
            {"price": "invalid", "size": 100.0},  # string price — filtered (ValueError)
            {"price": 0.65, "size": 50.0},    # first valid
        ]
        result = _vwap_from_book(asks, order_size_shares=10.0)
        assert result is not None
        vwap, _, _ = result
        assert abs(vwap - 0.65) < 1e-6


# ── Paper Trading Integration (S115 flow) ────────────────────────────────


class TestShadowFillFlow:
    def _make_engine(self):
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        return engine

    @pytest.mark.asyncio
    async def test_buy_without_orderbook_tracker(self):
        """Without orderbook tracker, fills at signal price (no book walk)."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=10.0, price=0.55, bot_name="test",
                bid=0.54, ask=0.56,
            )
        assert result["success"] is True
        # Without book tracker, fills at ask (B4 spread-side anchor)
        assert abs(result["price"] - 0.56) < 0.001

    @pytest.mark.asyncio
    async def test_buy_with_book_walk(self):
        """With book walk data from order_gateway, fills at VWAP."""
        engine = self._make_engine()

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            # Simulate order_gateway passing book walk results via event_data
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=50.0, price=0.55, bot_name="test",
                bid=0.53, ask=0.55, confidence=0.60,
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.55,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.0,
                    "_shadow_book_snapshot": [{"price": "0.55", "size": "100"}],
                    "_shadow_best_ask": 0.55,
                    "_shadow_best_bid": 0.53,
                    "_shadow_spread": 0.02,
                    "_shadow_depth_best": 55.0,
                    "_shadow_total_depth": 55.0,
                },
            )
        assert result["success"] is True
        assert result["book_walk_used"] is True
        assert abs(result["price"] - 0.55) < 0.001

    @pytest.mark.asyncio
    async def test_book_walk_slippage_on_large_order(self):
        """Large order walks book → VWAP higher than best ask."""
        engine = self._make_engine()
        # Pre-compute VWAP: 100@0.50 + 100@0.52 + 50@0.54 = 127/250 = 0.508
        _vwap = (100 * 0.50 + 100 * 0.52 + 50 * 0.54) / 250

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=250.0, price=0.50, bot_name="test",
                bid=0.49, ask=0.50, confidence=0.60,
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": _vwap,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": _vwap - 0.50,
                    "_shadow_best_ask": 0.50,
                },
            )
        assert result["success"] is True
        assert result["price"] > 0.50
        assert result["book_walk_used"] is True

    @pytest.mark.asyncio
    async def test_sell_always_fills(self):
        """SELL orders always fill — no book walk applied."""
        engine = self._make_engine()
        engine.positions[("paper_trader", "mkt1")] = {
            "size": 50.0, "avg_price": 0.50, "token_id": "tok1",
            "side": "YES", "entry_fee": 0.0,
        }
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="mkt1", token_id="tok1", side="SELL",
                size=50.0, price=0.50, bid=0.49, ask=0.51,
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_partial_fill_from_thin_book(self):
        """Thin book → partial fill via fill_fraction."""
        engine = self._make_engine()

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=100.0, price=0.50, bot_name="test",
                bid=0.49, ask=0.50, confidence=0.60,
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.50,
                    "_shadow_fill_frac": 0.05,  # Only 5 shares of 100
                    "_shadow_slippage": 0.0,
                    "_shadow_best_ask": 0.50,
                },
            )
        assert result["success"] is True
        # Should fill only 5 shares (5% of 100)
        assert result["filled"] < 100.0
        assert result["fill_fraction"] < 1.0

    @pytest.mark.asyncio
    async def test_edge_gate_rejects_negative_edge(self):
        """S115: Edge-at-VWAP gate rejects when confidence <= VWAP price."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            # Confidence 0.49, VWAP 0.50 → negative edge → rejected
            # Note: edge gate now lives in order_gateway, but paper_trading
            # still rejects via B4 anchor (ask=0.50 > confidence=0.49)
            # when the book walk shows confidence <= price
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=10.0, price=0.50, bot_name="test",
                confidence=0.49, bid=0.49, ask=0.50,
            )
        # Without book walk data, fills at ask (0.50) > confidence (0.49)
        # Paper engine doesn't have edge gate anymore — that's in order_gateway
        # So this trade goes through (order_gateway would catch it in real flow)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_edge_gate_allows_positive_edge(self):
        """S115: Trade proceeds when confidence > fill price (positive edge)."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=10.0, price=0.50, bot_name="test",
                confidence=0.60, bid=0.49, ask=0.50,
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_book_walk_data_fills_at_ask(self):
        """Without book walk data from order_gateway, fills at ask (B4 anchor)."""
        engine = self._make_engine()

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=10.0, price=0.55, bot_name="test",
                bid=0.54, ask=0.56,
            )
        assert result["success"] is True
        # Falls back to ask price (B4 anchor)
        assert abs(result["price"] - 0.56) < 0.001


class TestS231AnchorTopLevel:
    """S231 anchor regression on the LIVE top-level PaperTradingEngine.

    The S231 fix originally landed only in the silo copy (c8f5f86); WeatherBot
    runs the TOP-LEVEL base_engine (S239: ExecStart=main.py injects it), so the
    fix was dead code until ported here. These tests exercise the top-level engine
    directly so the anchor fix cannot silently regress to the synthetic-anchor bug.

    Live evidence the fix targets (2026-06-11, market 0xd63e43.., Lucknow NO@0.74):
    `Paper trade FAILED — Slippage 183.0% exceeds 10% limit (original=0.2700,
    fill=0.7640)`. The 0.27 anchor was the YES-token synthetic mid (1 - 0.74),
    compared against the real NO-book VWAP 0.764. Top failure pairs all summed to
    ~1.0 (0.40/0.61, 0.42/0.60, 0.21/0.82) — the YES-anchor-vs-NO-fill fingerprint.
    """

    def _make_engine(self):
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        return engine

    @pytest.mark.asyncio
    async def test_buy_anchor_uses_real_shadow_over_synthetic_bid_ask(self):
        """BUY (Lucknow NO pattern): synthetic ask=0.27 (1-0.74 YES mid) vs real
        _shadow_best_ask=0.74 and VWAP=0.764. Pre-fix anchored to 0.27 → 183%
        adverse → REJECT. Post-fix anchors to real 0.74 → ~3% → SUCCESS."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="m_lucknow", token_id="t_lucknow", side="BUY",
                size=10.0, price=0.74, bot_name="test", original_side="NO",
                bid=0.26, ask=0.27, confidence=0.93,  # synthetic YES-denominated
                event_data={
                    "_shadow_book_walk_used": True,
                    "_shadow_vwap": 0.764,
                    "_shadow_fill_frac": 1.0,
                    "_shadow_slippage": 0.0,
                    "_shadow_best_ask": 0.74,  # REAL NO top-of-book
                    "_shadow_best_bid": 0.72,
                },
            )
        assert result["success"] is True, f"expected success after S231 fix, got {result}"
        assert abs(result["price"] - 0.764) < 0.001  # fill at real NO VWAP

    @pytest.mark.asyncio
    async def test_sell_anchor_uses_real_shadow_over_synthetic_bid_ask(self):
        """SELL mirror: synthetic bid=0.60 vs real _shadow_best_bid=0.35, VWAP=0.35.
        Pre-fix adverse = 0.60-0.35 = 42% → REJECT. Post-fix anchors to 0.35 → PASS."""
        engine = self._make_engine()
        engine.positions[("test", "m_thin_sell")] = {
            "size": 50.0, "avg_price": 0.50, "token_id": "t_thin_sell",
            "side": "YES", "entry_fee": 0.0,
        }
        with patch("base_engine.execution.paper_trading.settings") as ms:
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
    async def test_anchor_falls_back_to_bid_ask_when_shadow_absent(self):
        """Backward-compat: no _shadow_best_* keys → unchanged B4 synthetic anchor."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 0
            result = await engine.place_order(
                market_id="m1", token_id="t1", side="BUY",
                size=10.0, price=0.55, bot_name="test",
                bid=0.54, ask=0.56,
            )
        assert result["success"] is True
        assert abs(result["price"] - 0.56) < 0.001  # B4 ask anchor unchanged
