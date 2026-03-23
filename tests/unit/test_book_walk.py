"""Tests for _vwap_from_book() — L2 order book walk with whale impact subtraction."""
import pytest
from base_engine.execution.paper_trading import _vwap_from_book


class TestVwapFromBook:
    """Core book walk logic."""

    def _make_asks(self, levels):
        """Helper: [(price, size), ...] -> [{"price": p, "size": s}, ...]"""
        return [{"price": p, "size": s} for p, s in levels]

    def test_empty_asks_returns_none(self):
        assert _vwap_from_book([], 100.0) is None

    def test_zero_order_size_returns_none(self):
        asks = self._make_asks([(0.50, 1000)])
        assert _vwap_from_book(asks, 0.0) is None

    def test_single_level_exact_fill(self):
        asks = self._make_asks([(0.60, 500)])
        result = _vwap_from_book(asks, 500.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert vwap == pytest.approx(0.60)
        assert fill_frac == pytest.approx(1.0)
        assert slippage == pytest.approx(0.0)

    def test_multi_level_walk(self):
        """Order walks through 3 price levels."""
        asks = self._make_asks([(0.50, 100), (0.51, 100), (0.52, 100)])
        result = _vwap_from_book(asks, 250.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        # 100@0.50 + 100@0.51 + 50@0.52 = 50+51+26 = 127 / 250 shares
        expected_vwap = (100 * 0.50 + 100 * 0.51 + 50 * 0.52) / 250
        assert vwap == pytest.approx(expected_vwap, abs=0.0001)
        assert fill_frac == pytest.approx(1.0)
        assert slippage > 0  # VWAP > best ask

    def test_insufficient_depth_partial_fill(self):
        asks = self._make_asks([(0.50, 100), (0.51, 50)])
        result = _vwap_from_book(asks, 200.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert fill_frac == pytest.approx(150.0 / 200.0)
        assert vwap == pytest.approx((100 * 0.50 + 50 * 0.51) / 150, abs=0.0001)

    def test_unsorted_asks_are_sorted(self):
        """Asks in random order should be sorted ascending by price."""
        asks = self._make_asks([(0.55, 100), (0.50, 100), (0.52, 100)])
        result = _vwap_from_book(asks, 100.0)
        assert result is not None
        vwap, _, slippage = result
        assert vwap == pytest.approx(0.50)  # cheapest level first
        assert slippage == pytest.approx(0.0)


class TestWhaleImpactSubtraction:
    """Whale depletes ask-side liquidity before copier arrives."""

    def _make_asks(self, levels):
        return [{"price": p, "size": s} for p, s in levels]

    def test_no_whale_no_change(self):
        asks = self._make_asks([(0.50, 200)])
        result = _vwap_from_book(asks, 100.0, whale_size_shares=0.0)
        assert result is not None
        vwap, fill_frac, _ = result
        assert vwap == pytest.approx(0.50)
        assert fill_frac == pytest.approx(1.0)

    def test_whale_consumes_first_level(self):
        """Whale eats 200 shares at 0.50 → copier starts at 0.51."""
        asks = self._make_asks([(0.50, 200), (0.51, 300)])
        result = _vwap_from_book(asks, 100.0, whale_size_shares=200.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert vwap == pytest.approx(0.51)
        assert fill_frac == pytest.approx(1.0)
        assert slippage == pytest.approx(0.01)  # 0.51 - 0.50

    def test_whale_partially_consumes_level(self):
        """Whale eats 150 of 200 shares at 0.50 → 50 left for copier."""
        asks = self._make_asks([(0.50, 200), (0.52, 300)])
        result = _vwap_from_book(asks, 100.0, whale_size_shares=150.0)
        assert result is not None
        vwap, fill_frac, _ = result
        # 50 @ 0.50 + 50 @ 0.52 = 25 + 26 = 51 / 100 = 0.51
        assert vwap == pytest.approx(0.51, abs=0.001)
        assert fill_frac == pytest.approx(1.0)

    def test_whale_consumes_entire_book(self):
        """Whale eats everything → copier gets None."""
        asks = self._make_asks([(0.50, 100), (0.51, 100)])
        result = _vwap_from_book(asks, 50.0, whale_size_shares=200.0)
        assert result is None

    def test_whale_larger_than_book(self):
        """Whale size exceeds total book → copier gets None."""
        asks = self._make_asks([(0.50, 100)])
        result = _vwap_from_book(asks, 50.0, whale_size_shares=500.0)
        assert result is None

    def test_whale_leaves_copier_partial_fill(self):
        """Whale eats most depth → copier can only partially fill."""
        asks = self._make_asks([(0.50, 300), (0.51, 100)])
        # Whale eats 350 → 50 left at 0.51
        result = _vwap_from_book(asks, 100.0, whale_size_shares=350.0)
        assert result is not None
        vwap, fill_frac, _ = result
        assert fill_frac == pytest.approx(50.0 / 100.0)
        assert vwap == pytest.approx(0.51)

    def test_real_world_scenario(self):
        """Simulate a realistic MirrorBot scenario:
        Whale buys $5000 worth (~10000 shares at 0.50).
        Book has 20 levels. Copier wants 200 shares (~$100).
        """
        asks = self._make_asks([
            (0.50, 2000), (0.505, 1500), (0.51, 3000), (0.515, 2000),
            (0.52, 1500), (0.525, 1000), (0.53, 2000), (0.535, 1000),
            (0.54, 500), (0.545, 500),
        ])
        # Whale buys 10000 shares → eats 0.50(2000) + 0.505(1500) + 0.51(3000) + 0.515(2000) + 0.52(1500) = 10000
        result = _vwap_from_book(asks, 200.0, whale_size_shares=10000.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        # Copier starts at 0.525 level (1000 available)
        assert vwap == pytest.approx(0.525)
        assert fill_frac == pytest.approx(1.0)
        assert slippage > 0.02  # at least 2.5 cents worse than best ask


class TestEdgeCases:
    """Malformed data, edge cases."""

    def _make_asks(self, levels):
        return [{"price": p, "size": s} for p, s in levels]

    def test_string_prices_and_sizes(self):
        """Polymarket API returns strings — must handle."""
        asks = [{"price": "0.50", "size": "100"}, {"price": "0.51", "size": "200"}]
        result = _vwap_from_book(asks, 100.0)
        assert result is not None
        assert result[0] == pytest.approx(0.50)

    def test_malformed_levels_skipped(self):
        asks = [{"price": "abc", "size": "100"}, {"price": 0.50, "size": 100}]
        result = _vwap_from_book(asks, 50.0)
        assert result is not None
        assert result[0] == pytest.approx(0.50)

    def test_zero_price_levels_skipped(self):
        asks = [{"price": 0, "size": 100}, {"price": 0.50, "size": 100}]
        result = _vwap_from_book(asks, 50.0)
        assert result is not None
        assert result[0] == pytest.approx(0.50)

    def test_negative_whale_treated_as_zero(self):
        asks = self._make_asks([(0.50, 200)])
        result = _vwap_from_book(asks, 100.0, whale_size_shares=-50.0)
        assert result is not None
        assert result[0] == pytest.approx(0.50)


class TestVwapFromBids:
    """S121: Bid-side book walk for SELL orders — mirrors ask-side walk."""

    def _make_bids(self, levels):
        """Helper: [(price, size), ...] -> [{"price": p, "size": s}, ...]"""
        return [{"price": p, "size": s} for p, s in levels]

    def test_empty_bids_returns_none(self):
        from base_engine.execution.paper_trading import _vwap_from_bids
        assert _vwap_from_bids([], 100.0) is None

    def test_zero_order_size_returns_none(self):
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = self._make_bids([(0.50, 1000)])
        assert _vwap_from_bids(bids, 0.0) is None

    def test_single_level_exact_fill(self):
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = self._make_bids([(0.60, 500)])
        result = _vwap_from_bids(bids, 500.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert vwap == pytest.approx(0.60)
        assert fill_frac == pytest.approx(1.0)
        assert slippage == pytest.approx(0.0)

    def test_multi_level_walk_descending(self):
        """SELL walks bids top-down: best bid first, then worse bids."""
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = self._make_bids([(0.50, 100), (0.49, 100), (0.48, 100)])
        result = _vwap_from_bids(bids, 250.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        # 100@0.50 + 100@0.49 + 50@0.48 = 50+49+24 = 123 / 250
        expected_vwap = (100 * 0.50 + 100 * 0.49 + 50 * 0.48) / 250
        assert vwap == pytest.approx(expected_vwap, abs=0.0001)
        assert fill_frac == pytest.approx(1.0)
        assert slippage > 0  # best_bid - VWAP > 0

    def test_insufficient_depth_partial_fill(self):
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = self._make_bids([(0.50, 100), (0.49, 50)])
        result = _vwap_from_bids(bids, 200.0)
        assert result is not None
        vwap, fill_frac, slippage = result
        assert fill_frac == pytest.approx(150.0 / 200.0)
        assert vwap == pytest.approx((100 * 0.50 + 50 * 0.49) / 150, abs=0.0001)

    def test_unsorted_bids_are_sorted_descending(self):
        """Bids in random order should be sorted descending by price."""
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = self._make_bids([(0.45, 100), (0.50, 100), (0.48, 100)])
        result = _vwap_from_bids(bids, 100.0)
        assert result is not None
        vwap, _, slippage = result
        assert vwap == pytest.approx(0.50)  # best bid first
        assert slippage == pytest.approx(0.0)

    def test_string_prices_and_sizes(self):
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = [{"price": "0.50", "size": "100"}, {"price": "0.49", "size": "200"}]
        result = _vwap_from_bids(bids, 100.0)
        assert result is not None
        assert result[0] == pytest.approx(0.50)

    def test_malformed_levels_skipped(self):
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = [{"price": "abc", "size": "100"}, {"price": 0.50, "size": 100}]
        result = _vwap_from_bids(bids, 50.0)
        assert result is not None
        assert result[0] == pytest.approx(0.50)

    def test_slippage_is_nonnegative(self):
        """Slippage = best_bid - vwap, should always be >= 0."""
        from base_engine.execution.paper_trading import _vwap_from_bids
        bids = self._make_bids([(0.50, 50), (0.48, 50), (0.45, 100)])
        result = _vwap_from_bids(bids, 150.0)
        assert result is not None
        _, _, slippage = result
        assert slippage >= 0


class TestBookDepletion:
    """S121: Consecutive fills on same token deplete the book progressively."""

    def test_depletion_worsens_vwap(self):
        """Three BUY fills on same token — VWAP should increase each time."""
        from base_engine.execution.paper_trading import PaperTradingEngine, _vwap_from_book
        engine = PaperTradingEngine(initial_capital=1_000_000)

        book = [{"price": 0.50, "size": 100}, {"price": 0.55, "size": 100}, {"price": 0.60, "size": 100}]
        token_id = "test_token_123"
        depletion_key = (token_id, "ask")

        # First fill: 80 shares from fresh book
        result1 = _vwap_from_book(book, 80.0)
        assert result1 is not None
        engine._update_book_depletion(depletion_key, book, 80.0, "ask")

        # Second fill: 80 shares from depleted book
        depleted = engine._scan_book_state[depletion_key]
        depleted_book = [{"price": p, "size": s} for p, s in depleted[0]]
        result2 = _vwap_from_book(depleted_book, 80.0)
        assert result2 is not None
        engine._update_book_depletion(depletion_key, depleted_book, 80.0, "ask")

        # Third fill: 80 shares from further depleted book
        depleted = engine._scan_book_state[depletion_key]
        depleted_book = [{"price": p, "size": s} for p, s in depleted[0]]
        result3 = _vwap_from_book(depleted_book, 80.0)
        assert result3 is not None

        # VWAP should worsen (increase) with each fill
        assert result2[0] > result1[0], "Second fill should be worse than first"
        assert result3[0] > result2[0], "Third fill should be worse than second"

    def test_depletion_exhausts_book(self):
        """After enough fills, book is exhausted — returns None."""
        from base_engine.execution.paper_trading import PaperTradingEngine, _vwap_from_book
        engine = PaperTradingEngine(initial_capital=1_000_000)

        book = [{"price": 0.50, "size": 100}]
        token_id = "test_exhaust"
        depletion_key = (token_id, "ask")

        # Fill all 100 shares
        engine._update_book_depletion(depletion_key, book, 100.0, "ask")

        # Next fill should see empty book
        depleted = engine._scan_book_state[depletion_key]
        depleted_book = [{"price": p, "size": s} for p, s in depleted[0]]
        result = _vwap_from_book(depleted_book, 50.0)
        assert result is None, "Exhausted book should return None"

    def test_depletion_bid_side(self):
        """SELL-side depletion walks bids descending."""
        from base_engine.execution.paper_trading import PaperTradingEngine, _vwap_from_bids
        engine = PaperTradingEngine(initial_capital=1_000_000)

        book = [{"price": 0.50, "size": 100}, {"price": 0.48, "size": 100}]
        token_id = "test_bid_deplete"
        depletion_key = (token_id, "bid")

        # First fill: sell 80 shares (walks from 0.50 down)
        result1 = _vwap_from_bids(book, 80.0)
        assert result1 is not None
        engine._update_book_depletion(depletion_key, book, 80.0, "bid")

        # Second fill: should get worse price (more from 0.48)
        depleted = engine._scan_book_state[depletion_key]
        depleted_book = [{"price": p, "size": s} for p, s in depleted[0]]
        result2 = _vwap_from_bids(depleted_book, 80.0)
        assert result2 is not None
        assert result2[0] < result1[0], "Second SELL fill should get worse (lower) price"

    def test_depletion_independent_tokens(self):
        """Depletion on token A doesn't affect token B."""
        from base_engine.execution.paper_trading import PaperTradingEngine
        engine = PaperTradingEngine(initial_capital=1_000_000)

        book_a = [{"price": 0.50, "size": 100}]
        book_b = [{"price": 0.60, "size": 200}]

        engine._update_book_depletion(("token_a", "ask"), book_a, 90.0, "ask")
        engine._update_book_depletion(("token_b", "ask"), book_b, 50.0, "ask")

        state_a = engine._scan_book_state[("token_a", "ask")]
        state_b = engine._scan_book_state[("token_b", "ask")]

        # Token A: 100 - 90 = 10 remaining
        assert len(state_a[0]) == 1
        assert state_a[0][0][1] == pytest.approx(10.0, abs=1.0)

        # Token B: 200 - 50 = 150 remaining
        assert len(state_b[0]) == 1
        assert state_b[0][0][1] == pytest.approx(150.0, abs=1.0)
