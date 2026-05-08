"""2H: Tests for per-bot depth_multiplier gate in LiquidityGuardian.check_liquidity().

The depth gate is separate from the slippage check. It fails fast when trade_size
is too large relative to top-5 book depth. Disabled when depth_multiplier == 0.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.risk.liquidity_guardian import LiquidityGuardian


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
