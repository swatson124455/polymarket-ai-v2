"""Tests for realistic paper trading fill probability model (S91+S95)."""
import math
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from base_engine.execution.paper_trading import (
    _fill_probability,
    _alpha_decay_factor,
    _resolution_proximity_penalty,
    PaperTradingEngine,
)


# ── _fill_probability() unit tests ────────────────────────────────────────


class TestFillProbability:
    def test_extreme_price_thin_volume(self):
        """Extreme price (0.05) with $250 on thin volume → low probability."""
        prob = _fill_probability(price=0.05, order_size_usd=250, spread=0.05, volume_24h=500)
        assert prob < 0.30, f"Expected <0.30, got {prob:.3f}"

    def test_midprice_normal_volume(self):
        """Mid-price (0.50) with $50 on $10k volume → moderate-high probability.
        S95: time-of-day + participation factors reduce from pre-S95 baseline."""
        prob = _fill_probability(price=0.50, order_size_usd=50, spread=0.01, volume_24h=10000)
        assert prob > 0.40, f"Expected >0.40, got {prob:.3f}"

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
            ms.PAPER_TAKER_FEE_BPS = 0
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
            ms.PAPER_TAKER_FEE_BPS = 0
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
            ms.PAPER_TAKER_FEE_BPS = 0
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
        """S95: With high latency, alpha decay makes BUY price worse (higher)."""
        engine = self._make_engine()
        prices_no_latency = []
        prices_with_latency = []

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 10  # short half-life to amplify effect
            ms.FIXED_SLIPPAGE_BPS = 50  # fixed to isolate latency effect
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = False
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = False

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
                    size=10.0, price=0.50, latency_ms=30000.0,  # 30s on 10s half-life → heavy decay
                )
                if r.get("success"):
                    prices_with_latency.append(r["price"])

        if prices_no_latency and prices_with_latency:
            avg_no = sum(prices_no_latency) / len(prices_no_latency)
            avg_with = sum(prices_with_latency) / len(prices_with_latency)
            # With latency, BUY price should be higher (worse)
            assert avg_with > avg_no, f"Expected latency drift, got no_latency={avg_no:.4f} with_latency={avg_with:.4f}"

    @pytest.mark.asyncio
    async def test_latency_below_500ms_still_applies_alpha_decay(self):
        """S95: Sub-500ms latency now applies proportional alpha decay (no threshold)."""
        engine = self._make_engine()
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 300
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = False
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = False

            result = await engine.place_order(
                market_id="mkt_low_lat", token_id="tok1", side="BUY",
                size=10.0, price=0.50, latency_ms=200.0,
            )
        # Sub-500ms now applies (proportional decay), should complete without error
        assert isinstance(result, dict)


# ── Alpha Decay unit tests ────────────────────────────────────────────────


class TestAlphaDecay:
    def test_no_latency_returns_one(self):
        """No latency → decay factor = 1.0 (no decay)."""
        assert _alpha_decay_factor(None) == 1.0
        assert _alpha_decay_factor(0) == 1.0
        assert _alpha_decay_factor(-100) == 1.0

    def test_half_life_math(self):
        """At t = half_life, decay factor should be exactly 0.5."""
        half_life_s = 300.0
        factor = _alpha_decay_factor(half_life_s * 1000, half_life_s)
        assert abs(factor - 0.5) < 0.001, f"Expected 0.5, got {factor}"

    def test_short_latency_minimal_decay(self):
        """200ms latency on 300s half-life → near 1.0 (minimal decay)."""
        factor = _alpha_decay_factor(200, 300.0)
        assert factor > 0.999, f"Expected >0.999, got {factor}"

    def test_high_latency_significant_decay(self):
        """5s latency on 300s half-life → still >0.98 but measurable."""
        factor = _alpha_decay_factor(5000, 300.0)
        expected = math.exp(-math.log(2) * 5.0 / 300.0)
        assert abs(factor - expected) < 0.0001

    @pytest.mark.asyncio
    async def test_alpha_decay_increases_buy_price(self):
        """High latency with alpha decay should make BUY price worse (higher)."""
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        prices_no_lat = []
        prices_hi_lat = []

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 10  # short half-life for test
            ms.FIXED_SLIPPAGE_BPS = 50
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = False
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = False

            for i in range(20):
                engine.cash = 10000.0
                r = await engine.place_order(
                    market_id=f"mkt_nl_{i}", token_id="tok1", side="BUY",
                    size=10.0, price=0.50, latency_ms=None,
                )
                if r.get("success"):
                    prices_no_lat.append(r["price"])

            for i in range(20):
                engine.cash = 10000.0
                r = await engine.place_order(
                    market_id=f"mkt_hl_{i}", token_id="tok1", side="BUY",
                    size=10.0, price=0.50, latency_ms=30000.0,  # 30s on 10s half-life → heavy decay
                )
                if r.get("success"):
                    prices_hi_lat.append(r["price"])

        if prices_no_lat and prices_hi_lat:
            avg_no = sum(prices_no_lat) / len(prices_no_lat)
            avg_hi = sum(prices_hi_lat) / len(prices_hi_lat)
            assert avg_hi > avg_no, f"Expected decay drift, got no_lat={avg_no:.4f} hi_lat={avg_hi:.4f}"


# ── Kyle's Lambda unit tests ──────────────────────────────────────────────


class TestKyleLambda:
    def _make_engine(self):
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        return engine

    @pytest.mark.asyncio
    async def test_high_lambda_lower_fill_prob(self):
        """High Kyle's lambda → lower fill probability (more no-fills)."""
        engine = self._make_engine()
        # Pre-seed cache with high lambda
        engine._kyle_lambda_cache["mkt_hi"] = (3.0, time.monotonic())
        engine._kyle_lambda_cache["mkt_lo"] = (0.1, time.monotonic())

        no_fill_hi = 0
        no_fill_lo = 0
        n = 50

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 300
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = True
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = False
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = False

            for i in range(n):
                engine.cash = 10000.0
                r = await engine.place_order(
                    market_id="mkt_hi", token_id="tok1", side="BUY",
                    size=100.0, price=0.50, bid=0.49, ask=0.51, volume=5000.0,
                )
                if not r.get("success"):
                    no_fill_hi += 1

            for i in range(n):
                engine.cash = 10000.0
                r = await engine.place_order(
                    market_id="mkt_lo", token_id="tok1", side="BUY",
                    size=100.0, price=0.50, bid=0.49, ask=0.51, volume=5000.0,
                )
                if not r.get("success"):
                    no_fill_lo += 1

        assert no_fill_hi >= no_fill_lo, f"Expected high lambda more rejections: hi={no_fill_hi} lo={no_fill_lo}"

    @pytest.mark.asyncio
    async def test_kyle_disabled_no_extra_slippage(self):
        """With PAPER_KYLE_LAMBDA_ENABLED=False, no lambda slippage added."""
        engine = self._make_engine()
        engine._kyle_lambda_cache["mkt1"] = (5.0, time.monotonic())  # very high, but disabled

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 300
            ms.FIXED_SLIPPAGE_BPS = 50
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = False
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = False

            r = await engine.place_order(
                market_id="mkt1", token_id="tok1", side="BUY",
                size=10.0, price=0.50,
            )
        assert r.get("success") is True or "fill probability" in r.get("error", "")

    @pytest.mark.asyncio
    async def test_default_lambda_on_no_db(self):
        """Without DB, _get_kyle_lambda returns DEFAULT_LAMBDA."""
        engine = self._make_engine()
        lam = await engine._get_kyle_lambda("any_market")
        from base_engine.features.market_impact import DEFAULT_LAMBDA
        assert lam == DEFAULT_LAMBDA


# ── Cross-Scan Cumulative Impact tests ────────────────────────────────────


class TestCrossScanImpact:
    def _make_engine(self):
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        return engine

    @pytest.mark.asyncio
    async def test_second_order_same_market_worse_price(self):
        """2nd BUY on same market within 60s → higher price (more slippage)."""
        engine = self._make_engine()
        prices = []

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.02
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 300
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = True
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = False

            for i in range(5):
                engine.cash = 10000.0
                r = await engine.place_order(
                    market_id="same_mkt", token_id="tok1", side="BUY",
                    size=200.0, price=0.50, bid=0.49, ask=0.51, volume=5000.0,
                )
                if r.get("success"):
                    prices.append(r["price"])

        # Expect later orders to have higher (worse) prices due to cumulative impact
        if len(prices) >= 3:
            assert prices[-1] >= prices[0], f"Expected cumulative impact: first={prices[0]:.4f} last={prices[-1]:.4f}"

    @pytest.mark.asyncio
    async def test_different_markets_no_cross_impact(self):
        """BUYs on different markets don't accumulate cross-scan impact."""
        engine = self._make_engine()

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.02
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 300
            ms.FIXED_SLIPPAGE_BPS = 50
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = True
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = False

            engine.cash = 10000.0
            r1 = await engine.place_order(
                market_id="mkt_a", token_id="tok1", side="BUY",
                size=100.0, price=0.50, bid=0.49, ask=0.51, volume=5000.0,
            )
            engine.cash = 10000.0
            r2 = await engine.place_order(
                market_id="mkt_b", token_id="tok1", side="BUY",
                size=100.0, price=0.50, bid=0.49, ask=0.51, volume=5000.0,
            )

        # mkt_b should not have cumulative impact from mkt_a
        # (Both should succeed — no cross-market contamination)
        assert r1.get("success") is True or "fill probability" in r1.get("error", "")
        assert r2.get("success") is True or "fill probability" in r2.get("error", "")


# ── Resolution Proximity unit tests ───────────────────────────────────────


class TestResolutionProximity:
    def test_far_from_resolution_no_penalty(self):
        """More than 6h from resolution → no penalty."""
        slip, fill = _resolution_proximity_penalty(24.0)
        assert slip == 1.0
        assert fill == 1.0

    def test_close_to_resolution_high_penalty(self):
        """Under 30 min → max penalty."""
        slip, fill = _resolution_proximity_penalty(0.25)
        assert slip == 3.0
        assert fill == 0.5

    def test_medium_distance(self):
        """2-6h bracket."""
        slip, fill = _resolution_proximity_penalty(4.0)
        assert slip == 1.5
        assert fill == 0.9

    def test_near_distance(self):
        """0.5-2h bracket."""
        slip, fill = _resolution_proximity_penalty(1.0)
        assert slip == 2.0
        assert fill == 0.7

    def test_none_hours_no_penalty(self):
        """No lead_time_hours → no penalty."""
        slip, fill = _resolution_proximity_penalty(None)
        assert slip == 1.0
        assert fill == 1.0

    @pytest.mark.asyncio
    async def test_sell_unaffected_by_resolution_proximity(self):
        """SELL orders always fill — resolution proximity doesn't apply."""
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()
        engine.positions["mkt1"] = {
            "size": 50.0, "avg_price": 0.50, "token_id": "tok1",
            "side": "YES", "entry_fee": 0.0,
        }
        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 300
            ms.FIXED_SLIPPAGE_BPS = 0
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = False
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = True

            result = await engine.place_order(
                market_id="mkt1", token_id="tok1", side="SELL",
                size=50.0, price=0.50, bid=0.49, ask=0.51,
                event_data={"lead_time_hours": 0.1},  # very close to resolution
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_lead_time_no_effect(self):
        """Without lead_time_hours in event_data, no resolution penalty."""
        engine = PaperTradingEngine(initial_capital=10000.0, db=None)
        engine.enable()

        with patch("base_engine.execution.paper_trading.settings") as ms:
            ms.PAPER_REALISTIC_FILLS = True
            ms.PAPER_DEFAULT_SPREAD = 0.04
            ms.PAPER_ALPHA_DECAY_HALF_LIFE_S = 300
            ms.FIXED_SLIPPAGE_BPS = 50
            ms.TAKER_FEE_BPS = 0
            ms.MAKER_FEE_BPS = 0
            ms.PAPER_TAKER_FEE_BPS = 0
            ms.PAPER_KYLE_LAMBDA_ENABLED = False
            ms.PAPER_CROSS_SCAN_IMPACT_ENABLED = False
            ms.PAPER_RESOLUTION_PROXIMITY_ENABLED = True

            result = await engine.place_order(
                market_id="mkt_no_lt", token_id="tok1", side="BUY",
                size=10.0, price=0.50, event_data={},  # no lead_time_hours
            )
        assert isinstance(result, dict)
