"""
S172 7B Phase A — Contract tests for MirrorBot._log_rejection helper and
Database.insert_mirror_rejected_signal DB method.

Purpose (per reviewer's guardrail):
  (a) _log_rejection writes to the new table with full trader_address (never truncated)
  (b) >=4 distinct rejection scenarios produce their correct reason codes and stage buckets
  (c) the helper does NOT block/raise on DB failure — instrumentation must not
      break the trade path

Stage buckets in scope for mirror_bot.py (per design doc §A2, corrected S187 §2.1):
  - pre_gate: 11 sites (mirror_whale_too_small, mirror_price_floor_blocked, ...)
  - gate:     5 sites (mirror_gate_blocked, mirror_low_confidence, ...)
  - post_gate:6 sites (mirror_no_edge_rejected, mirror_dust_skipped, ...)
  watchlist/pre_watchlist live in elite_watchlist.py and are OUT of scope per S187.

These tests mock the DB entirely; no migration required to run.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bots.mirror_bot import MirrorBot
from config.settings import settings


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_engine_with_db():
    """Minimal mock BaseEngine with an AsyncMock db.insert_mirror_rejected_signal."""
    engine = MagicMock()
    engine.db = MagicMock()
    engine.db.session_factory = MagicMock()  # truthy so insert helper proceeds
    # The method under contract — we assert on its call args below.
    engine.db.insert_mirror_rejected_signal = AsyncMock(return_value=None)
    # Boilerplate required to construct MirrorBot without touching the network
    engine.order_gateway = MagicMock()
    engine.order_gateway.has_open_position = MagicMock(return_value=False)
    engine.order_gateway._daily_exposure_usd = {}
    engine.risk_manager.check_hard_stop_loss = MagicMock(return_value={
        "should_exit": False, "reason": "", "details": {},
    })
    engine.get_markets = AsyncMock(return_value=[])
    engine.filter_markets_for_trading = MagicMock(return_value=[])
    engine.get_market_from_index = MagicMock(return_value={
        "active": True, "volume_24h": 100000.0, "liquidity": 50000.0,
    })
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    engine.db.get_session = MagicMock(return_value=mock_ctx)
    return engine


def _make_bot():
    engine = _make_engine_with_db()
    with patch("bots.mirror_bot.settings") as ms:
        ms.MIRROR_MIN_CONFIDENCE = 0.50
        ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
        ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
        ms.MIRROR_STOP_LOSS_PCT = 0.15
        ms.MIRROR_MAX_TRACKED_TRADES = 10_000
        ms.TOP_TRADER_COUNT = 10
        ms.TOTAL_CAPITAL = 10_000.0
        ms.ORDER_LATENCY_ALERT_MS = 5000
        ms.BOT_SCAN_TIMEOUT_SECONDS = 60
        ms.MIRROR_MAX_CONCURRENT_FETCHES = 20
        ms.MIRROR_FLAT_POSITION_SIZE_USD = 30.0
        bot = MirrorBot(engine)
    bot.bankroll = None
    bot._adaptive_safety = None
    return bot, engine


# ── Contract (a): full trader_address never truncated ────────────────────────

@pytest.mark.asyncio
async def test_log_rejection_writes_full_trader_address():
    """The helper must persist the FULL 42-char hex address, not a truncation
    like trader[:10] or the mirror_rtds_{trader[:8]} used in signal_source.
    Phase B's counterfactual ranking collides on the 10-hex prefix otherwise.
    """
    bot, engine = _make_bot()
    full_addr = "0x1234567890abcdef1234567890abcdef12345678"  # 42 chars

    await bot._log_rejection(
        trader_address=full_addr,
        market_id="0xdeadbeefmarket",
        rejection_reason="mirror_whale_too_small",
        rejection_stage="pre_gate",
        token_id="tok-123",
        side="YES",
        price=0.45,
        whale_trade_usd=15.0,
        signal_metadata={"min_usd": 100.0},
    )

    engine.db.insert_mirror_rejected_signal.assert_awaited_once()
    kwargs = engine.db.insert_mirror_rejected_signal.await_args.kwargs
    assert kwargs["trader_address"] == full_addr, \
        "full trader_address must be preserved, not truncated"
    assert len(kwargs["trader_address"]) == 42


# ── Contract (b): 4 distinct scenarios across 3 stage buckets ────────────────

@pytest.mark.asyncio
async def test_log_rejection_pre_gate_whale_too_small():
    """Site #17 — mirror_whale_too_small in pre_gate stage."""
    bot, engine = _make_bot()
    await bot._log_rejection(
        trader_address="0xAAA000000000000000000000000000000000AAAA",
        market_id="0xmarket1",
        rejection_reason="mirror_whale_too_small",
        rejection_stage="pre_gate",
        whale_trade_usd=42.0,
        signal_metadata={"min_usd": 100.0},
    )
    kwargs = engine.db.insert_mirror_rejected_signal.await_args.kwargs
    assert kwargs["rejection_reason"] == "mirror_whale_too_small"
    assert kwargs["rejection_stage"] == "pre_gate"
    assert kwargs["whale_trade_usd"] == 42.0
    assert kwargs["metadata"] == {"min_usd": 100.0}


@pytest.mark.asyncio
async def test_log_rejection_gate_blocked():
    """Site #31 — mirror_gate_blocked in gate stage (split scoring)."""
    bot, engine = _make_bot()
    await bot._log_rejection(
        trader_address="0xBBB000000000000000000000000000000000BBBB",
        market_id="0xmarket2",
        rejection_reason="mirror_gate_blocked",
        rejection_stage="gate",
        price=0.48,
        side="YES",
        signal_metadata={"gate_score": 0.41, "threshold": 0.52, "kelly_prob": 0.55},
    )
    kwargs = engine.db.insert_mirror_rejected_signal.await_args.kwargs
    assert kwargs["rejection_reason"] == "mirror_gate_blocked"
    assert kwargs["rejection_stage"] == "gate"
    assert kwargs["metadata"]["gate_score"] == 0.41


@pytest.mark.asyncio
async def test_log_rejection_post_gate_no_edge():
    """Site #34 — mirror_no_edge_rejected in post_gate stage."""
    bot, engine = _make_bot()
    await bot._log_rejection(
        trader_address="0xCCC000000000000000000000000000000000CCCC",
        market_id="0xmarket3",
        rejection_reason="mirror_no_edge_rejected",
        rejection_stage="post_gate",
        side="NO",
        price=0.78,
        signal_metadata={"edge": 0.02, "min_edge": 0.05, "confidence": 0.72},
    )
    kwargs = engine.db.insert_mirror_rejected_signal.await_args.kwargs
    assert kwargs["rejection_reason"] == "mirror_no_edge_rejected"
    assert kwargs["rejection_stage"] == "post_gate"
    assert kwargs["side"] == "NO"


@pytest.mark.asyncio
async def test_log_rejection_pre_gate_price_floor():
    """Site #15 — mirror_price_floor_blocked (4th distinct scenario)."""
    bot, engine = _make_bot()
    await bot._log_rejection(
        trader_address="0xDDD000000000000000000000000000000000DDDD",
        market_id="0xmarket4",
        rejection_reason="mirror_price_floor_blocked",
        rejection_stage="pre_gate",
        price=0.98,
        signal_metadata={"price_floor": 0.03, "price_ceiling": 0.97},
    )
    kwargs = engine.db.insert_mirror_rejected_signal.await_args.kwargs
    assert kwargs["rejection_reason"] == "mirror_price_floor_blocked"
    assert kwargs["price"] == 0.98


# ── Contract (c): DB failure must not block the trade path ───────────────────

@pytest.mark.asyncio
async def test_log_rejection_db_failure_non_blocking():
    """If the DB insert raises, _log_rejection must swallow and NOT re-raise.
    Rejection instrumentation failure should never prevent the trade path
    from continuing to its `return False` — the rejection still happens,
    we just lose the log row for that event.
    """
    bot, engine = _make_bot()
    engine.db.insert_mirror_rejected_signal = AsyncMock(
        side_effect=RuntimeError("simulated db down")
    )

    # Must NOT raise.
    await bot._log_rejection(
        trader_address="0xEEE000000000000000000000000000000000EEEE",
        market_id="0xmarket5",
        rejection_reason="mirror_whale_too_small",
        rejection_stage="pre_gate",
    )

    engine.db.insert_mirror_rejected_signal.assert_awaited_once()


@pytest.mark.asyncio
async def test_log_rejection_missing_db_is_noop():
    """If engine.db is None (API-only / test context without DB), helper is a no-op.
    This covers the early-exit branch — the helper must tolerate no-DB cleanly."""
    bot, engine = _make_bot()
    engine.db = None  # simulate API-only mode

    # Must NOT raise and must NOT attempt to call any DB method.
    await bot._log_rejection(
        trader_address="0xFFF000000000000000000000000000000000FFFF",
        market_id="0xmarket6",
        rejection_reason="mirror_gate_blocked",
        rejection_stage="gate",
    )


@pytest.mark.asyncio
async def test_log_rejection_signal_metadata_optional():
    """signal_metadata is optional; helper must work when omitted (many call sites
    don't have site-specific context to attach). Test the minimal call shape."""
    bot, engine = _make_bot()
    await bot._log_rejection(
        trader_address="0x1110000000000000000000000000000000001111",
        market_id="0xmarket7",
        rejection_reason="mirror_category_cap_reject",
        rejection_stage="post_gate",
    )
    kwargs = engine.db.insert_mirror_rejected_signal.await_args.kwargs
    assert kwargs["metadata"] is None or kwargs["metadata"] == {}
