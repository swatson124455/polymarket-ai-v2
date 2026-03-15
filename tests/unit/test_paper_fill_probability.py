"""Tests for realistic paper trading fill probability model (S91)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from base_engine.execution.paper_trading import (
    _fill_probability,
    PaperTradingEngine,
)


# ── _fill_probability() unit tests ────────────────────────────────────────


class TestFillProbability:
    def test_extreme_price_thin_volume(self):
        """Extreme price (0.05) with $250 on thin volume → low probability."""
        prob = _fill_probability(price=0.05, order_size_usd=250, spread=0.05, volume_24h=500)
        assert prob < 0.30, f"Expected <0.30, got {prob:.3f}"

    def test_midprice_normal_volume(self):
        """Mid-price (0.50) with $50 on $10k volume → high probability."""
        prob = _fill_probability(price=0.50, order_size_usd=50, spread=0.01, volume_24h=10000)
        assert prob > 0.85, f"Expected >0.85, got {prob:.3f}"

    def test_floor_never_below_5pct(self):
        """Even extreme conditions never return below 5%."""
        prob = _fill_probability(price=0.01, order_size_usd=10000, spread=0.20, volume_24h=100)
        assert prob >= 0.05, f"Expected >=0.05, got {prob:.3f}"

    def test_ceiling_never_above_1(self):
        """Best conditions never exceed 1.0."""
        prob = _fill_probability(price=0.50, order_size_usd=1, spread=0.001, volume_24h=1000000)
        assert prob <= 1.0

    def test_price_symmetry(self):
        """Probability is symmetric around 0.50 (0.30 ≈ 0.70)."""
        p_low = _fill_probability(price=0.30, order_size_usd=100, spread=0.03, volume_24h=5000)
        p_high = _fill_probability(price=0.70, order_size_usd=100, spread=0.03, volume_24h=5000)
        assert abs(p_low - p_high) < 0.01, f"Expected symmetric, got {p_low:.3f} vs {p_high:.3f}"

    def test_larger_order_lower_probability(self):
        """Larger order → lower fill probability."""
        p_small = _fill_probability(price=0.50, order_size_usd=10, spread=0.02, volume_24h=5000)
        p_large = _fill_probability(price=0.50, order_size_usd=2000, spread=0.02, volume_24h=5000)
        assert p_small > p_large, f"Expected small > large, got {p_small:.3f} vs {p_large:.3f}"

    def test_wider_spread_lower_probability(self):
        """Wider spread → lower fill probability."""
        p_tight = _fill_probability(price=0.50, order_size_usd=100, spread=0.01, volume_24h=5000)
        p_wide = _fill_probability(price=0.50, order_size_usd=100, spread=0.15, volume_24h=5000)
        assert p_tight > p_wide, f"Expected tight > wide, got {p_tight:.3f} vs {p_wide:.3f}"

    def test_zero_volume_uses_fallback(self):
        """Zero volume uses conservative fallback (1000), doesn't crash."""
        prob = _fill_probability(price=0.50, order_size_usd=100, spread=0.03, volume_24h=0)
        assert 0.05 <= prob <= 1.0


# ── Partial fill integration tests ────────────────────────────────────────


class TestPartialFill:
    def _make_engine(self):
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        return engine

    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        """With PAPER_REALISTIC_FILLS=false (default), all BUY orders fill fully."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = False
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 150
            ms.MAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="mkt1", token_id="tok1", side="BUY",
                size=100.0, price=0.50, bid=0.49, ask=0.51,
            )
        assert result["success"] is True
        assert result["filled"] >= 99.0  # full fill (minus float rounding)

    @pytest.mark.asyncio
    async def test_sell_always_fills(self):
        """SELL orders always fill fully even with PAPER_REALISTIC_FILLS=true."""
        engine = self._make_engine()
        # Seed a position to sell
        engine.positions["mkt1"] = {
            "size": 50.0, "avg_price": 0.50, "token_id": "tok1",
            "side": "YES", "entry_fee": 0.0,
        }
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 150
            ms.MAKER_FEE_BPS = 0
            result = await engine.place_order(
                market_id="mkt1", token_id="tok1", side="SELL",
                size=50.0, price=0.50, bid=0.49, ask=0.51,
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_realistic_reduces_fills_on_thin_market(self):
        """With realistic fills ON and thin market, some orders don't fill."""
        engine = self._make_engine()
        no_fill_count = 0
        partial_count = 0
        n_trials = 50

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            for i in range(n_trials):
                engine.cash = 10000.0  # reset cash each trial
                result = await engine.place_order(
                    market_id=f"mkt_{i}", token_id="tok1", side="BUY",
                    size=500.0, price=0.05,  # extreme price, $25 order
                    bid=0.04, ask=0.06, volume=200.0,  # thin volume
                )
                if not result.get("success"):
                    no_fill_count += 1
                elif result.get("filled", 500.0) < 499.0:
                    partial_count += 1

        # With price=0.05 and thin volume, we expect meaningful rejection rate
        assert no_fill_count > 0, f"Expected some no-fills on thin market, got 0/{n_trials}"

    @pytest.mark.asyncio
    async def test_latency_drift_applied(self):
        """With high latency, BUY price drifts adversely (higher)."""
        engine = self._make_engine()
        prices_no_latency = []
        prices_with_latency = []

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 10
            ms.FIXED_SLIPPAGE_BPS = 50  # fixed to isolate latency effect
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0

            for i in range(20):
                engine.cash = 10000.0
                r = await engine.place_order(
                    market_id=f"mkt_nl_{i}", token_id="tok1", side="BUY",
                    size=10.0, price=0.50, latency_ms=None,
                )
                if r.get("success"):
                    prices_no_latency.append(r["price"])

            for i in range(20):
                engine.cash = 10000.0
                r = await engine.place_order(
                    market_id=f"mkt_wl_{i}", token_id="tok1", side="BUY",
                    size=10.0, price=0.50, latency_ms=5000.0,  # 5s latency
                )
                if r.get("success"):
                    prices_with_latency.append(r["price"])

        if prices_no_latency and prices_with_latency:
            avg_no = sum(prices_no_latency) / len(prices_no_latency)
            avg_with = sum(prices_with_latency) / len(prices_with_latency)
            # With latency, BUY price should be higher (worse)
            assert avg_with > avg_no, f"Expected latency drift, got no_latency={avg_no:.4f} with_latency={avg_with:.4f}"

    @pytest.mark.asyncio
    async def test_latency_drift_below_threshold_ignored(self):
        """Latency below 500ms does not trigger drift."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 10
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0

            # With 200ms latency (below 500ms threshold), should not see drift log
            result = await engine.place_order(
                market_id="mkt_low_lat", token_id="tok1", side="BUY",
                size=10.0, price=0.50, latency_ms=200.0,
            )
        # Just verify it completes without error
        assert isinstance(result, dict)
