"""2H: Tests for per-bot depth_multiplier gate in LiquidityGuardian.check_liquidity().

The depth gate is separate from the slippage check. It fails fast when trade_size
is too large relative to top-5 book depth. Disabled when depth_multiplier == 0.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.risk.liquidity_guardian import LiquidityGuardian
# S237 HIGH-1: the phantom size-0 anchor fix lives in the VENDORED copy WeatherBot runs.
# The import above is the repo-root copy (MB-owned, byte-identical pre-fix); the phantom
# tests at the bottom target the silo copy that carries the fix.
from bots.weather.engine.base_engine.risk.liquidity_guardian import (
    LiquidityGuardian as _SiloLiquidityGuardian,
)


def _make_guardian(asks=None, bids=None):
    """Build a LiquidityGuardian with a mocked orderbook_tracker."""
    tracker = MagicMock()
    tracker.snapshot_order_book = AsyncMock(
        return_value={"asks": asks or [], "bids": bids or []}
    )
    return LiquidityGuardian(client=MagicMock(), orderbook_tracker=tracker)


@pytest.mark.asyncio
async def test_depth_check_disabled_by_default():
    """depth_multiplier=0 (default) — check is skipped, slippage check runs."""
    asks = [
        {"price": 0.50, "size": 1000.0},
        {"price": 0.51, "size": 1000.0},
        {"price": 0.52, "size": 1000.0},
    ]
    g = _make_guardian(asks=asks)
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=100.0, side="BUY",
    )
    # Should proceed — depth check disabled, trade fits, slippage trivial
    assert result["can_execute"] is True
    assert "depth_exceeded" not in result.get("reason", "")


@pytest.mark.asyncio
async def test_depth_check_blocks_when_trade_exceeds_capacity():
    """depth_multiplier=10 with trade > depth/10 → blocked with depth_exceeded."""
    asks = [{"price": 0.50, "size": 100.0} for _ in range(5)]  # total 500
    g = _make_guardian(asks=asks)
    # max_safe = 500 / 10 = 50. Trade 100 > 50 → blocked.
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=100.0, side="BUY",
        depth_multiplier=10.0,
    )
    assert result["can_execute"] is False
    assert result["reason"] == "depth_exceeded"
    assert result["liquidity_depth"] == 500.0
    assert result["max_safe"] == 50.0
    assert result["depth_multiplier"] == 10.0
    assert result["recommendation"] == "reduce_size"


@pytest.mark.asyncio
async def test_depth_check_passes_when_trade_within_capacity():
    """depth_multiplier=5 with trade < depth/5 → passes through to slippage check."""
    asks = [{"price": 0.50, "size": 100.0} for _ in range(5)]  # total 500
    g = _make_guardian(asks=asks)
    # max_safe = 500 / 5 = 100. Trade 50 <= 100 → passes depth.
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=50.0, side="BUY",
        depth_multiplier=5.0,
    )
    assert result["can_execute"] is True
    assert result.get("reason") != "depth_exceeded"


@pytest.mark.asyncio
async def test_depth_check_uses_bids_for_sell():
    """SELL side should consult bids (not asks) for the depth calculation."""
    asks = [{"price": 0.50, "size": 1000.0}]
    bids = [{"price": 0.49, "size": 50.0}]  # thin bid side
    g = _make_guardian(asks=asks, bids=bids)
    # max_safe = 50 / 10 = 5. Trade 10 > 5 → blocked (depth only from bids).
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=10.0, side="SELL",
        depth_multiplier=10.0,
    )
    assert result["can_execute"] is False
    assert result["reason"] == "depth_exceeded"
    assert result["liquidity_depth"] == 50.0


@pytest.mark.asyncio
async def test_depth_check_uses_top_5_levels_only():
    """Only the top 5 levels count toward liquidity_depth — levels 6+ ignored."""
    # 10 levels of 100 each = 1000 total, but top-5 = 500
    asks = [{"price": 0.50 + i * 0.01, "size": 100.0} for i in range(10)]
    g = _make_guardian(asks=asks)
    # If it considered all 10 levels: 1000/10 = 100 max_safe → 80 passes.
    # If it considers only top 5: 500/10 = 50 max_safe → 80 blocked.
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=80.0, side="BUY",
        depth_multiplier=10.0,
    )
    assert result["can_execute"] is False
    assert result["reason"] == "depth_exceeded"
    assert result["liquidity_depth"] == 500.0  # confirms top-5 window


# ─────────────────────────────────────────────────────────────────────────────
# S215: split top5_depth=0 (no orderbook data) from real depth_exceeded
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_top5_depth_zero_returns_no_orderbook_data():
    """top-5 depth = 0 (empty/zero-size levels) → no_orderbook_data, NOT depth_exceeded.

    Pre-fix: any non-empty levels list with all-zero sizes returned reason=depth_exceeded
    with max_safe=0, conflating 'book genuinely thin' with 'no usable depth at all'.
    The 8h post-Phase-2 sample showed 12/12 hard-rejects were the latter case (markets
    not in OrderGateway._market_index → empty orderbook fetch). Splitting them lets
    Phase 2 soft-clamp fire on the recoverable case while keeping the unrecoverable
    case clearly diagnosable.
    """
    asks = [{"price": 0.50, "size": 0.0} for _ in range(5)]  # levels exist, sizes 0
    g = _make_guardian(asks=asks)
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=100.0, side="BUY",
        depth_multiplier=5.0,
    )
    assert result["can_execute"] is False
    assert result["reason"] == "no_orderbook_data", (
        f"top5_depth=0 must report no_orderbook_data (not depth_exceeded), "
        f"got reason={result.get('reason')!r}"
    )
    assert result["recommendation"] == "abort"
    assert result["liquidity_depth"] == 0.0
    assert result["max_safe"] == 0.0


@pytest.mark.asyncio
async def test_real_depth_exceeded_still_returns_depth_exceeded():
    """Sanity: top5_depth>0 but trade > max_safe → still reason=depth_exceeded
    with reduce_size recommendation (preserves Phase 2 soft-clamp eligibility)."""
    asks = [{"price": 0.50, "size": 10.0} for _ in range(5)]  # top5 = 50
    g = _make_guardian(asks=asks)
    # max_safe = 50/5 = 10; trade 100 > 10 → real depth_exceeded
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=100.0, side="BUY",
        depth_multiplier=5.0,
    )
    assert result["can_execute"] is False
    assert result["reason"] == "depth_exceeded"
    assert result["recommendation"] == "reduce_size"
    assert result["max_safe"] > 0


@pytest.mark.asyncio
async def test_no_levels_at_all_returns_no_liquidity():
    """Empty levels list (existing pre-fix behavior) → reason=no_liquidity, distinct
    from no_orderbook_data which fires when levels exist but all have size=0."""
    g = _make_guardian(asks=[])
    result = await g.check_liquidity(
        market_id="mkt1", token_id="tok1", trade_size=100.0, side="BUY",
        depth_multiplier=5.0,
    )
    assert result["can_execute"] is False
    assert result["reason"] == "no_liquidity"


# ─────────────────────────────────────────────────────────────────────────────
# S237 HIGH-1: phantom size-0 top-of-book anchoring (vendored / WeatherBot copy).
#
# get_max_safe_size() and check_liquidity() must anchor "best price" to the first level
# with REAL size, not a phantom size-0 top level (the S231/S232 bug class). WeatherBot
# sizes flat ($100 default), so get_max_safe_size shapes the actual fill size on every
# thin book — a phantom anchor mis-sizes or falsely shadow-rejects real fills.
# ─────────────────────────────────────────────────────────────────────────────


def _silo_guardian(book):
    tracker = MagicMock()
    tracker.snapshot_order_book = AsyncMock(return_value=book)
    return _SiloLiquidityGuardian(client=None, orderbook_tracker=tracker)


class TestGetMaxSafeSizePhantom:
    @pytest.mark.asyncio
    async def test_phantom_size0_top_level_is_skipped(self):
        """Size-0 best ask must NOT anchor max_price.

        Bug: best=0.10 (phantom) → max_price=0.102 → real 0.14 excluded → returns 0 →
        WeatherBot falsely shadow-rejects a fillable market.
        Fixed: anchor to 0.14 (first real) → max_price=0.1428 → the 0.14 depth counts.
        """
        book = {"asks": [
            {"price": 0.10, "size": 0},
            {"price": 0.14, "size": 1000.0},
            {"price": 0.15, "size": 500.0},
        ]}
        g = _silo_guardian(book)
        size = await g.get_max_safe_size("m1", "tok", side="BUY", max_slippage_pct=0.02)
        assert size == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_all_zero_size_returns_zero(self):
        book = {"asks": [{"price": 0.10, "size": 0}, {"price": 0.14, "size": 0}]}
        g = _silo_guardian(book)
        assert await g.get_max_safe_size("m1", "tok", side="BUY", max_slippage_pct=0.02) == 0.0

    @pytest.mark.asyncio
    async def test_clean_book_unaffected(self):
        book = {"asks": [{"price": 0.14, "size": 800.0}, {"price": 0.20, "size": 999.0}]}
        g = _silo_guardian(book)
        size = await g.get_max_safe_size("m1", "tok", side="BUY", max_slippage_pct=0.02)
        assert size == pytest.approx(800.0)  # 0.20 > 0.14*1.02 → excluded


class TestCheckLiquidityPhantom:
    @pytest.mark.asyncio
    async def test_phantom_top_does_not_inflate_slippage(self):
        """Slippage measured against the first REAL level, not a phantom.

        Bug: best=0.10 (phantom) vs VWAP 0.14 → 40% slippage → blocked.
        Fixed: best=0.14 (real) vs VWAP 0.14 → ~0% → can_execute=True.
        """
        book = {"asks": [{"price": 0.10, "size": 0}, {"price": 0.14, "size": 1000.0}]}
        g = _silo_guardian(book)
        res = await g.check_liquidity("m1", "tok", trade_size=100.0, side="BUY")
        assert res["best_price"] == pytest.approx(0.14)
        assert res["slippage"] == pytest.approx(0.0, abs=1e-9)
        assert res["can_execute"] is True

    @pytest.mark.asyncio
    async def test_real_slippage_still_detected(self):
        book = {"asks": [
            {"price": 0.10, "size": 0},
            {"price": 0.20, "size": 50.0},
            {"price": 0.30, "size": 1000.0},
        ]}
        g = _silo_guardian(book)
        res = await g.check_liquidity("m1", "tok", trade_size=100.0, side="BUY")
        # VWAP = (50*0.20 + 50*0.30)/100 = 0.25; anchored to real best 0.20 → 25%.
        assert res["best_price"] == pytest.approx(0.20)
        assert res["slippage"] == pytest.approx(0.25, rel=1e-3)
        assert res["can_execute"] is False
