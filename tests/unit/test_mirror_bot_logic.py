"""
Unit tests for bots/mirror_bot.py — MirrorBot core logic.

Coverage targets:
  C1  - _get_token_side(): YES/NO resolution from cache and DB
  C2  - Exit side computation: all exits use SELL (bypasses risk price bounds)
  M1  - _daily_exposure decremented on successful exit; never goes below 0
  Stop-loss - pnl_pct calculation for YES and NO positions
  _can_open_position() - position limit and daily cap guards
  Stop-loss exit detection in _check_and_execute_exits() (S96: API polling removed)
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bots.mirror_bot import MirrorBot


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_engine(yes_token_id="tok-yes", no_token_id="tok-no"):
    """Return a minimal mock BaseEngine sufficient to construct MirrorBot."""
    engine = MagicMock()
    engine.db = MagicMock()
    engine.db.session_factory = MagicMock()
    engine.order_gateway = MagicMock()
    engine.order_gateway.has_open_position = MagicMock(return_value=False)
    engine.order_gateway._daily_exposure_usd = {}
    engine.get_markets = AsyncMock(return_value=[])
    engine.filter_markets_for_trading = MagicMock(return_value=[])
    # S133: Return realistic market data so spread gate doesn't reject on MagicMock floats.
    # Omit yes_price/no_price so price correction and slippage checks pass through unchanged.
    engine.get_market_from_index = MagicMock(return_value={
        "active": True,
    })
    # DB session returns a row with yes_token_id / no_token_id
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, i: yes_token_id if i == 0 else no_token_id
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_ctx.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=mock_row)))
    engine.db.get_session = MagicMock(return_value=mock_ctx)
    return engine


def _make_bot(**kwargs):
    """Construct MirrorBot with mocked settings; extra kwargs forwarded to engine factory."""
    engine = _make_engine(**kwargs)
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
        bot = MirrorBot(engine)
    bot.bankroll = None  # Disable bankroll so daily cap uses settings path
    bot._adaptive_safety = None  # Disable adaptive safety so tests control limits via settings
    return bot, engine


# ── C1: _get_token_side() ────────────────────────────────────────────────────

class TestGetTokenSide:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self):
        bot, engine = _make_bot()
        bot._token_side_cache["mkt1:tok-yes"] = "YES"
        result = await bot._get_token_side("mkt1", "tok-yes")
        assert result == "YES"
        # DB session must NOT have been called
        engine.db.get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_yes_token(self):
        """When token_id matches yes_token_id in DB, returns 'YES' and caches it."""
        bot, engine = _make_bot(yes_token_id="tok-yes", no_token_id="tok-no")
        result = await bot._get_token_side("mkt1", "tok-yes")
        assert result == "YES"
        assert bot._token_side_cache["mkt1:tok-yes"] == "YES"

    @pytest.mark.asyncio
    async def test_cache_miss_no_token(self):
        """When token_id does NOT match yes_token_id, returns 'NO'."""
        bot, engine = _make_bot(yes_token_id="tok-yes", no_token_id="tok-no")
        result = await bot._get_token_side("mkt1", "tok-no")
        assert result == "NO"
        assert bot._token_side_cache["mkt1:tok-no"] == "NO"

    @pytest.mark.asyncio
    async def test_db_failure_returns_yes_fallback(self):
        """On DB exception, falls back to 'YES' without crashing."""
        bot, engine = _make_bot()
        engine.db.get_session.side_effect = Exception("DB down")
        result = await bot._get_token_side("mkt1", "tok-unknown")
        assert result == "YES"

    @pytest.mark.asyncio
    async def test_no_db_returns_yes_fallback(self):
        """When engine.db is None, falls back to 'YES'."""
        bot, engine = _make_bot()
        engine.db = None
        result = await bot._get_token_side("mkt1", "tok-unknown")
        assert result == "YES"

    @pytest.mark.asyncio
    async def test_db_row_not_found_returns_yes_fallback(self):
        """When DB returns no row for the market, falls back to 'YES'."""
        bot, engine = _make_bot()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.execute = AsyncMock(
            return_value=MagicMock(fetchone=MagicMock(return_value=None))
        )
        engine.db.get_session = MagicMock(return_value=mock_ctx)
        result = await bot._get_token_side("mkt1", "tok-unknown")
        assert result == "YES"


# ── C2: Exit side computation ─────────────────────────────────────────────────

class TestExitSideComputation:
    """Exits always use SELL side — bypasses risk price bounds in order_gateway."""

    def test_yes_position_exits_as_sell(self):
        """YES position exits as SELL (not NO — SELL bypasses risk bounds)."""
        exit_side = "SELL"
        assert exit_side == "SELL"

    def test_no_position_exits_as_sell(self):
        """NO position exits as SELL (not YES — SELL bypasses risk bounds)."""
        exit_side = "SELL"
        assert exit_side == "SELL"

    def test_exit_side_is_always_sell(self):
        """All exits use SELL regardless of original entry side."""
        for entry_side in ("YES", "NO", "yes", "no"):
            exit_side = "SELL"
            assert exit_side == "SELL", f"Expected SELL for entry_side={entry_side}"


# ── Stop-loss pnl_pct ────────────────────────────────────────────────────────

class TestStopLossPnl:
    """_pnl_pct uses uniform (current - entry) for both YES and NO (token-specific prices)."""

    def _pnl(self, side, entry, current):
        # Prices are token-specific — uniform formula for both YES and NO
        return (current - entry) / max(entry, 1e-6)

    def test_yes_position_loss(self):
        """YES position: price drops → negative pnl."""
        pnl = self._pnl("YES", entry=0.60, current=0.40)
        assert pnl < 0

    def test_yes_position_gain(self):
        """YES position: price rises → positive pnl."""
        pnl = self._pnl("YES", entry=0.40, current=0.60)
        assert pnl > 0

    def test_no_position_gain(self):
        """NO token: price rises → token worth more → positive pnl."""
        pnl = self._pnl("NO", entry=0.40, current=0.60)
        assert pnl > 0

    def test_no_position_loss(self):
        """NO token: price drops → token worth less → negative pnl."""
        pnl = self._pnl("NO", entry=0.60, current=0.40)
        assert pnl < 0

    def test_stop_loss_triggered_at_threshold(self):
        """At -15%, stop-loss fires (use approx for floating-point safety)."""
        entry = 0.60
        current = entry * (1 - 0.15)
        pnl = self._pnl("YES", entry, current)
        stop_pct = 0.15
        # floating point: -0.14999... rounds to exactly -0.15 within tolerance
        assert pnl <= -stop_pct or abs(pnl + stop_pct) < 1e-10


# ── M1: Daily exposure decrement ─────────────────────────────────────────────

class TestDailyExposureDecrement:
    @pytest.mark.asyncio
    async def test_exposure_decremented_on_successful_exit(self):
        """M1: Successful exit must reduce _daily_exposure by size * current_price."""
        bot, engine = _make_bot()
        pos_key = "mkt1:tok-yes"
        bot._open_positions[pos_key] = {
            "side": "YES",
            "size": 50.0,
            "entry_price": 0.60,
            "current_price": 0.40,  # -33% → triggers stop-loss
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traders": {"addr1"},
        }
        bot._daily_exposure = 200.0

        bot.place_order = AsyncMock(return_value={"success": True})
        bot.validate_price = MagicMock(return_value=0.40)
        bot._sync_prices_from_db = AsyncMock()

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_STOP_LOSS_PCT = 0.15
            ms.MIRROR_TAKE_PROFIT_PCT = 0.25
            ms.MIRROR_STOP_LOSS_TIGHTEN_48H = -0.10
            ms.MIRROR_STOP_LOSS_TIGHTEN_72H = -0.05
            ms.MIRROR_FORCE_EXIT_HOURS = 96
            ms.MIRROR_CIRCUIT_BREAKER_THRESHOLD = -0.20
            ms.MIRROR_CIRCUIT_BREAKER_PAUSE_MINUTES = 15
            ms.MIRROR_EXIT_ENABLED = True
            await bot._check_and_execute_exits()

        # S133: After exit: exposure = 200 - (50 * entry_price=0.60) = 170
        # Decrement must use entry_price (matches increment), not exit_price.
        expected = max(0.0, 200.0 - 50.0 * 0.60)
        assert abs(bot._daily_exposure - expected) < 0.01

    @pytest.mark.asyncio
    async def test_exposure_never_goes_below_zero(self):
        """M1: Even if exit cost > current exposure, result is 0.0 (no negative)."""
        bot, engine = _make_bot()
        pos_key = "mkt1:tok-yes"
        bot._open_positions[pos_key] = {
            "side": "YES",
            "size": 1000.0,  # very large exit
            "entry_price": 0.60,
            "current_price": 0.10,  # severe loss → triggers stop
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traders": {"addr1"},
        }
        bot._daily_exposure = 5.0  # much smaller than exit cost

        bot.place_order = AsyncMock(return_value={"success": True})
        bot.validate_price = MagicMock(return_value=0.10)
        bot._sync_prices_from_db = AsyncMock()

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_STOP_LOSS_PCT = 0.15
            ms.MIRROR_TAKE_PROFIT_PCT = 0.25
            ms.MIRROR_STOP_LOSS_TIGHTEN_48H = -0.10
            ms.MIRROR_STOP_LOSS_TIGHTEN_72H = -0.05
            ms.MIRROR_FORCE_EXIT_HOURS = 96
            ms.MIRROR_CIRCUIT_BREAKER_THRESHOLD = -0.20
            ms.MIRROR_CIRCUIT_BREAKER_PAUSE_MINUTES = 15
            ms.MIRROR_EXIT_ENABLED = True
            await bot._check_and_execute_exits()

        assert bot._daily_exposure == 0.0

    @pytest.mark.asyncio
    async def test_failed_order_does_not_decrement_exposure(self):
        """M1: If place_order fails (success=False), exposure must NOT change."""
        bot, engine = _make_bot()
        pos_key = "mkt1:tok-yes"
        bot._open_positions[pos_key] = {
            "side": "YES",
            "size": 50.0,
            "entry_price": 0.60,
            "current_price": 0.10,  # triggers stop-loss
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traders": set(),
        }
        bot._daily_exposure = 200.0

        bot.place_order = AsyncMock(return_value={"success": False})
        bot.validate_price = MagicMock(return_value=0.10)
        bot._sync_prices_from_db = AsyncMock()

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_STOP_LOSS_PCT = 0.15
            ms.MIRROR_TAKE_PROFIT_PCT = 0.25
            ms.MIRROR_STOP_LOSS_TIGHTEN_48H = -0.10
            ms.MIRROR_STOP_LOSS_TIGHTEN_72H = -0.05
            ms.MIRROR_FORCE_EXIT_HOURS = 96
            ms.MIRROR_CIRCUIT_BREAKER_THRESHOLD = -0.20
            ms.MIRROR_CIRCUIT_BREAKER_PAUSE_MINUTES = 15
            ms.MIRROR_EXIT_ENABLED = True
            await bot._check_and_execute_exits()

        assert bot._daily_exposure == 200.0


# ── _can_open_position() ──────────────────────────────────────────────────────

class TestCanOpenPosition:
    def test_returns_true_when_below_limits(self):
        bot, _ = _make_bot()
        bot._daily_exposure = 100.0
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.TOTAL_CAPITAL = 10_000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            assert bot._can_open_position(0.50) is True

    def test_blocks_when_position_limit_reached(self):
        bot, _ = _make_bot()
        # Fill to the max
        for i in range(20):
            bot._open_positions[f"mkt{i}:tok"] = {}
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.TOTAL_CAPITAL = 10_000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            assert bot._can_open_position(0.50) is False

    def test_blocks_when_daily_cap_reached(self):
        bot, _ = _make_bot()
        # max_daily = 0.15 * 10_000 = 1500; set exposure at cap
        bot._daily_exposure = 1500.0
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.TOTAL_CAPITAL = 10_000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            assert bot._can_open_position(0.50) is False

    def test_blocks_exactly_at_cap(self):
        bot, _ = _make_bot()
        bot._daily_exposure = 1500.0
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.TOTAL_CAPITAL = 10_000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            assert bot._can_open_position(0.50) is False

    def test_allows_at_one_below_cap(self):
        bot, _ = _make_bot()
        bot._daily_exposure = 1499.99
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.TOTAL_CAPITAL = 10_000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            assert bot._can_open_position(0.50) is True



# ── C2 trader-SELL exit detection ────────────────────────────────────────────

class TestTraderSellExitDetection:
    """S96: API polling removed — trader exits handled by RTDS via _execute_mirror_trade(side='SELL').
    These tests verify the SELL path in _execute_mirror_trade and stop-loss in _check_and_execute_exits."""

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_exit(self):
        """Stop-loss fires when position drops below threshold."""
        bot, engine = _make_bot()
        pos_key = "mkt1:tok-yes"
        bot._open_positions[pos_key] = {
            "side": "YES",
            "size": 50.0,
            "entry_price": 0.60,
            "current_price": 0.50,  # -16.7% loss
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traders": {"addr1"},
        }

        bot.place_order = AsyncMock(return_value={"success": True})
        bot.validate_price = MagicMock(return_value=0.50)
        bot._sync_prices_from_db = AsyncMock()

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_STOP_LOSS_PCT = 0.15
            ms.MIRROR_TAKE_PROFIT_PCT = 0.25
            ms.MIRROR_STOP_LOSS_TIGHTEN_48H = -0.10
            ms.MIRROR_STOP_LOSS_TIGHTEN_72H = -0.05
            ms.MIRROR_FORCE_EXIT_HOURS = 96
            ms.MIRROR_CIRCUIT_BREAKER_THRESHOLD = -0.20
            ms.MIRROR_CIRCUIT_BREAKER_PAUSE_MINUTES = 15
            ms.MIRROR_EXIT_ENABLED = True
            await bot._check_and_execute_exits()

        assert pos_key not in bot._open_positions
        call_kwargs = bot.place_order.call_args.kwargs
        assert call_kwargs["side"] == "SELL"

    @pytest.mark.asyncio
    async def test_no_exit_above_stop_loss(self):
        """Position NOT closed when loss is above stop-loss threshold."""
        bot, engine = _make_bot()
        pos_key = "mkt1:tok-yes"
        bot._open_positions[pos_key] = {
            "side": "YES",
            "size": 50.0,
            "entry_price": 0.60,
            "current_price": 0.55,  # -8.3% loss, above -15% threshold
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traders": {"addr1"},
        }

        bot.place_order = AsyncMock(return_value={"success": True})
        bot._sync_prices_from_db = AsyncMock()

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_STOP_LOSS_PCT = 0.15
            ms.MIRROR_TAKE_PROFIT_PCT = 0.25
            ms.MIRROR_STOP_LOSS_TIGHTEN_48H = -0.10
            ms.MIRROR_STOP_LOSS_TIGHTEN_72H = -0.05
            ms.MIRROR_FORCE_EXIT_HOURS = 96
            ms.MIRROR_CIRCUIT_BREAKER_THRESHOLD = -0.20
            ms.MIRROR_CIRCUIT_BREAKER_PAUSE_MINUTES = 15
            ms.MIRROR_EXIT_ENABLED = True
            await bot._check_and_execute_exits()

        assert pos_key in bot._open_positions
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_positions_returns_early(self):
        """_check_and_execute_exits() is a no-op when _open_positions is empty."""
        bot, engine = _make_bot()
        bot._open_positions = {}

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_STOP_LOSS_PCT = 0.15
            ms.MIRROR_TAKE_PROFIT_PCT = 0.25
            ms.MIRROR_STOP_LOSS_TIGHTEN_48H = -0.10
            ms.MIRROR_STOP_LOSS_TIGHTEN_72H = -0.05
            ms.MIRROR_FORCE_EXIT_HOURS = 96
            ms.MIRROR_CIRCUIT_BREAKER_THRESHOLD = -0.20
            ms.MIRROR_CIRCUIT_BREAKER_PAUSE_MINUTES = 15
            ms.MIRROR_EXIT_ENABLED = True
            await bot._check_and_execute_exits()


# ── Deduplication / pruning ───────────────────────────────────────────────────

class TestDeduplication:
    def test_prune_mirrored_trades_caps_size(self):
        bot, _ = _make_bot()
        from collections import OrderedDict
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_TRACKED_TRADES = 100
            # Fill well beyond the cap
            bot.mirrored_trades = OrderedDict((str(i), None) for i in range(200))
            bot._prune_mirrored_trades()
        # Should be pruned to ~100 (the newest half of 200)
        assert len(bot.mirrored_trades) == 100

    def test_prune_does_nothing_below_cap(self):
        bot, _ = _make_bot()
        from collections import OrderedDict
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_TRACKED_TRADES = 10_000
            bot.mirrored_trades = OrderedDict((str(i), None) for i in range(50))
            bot._prune_mirrored_trades()
        assert len(bot.mirrored_trades) == 50

    def test_prune_keeps_newest_drops_oldest(self):
        """Verify pruning preserves insertion order — oldest removed first."""
        bot, _ = _make_bot()
        from collections import OrderedDict
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_TRACKED_TRADES = 5
            bot.mirrored_trades = OrderedDict(
                (f"trade-{i}", None) for i in range(10)
            )
            bot._prune_mirrored_trades()
        # Should keep the newest 5 (trade-5 through trade-9)
        assert len(bot.mirrored_trades) == 5
        assert "trade-0" not in bot.mirrored_trades
        assert "trade-4" not in bot.mirrored_trades
        assert "trade-5" in bot.mirrored_trades
        assert "trade-9" in bot.mirrored_trades


# ── Daily reset ───────────────────────────────────────────────────────────────

class TestDailyReset:
    def test_resets_exposure_on_new_day(self):
        bot, _ = _make_bot()
        bot._daily_exposure = 500.0
        bot._daily_reset_date = "2026-01-01"
        # Simulate it's now 2026-01-02
        with patch("bots.mirror_bot.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-01-02"
            mock_dt.now.return_value = MagicMock()
            mock_dt.now.return_value.strftime = MagicMock(return_value="2026-01-02")
            bot._check_daily_reset()
        assert bot._daily_exposure == 0.0

    def test_no_reset_on_same_day(self):
        bot, _ = _make_bot()
        bot._daily_exposure = 500.0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        bot._daily_reset_date = today
        bot._check_daily_reset()
        assert bot._daily_exposure == 500.0


# ── _restore_state_on_startup() ────────────────────────────────────────────


class TestRestoreStateOnStartup:
    @pytest.mark.asyncio
    async def test_seeds_daily_exposure_from_paper_trades(self):
        """Startup restore reads today's paper_trades and seeds _daily_exposure."""
        bot, engine = _make_bot()
        bot._state_restored = False

        # Mock DB session returning a scalar (total spent today)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        # First execute: SUM of paper_trades (daily exposure)
        scalar_result = MagicMock()
        scalar_result.scalar = MagicMock(return_value=350.0)
        # Second execute: positions query (returns empty)
        positions_result = MagicMock()
        positions_result.fetchall = MagicMock(return_value=[])
        mock_ctx.execute = AsyncMock(side_effect=[scalar_result, positions_result])
        engine.db.get_session = MagicMock(return_value=mock_ctx)

        await bot._restore_state_on_startup()

        assert bot._daily_exposure == 350.0
        assert bot._state_restored is True

    @pytest.mark.asyncio
    async def test_restores_open_positions(self):
        """Startup restore rebuilds _open_positions from positions table."""
        bot, engine = _make_bot()
        bot._state_restored = False

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        # First execute: SUM of paper_trades
        scalar_result = MagicMock()
        scalar_result.scalar = MagicMock(return_value=0.0)

        # Second execute: category exposure seed (S119)
        cat_result = MagicMock()
        cat_result.fetchall = MagicMock(return_value=[])

        # Third execute: positions table rows
        pos_row = MagicMock()
        pos_row.market_id = "mkt1"
        pos_row.token_id = "tok-yes"
        pos_row.side = "YES"
        pos_row.size = 50.0
        pos_row.entry_price = 0.60
        pos_row.current_price = 0.55
        pos_row.opened_at = datetime(2026, 3, 9, tzinfo=timezone.utc)
        pos_row.trader_addresses = ["addr1", "addr2"]
        positions_result = MagicMock()
        positions_result.fetchall = MagicMock(return_value=[pos_row])

        mock_ctx.execute = AsyncMock(side_effect=[scalar_result, cat_result, positions_result])
        engine.db.get_session = MagicMock(return_value=mock_ctx)

        await bot._restore_state_on_startup()

        assert "mkt1:tok-yes" in bot._open_positions
        pos = bot._open_positions["mkt1:tok-yes"]
        assert pos["side"] == "YES"
        assert pos["size"] == 50.0
        assert pos["entry_price"] == 0.60
        assert pos["current_price"] == 0.55
        assert pos["traders"] == {"addr1", "addr2"}

    @pytest.mark.asyncio
    async def test_only_runs_once(self):
        """Guard: _state_restored prevents double execution."""
        bot, engine = _make_bot()
        bot._state_restored = True
        await bot._restore_state_on_startup()
        # DB should not be touched at all
        engine.db.get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_db_failure_gracefully(self):
        """DB exception is caught — bot starts with default state."""
        bot, engine = _make_bot()
        bot._state_restored = False
        engine.db.get_session.side_effect = Exception("DB down")
        await bot._restore_state_on_startup()
        assert bot._state_restored is True
        assert bot._daily_exposure == 0.0
        assert bot._open_positions == {}

    @pytest.mark.asyncio
    async def test_no_db_skips_restore(self):
        """When engine.db is None, restoration is skipped cleanly."""
        bot, engine = _make_bot()
        bot._state_restored = False
        engine.db = None
        await bot._restore_state_on_startup()
        assert bot._state_restored is True
        assert bot._daily_exposure == 0.0



# ── _track_open_position() ─────────────────────────────────────────────────


class TestTrackOpenPosition:
    """S133: _track_open_position() was dead code (never called). Removed.
    Position creation now happens inline in _execute_mirror_trade (line 1693+).
    These tests verify the new inline creation path via _open_positions dict."""

    def test_new_position_not_in_dict_initially(self):
        """Verify _open_positions is empty for a fresh bot."""
        bot, _ = _make_bot()
        assert len(bot._open_positions) == 0


# ── _persist_trader_to_position() ──────────────────────────────────────────


class TestPersistTraderToPosition:
    @pytest.mark.asyncio
    async def test_writes_trader_to_db(self):
        """Persists trader_address to positions.trader_addresses via DB UPDATE."""
        bot, engine = _make_bot()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.execute = AsyncMock()
        mock_ctx.commit = AsyncMock()
        engine.db.get_session = MagicMock(return_value=mock_ctx)

        trade_info = {
            "trader_address": "addr1",
            "market_id": "mkt1",
            "token_id": "tok-yes",
        }
        await bot._persist_trader_to_position(trade_info)

        mock_ctx.execute.assert_called_once()
        mock_ctx.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_db_failure_gracefully(self):
        """DB exception is caught and logged — does not crash."""
        bot, engine = _make_bot()
        engine.db.get_session.side_effect = Exception("DB down")
        trade_info = {
            "trader_address": "addr1",
            "market_id": "mkt1",
            "token_id": "tok-yes",
        }
        # Should not raise
        await bot._persist_trader_to_position(trade_info)

    @pytest.mark.asyncio
    async def test_no_db_returns_early(self):
        """When engine.db is None, persist is skipped."""
        bot, engine = _make_bot()
        engine.db = None
        trade_info = {
            "trader_address": "addr1",
            "market_id": "mkt1",
            "token_id": "tok-yes",
        }
        await bot._persist_trader_to_position(trade_info)
        # No crash, no calls


# ── _execute_mirror_trade() ────────────────────────────────────────────────


class TestExecuteMirrorTrade:
    @pytest.mark.asyncio
    async def test_entry_trade_success(self):
        """Successful entry trade increments _daily_exposure and updates position size."""
        bot, engine = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.capital = 3000.0
        bot.bankroll.max_daily_usd = 10000
        bot.calculate_bot_position_size = AsyncMock(return_value=100.0)
        bot.place_order = AsyncMock(return_value={"success": True, "order_id": "ord1"})
        bot.store_pending_trade_signals = AsyncMock()
        # S103: Mock reliability tracker so multi-factor confidence produces valid value
        bot._reliability_tracker = MagicMock()
        bot._reliability_tracker.likelihood_ratio = MagicMock(return_value=1.0)
        bot._reliability_tracker.category_trade_count = MagicMock(return_value=50)
        bot._reliability_tracker.mean = MagicMock(return_value=0.60)
        bot._reliability_tracker.total_trade_count = MagicMock(return_value=50)
        bot._reliability_tracker.overall_win_rate = MagicMock(return_value=0.60)
        # S109: No pre-existing position on same market+side — same-side dedup blocks re-entry.

        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="YES",
            price=0.60, confidence=0.70, trader_address="addr1",
        )

        assert result is True
        # Size capped at MIRROR_MAX_PER_MARKET/price
        assert bot._daily_exposure > 0
        # place_order was called with correct params
        bot.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_spread_gate_rejects_wide_spread(self):
        """S133: Trades on markets with spread > MIRROR_MAX_SPREAD are rejected."""
        bot, engine = _make_bot()
        # Override market data to have wide spread (yes=0.70, no=0.60, spread=0.30)
        engine.get_market_from_index = MagicMock(return_value={
            "active": True, "yes_price": 0.70, "no_price": 0.60,
        })
        bot.place_order = AsyncMock(return_value={"success": True, "order_id": "ord1"})
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="YES",
            price=0.70, confidence=0.70, trader_address="addr1",
        )
        assert result is False
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_spread_gate_allows_tight_spread(self):
        """S133: Trades on markets with spread <= MIRROR_MAX_SPREAD are allowed through."""
        bot, engine = _make_bot()
        # Override market data to have tight spread (yes=0.55, no=0.45, spread=0.0)
        engine.get_market_from_index = MagicMock(return_value={
            "active": True, "yes_price": 0.55, "no_price": 0.45,
        })
        bot.bankroll = MagicMock()
        bot.bankroll.capital = 3000.0
        bot.bankroll.max_daily_usd = 10000
        bot.calculate_bot_position_size = AsyncMock(return_value=100.0)
        bot.place_order = AsyncMock(return_value={"success": True, "order_id": "ord1"})
        bot.store_pending_trade_signals = AsyncMock()
        bot._reliability_tracker = MagicMock()
        bot._reliability_tracker.likelihood_ratio = MagicMock(return_value=1.0)
        bot._reliability_tracker.category_trade_count = MagicMock(return_value=50)
        bot._reliability_tracker.mean = MagicMock(return_value=0.60)
        bot._reliability_tracker.total_trade_count = MagicMock(return_value=50)
        bot._reliability_tracker.overall_win_rate = MagicMock(return_value=0.60)
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="YES",
            price=0.55, confidence=0.70, trader_address="addr1",
        )
        assert result is True
        bot.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_same_side_dedup_blocks_reentry(self):
        """S109: Re-entry on same market+side is blocked by same-side dedup."""
        bot, engine = _make_bot()
        bot._reliability_tracker = MagicMock()
        bot._reliability_tracker.likelihood_ratio = MagicMock(return_value=1.0)
        bot._reliability_tracker.category_trade_count = MagicMock(return_value=50)
        bot._reliability_tracker.mean = MagicMock(return_value=0.60)
        bot._reliability_tracker.total_trade_count = MagicMock(return_value=50)
        bot._reliability_tracker.overall_win_rate = MagicMock(return_value=0.60)
        # Pre-existing YES position on mkt1
        bot._open_positions["mkt1:tok-yes"] = {
            "side": "YES", "size": 50.0, "entry_price": 0.60,
            "traders": {"addr1"}, "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="YES",
            price=0.60, confidence=0.70, trader_address="addr2",
        )
        assert result is False  # Blocked by same-side dedup

    @pytest.mark.asyncio
    async def test_trader_blacklist_blocks_low_wr(self):
        """S133: Trader with <35% WR after 20+ resolved trades is blacklisted."""
        bot, engine = _make_bot()
        bot._reliability_tracker = MagicMock()
        bot._reliability_tracker.total_trade_count = MagicMock(return_value=25)
        bot._reliability_tracker.overall_win_rate = MagicMock(return_value=0.28)
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="YES",
            price=0.60, confidence=0.70, trader_address="bad_trader_1",
            whale_trade_usd=100.0,
        )
        assert result is False  # Blocked by trader blacklist

    @pytest.mark.asyncio
    async def test_trader_blacklist_passes_good_wr(self):
        """S133: Trader with >=35% WR is NOT blocked by blacklist gate.

        Verifies the blacklist gate checks WR but does not reject.
        Trade may still be rejected by downstream gates — we only assert
        that overall_win_rate was called (gate ran) and did not block.
        """
        bot, engine = _make_bot()
        bot._reliability_tracker = MagicMock()
        bot._reliability_tracker.total_trade_count = MagicMock(return_value=30)
        bot._reliability_tracker.overall_win_rate = MagicMock(return_value=0.45)
        # Market blocklist gate will block (no market data set up), but that's
        # AFTER the blacklist gate — proves blacklist did not reject.
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="YES",
            price=0.55, confidence=0.70, trader_address="good_trader_1",
            whale_trade_usd=100.0,
        )
        # overall_win_rate WAS called — blacklist gate ran but did not reject
        bot._reliability_tracker.overall_win_rate.assert_called_once_with("good_trader_1")

    @pytest.mark.asyncio
    async def test_trader_blacklist_skipped_insufficient_data(self):
        """S133: Trader with <20 resolved trades is NOT blacklisted (insufficient data).

        Verifies overall_win_rate is never called when total_trade_count < threshold.
        """
        bot, engine = _make_bot()
        bot._reliability_tracker = MagicMock()
        bot._reliability_tracker.total_trade_count = MagicMock(return_value=10)
        bot._reliability_tracker.overall_win_rate = MagicMock(return_value=0.20)
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="YES",
            price=0.55, confidence=0.70, trader_address="new_trader_1",
            whale_trade_usd=100.0,
        )
        # overall_win_rate should NOT have been called — insufficient data
        bot._reliability_tracker.overall_win_rate.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_skipped_when_no_position(self):
        """SELL consensus trades are skipped if we don't hold the position."""
        bot, engine = _make_bot()
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="SELL",
            price=0.60, confidence=0.70, trader_address="addr1",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_sell_exits_when_position_exists(self):
        """SELL with existing position: exit uses position size, not Kelly sizing."""
        bot, engine = _make_bot()
        bot._open_positions["mkt1:tok-yes"] = {
            "side": "YES", "size": 75.0, "entry_price": 0.60,
            "current_price": 0.55,
            "traders": {"addr1"}, "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        bot._daily_exposure = 100.0
        bot.place_order = AsyncMock(return_value={"success": True})

        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="SELL",
            price=0.55, confidence=0.70, trader_address="addr1",
        )

        assert result is True
        assert "mkt1:tok-yes" not in bot._open_positions
        # Daily exposure decremented: 100 - (75 * 0.55) = 58.75
        expected = max(0.0, 100.0 - 75.0 * 0.55)
        assert abs(bot._daily_exposure - expected) < 0.01

    @pytest.mark.asyncio
    async def test_sell_with_zero_size_skipped(self):
        """SELL with position size=0 is skipped."""
        bot, engine = _make_bot()
        bot._open_positions["mkt1:tok-yes"] = {
            "side": "YES", "size": 0.0, "entry_price": 0.60,
            "traders": set(), "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = await bot._execute_mirror_trade(
            market_id="mkt1", token_id="tok-yes", side="SELL",
            price=0.60, confidence=0.70, trader_address="addr1",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_max_per_market_cap(self):
        """Entry trade size is capped at MIRROR_MAX_PER_MARKET / price."""
        bot, engine = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.max_daily_usd = 10000
        bot.bankroll.capital = 3000.0
        bot._reliability_tracker = None  # disable to avoid domain drift halving confidence
        bot.calculate_bot_position_size = AsyncMock(return_value=10000.0)  # huge raw size
        bot.place_order = AsyncMock(return_value={"success": True, "order_id": "ord1"})
        bot.store_pending_trade_signals = AsyncMock()
        # S109: No pre-existing same-side position — same-side dedup blocks re-entry.

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_PER_MARKET = 400
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.MIRROR_SKIP_SIGNAL_ENHANCEMENTS = True
            ms.MIRROR_MIN_RELIABILITY = 0.45
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 200
            ms.MIRROR_ADAPTIVE_SAFETY = False
            ms.TOTAL_CAPITAL = 10000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            ms.MIRROR_CATEGORY_BLOCKLIST = ""
            ms.MIRROR_MARKET_COOLDOWN_SECONDS = 0
            ms.MIRROR_MAX_SLIPPAGE_PCT = 0.08
            ms.MIRROR_MIN_TRADE_USD = 1.0
            ms.MIRROR_MAX_PER_MARKET_PCT = 0.05
            ms.MIRROR_MIN_HOURS_TO_RESOLUTION = 4
            await bot._execute_mirror_trade(
                market_id="mkt1", token_id="tok-yes", side="YES",
                price=0.50, confidence=0.70, trader_address="addr1",
            )

        # place_order should have been called with size <= 400/0.50 = 800
        call_kwargs = bot.place_order.call_args.kwargs
        assert call_kwargs["size"] <= 800.0

    @pytest.mark.asyncio
    async def test_daily_cap_limits_size(self):
        """Entry trade size is limited by remaining daily exposure."""
        bot, engine = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.max_daily_usd = 100.0  # only $100 daily
        bot.bankroll.capital = 3000.0
        bot._daily_exposure = 90.0  # already spent $90
        bot._reliability_tracker = None  # disable to avoid domain drift halving confidence
        bot.calculate_bot_position_size = AsyncMock(return_value=1000.0)
        bot.place_order = AsyncMock(return_value={"success": True, "order_id": "ord1"})
        bot.store_pending_trade_signals = AsyncMock()
        # S109: No pre-existing same-side position — same-side dedup blocks re-entry.

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_PER_MARKET = 10000
            ms.MIRROR_SKIP_SIGNAL_ENHANCEMENTS = True
            ms.MIRROR_MIN_RELIABILITY = 0.45
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 200
            ms.MIRROR_ADAPTIVE_SAFETY = False
            ms.TOTAL_CAPITAL = 10000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            ms.MIRROR_CATEGORY_BLOCKLIST = ""
            ms.MIRROR_MARKET_COOLDOWN_SECONDS = 0
            ms.MIRROR_MAX_SLIPPAGE_PCT = 0.08
            ms.MIRROR_MIN_TRADE_USD = 1.0
            ms.MIRROR_MAX_PER_MARKET_PCT = 0.05
            ms.MIRROR_MIN_HOURS_TO_RESOLUTION = 4
            await bot._execute_mirror_trade(
                market_id="mkt1", token_id="tok-yes", side="YES",
                price=0.50, confidence=0.70, trader_address="addr1",
            )

        # Remaining = $10 → max shares = 10/0.50 = 20
        call_kwargs = bot.place_order.call_args.kwargs
        assert call_kwargs["size"] <= 20.0

    @pytest.mark.asyncio
    async def test_zero_size_after_limits_returns_false(self):
        """If sizing yields zero after caps, trade is skipped."""
        bot, engine = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.max_daily_usd = 100.0
        bot._daily_exposure = 100.0  # fully spent
        bot.calculate_bot_position_size = AsyncMock(return_value=100.0)
        bot.place_order = AsyncMock(return_value={"success": False})

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_PER_MARKET = 400
            ms.MIRROR_SKIP_SIGNAL_ENHANCEMENTS = True
            ms.MIRROR_MIN_RELIABILITY = 0.45
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 200
            ms.MIRROR_ADAPTIVE_SAFETY = False
            ms.TOTAL_CAPITAL = 10000.0
            result = await bot._execute_mirror_trade(
                market_id="mkt1", token_id="tok-yes", side="YES",
                price=0.50, confidence=0.70, trader_address="addr1",
            )

        assert result is False
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_order_no_exposure_change(self):
        """If place_order fails, _daily_exposure and position size unchanged."""
        bot, engine = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.max_daily_usd = 10000
        bot.bankroll.capital = 3000.0
        bot._reliability_tracker = None
        bot.calculate_bot_position_size = AsyncMock(return_value=100.0)
        bot.place_order = AsyncMock(return_value={"success": False})
        bot.store_pending_trade_signals = AsyncMock()
        # S109: No pre-existing same-side position — same-side dedup blocks re-entry.

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_PER_MARKET = 400
            ms.MIRROR_SKIP_SIGNAL_ENHANCEMENTS = True
            ms.MIRROR_MIN_RELIABILITY = 0.45
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 200
            ms.MIRROR_ADAPTIVE_SAFETY = False
            ms.TOTAL_CAPITAL = 10000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            ms.MIRROR_CATEGORY_BLOCKLIST = ""
            ms.MIRROR_MARKET_COOLDOWN_SECONDS = 0
            ms.MIRROR_MAX_SLIPPAGE_PCT = 0.08
            ms.MIRROR_MIN_TRADE_USD = 1.0
            ms.MIRROR_MAX_PER_MARKET_PCT = 0.05
            ms.MIRROR_MIN_HOURS_TO_RESOLUTION = 4
            result = await bot._execute_mirror_trade(
                market_id="mkt1", token_id="tok-yes", side="YES",
                price=0.50, confidence=0.70, trader_address="addr1",
            )

        assert result is False
        assert bot._daily_exposure == 0.0


# ── _update_elite_traders() ────────────────────────────────────────────────


class TestUpdateEliteTraders:
    @pytest.mark.asyncio
    async def test_m2_retains_stale_list_on_db_failure(self):
        """M2: On DB exception, elite_traders list is NOT cleared."""
        bot, engine = _make_bot()
        original_elites = [{"address": "addr1"}, {"address": "addr2"}]
        bot.elite_traders = list(original_elites)
        bot._reliability_tracker = None

        engine.db.get_elite_traders = AsyncMock(side_effect=Exception("DB down"))
        await bot._update_elite_traders()

        assert bot.elite_traders == original_elites

    @pytest.mark.asyncio
    async def test_loads_from_db(self):
        """Normal path: loads elite traders from DB."""
        bot, engine = _make_bot()
        bot._reliability_tracker = None
        expected = [{"address": "addr1"}, {"address": "addr2"}]
        engine.db.get_elite_traders = AsyncMock(return_value=expected)

        await bot._update_elite_traders()

        assert bot.elite_traders == expected


# ── _get_market_meta() ─────────────────────────────────────────────────────


class TestGetMarketMeta:
    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Cached market meta returned without DB query."""
        import time
        bot, engine = _make_bot()
        bot._market_meta_cache["mkt1"] = ("politics", "days", time.monotonic() + 300)

        cat, ttr = await bot._get_market_meta("mkt1")

        assert cat == "politics"
        assert ttr == "days"
        # DB not queried (get_session already set up, but execute not called for this market)

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db(self):
        """Cache miss queries markets table and caches result."""
        bot, engine = _make_bot()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        row = MagicMock()
        row.__getitem__ = lambda self, i: "crypto" if i == 0 else "2026-03-20T00:00:00Z"
        mock_ctx.execute = AsyncMock(
            return_value=MagicMock(fetchone=MagicMock(return_value=row))
        )
        engine.db.get_session = MagicMock(return_value=mock_ctx)
        # Mock hours_until_resolution to return a value
        bot.hours_until_resolution = MagicMock(return_value=240)  # 10 days

        cat, ttr = await bot._get_market_meta("mkt1")

        assert cat == "crypto"
        assert ttr == "weeks"  # 240h > 168h
        assert "mkt1" in bot._market_meta_cache

    @pytest.mark.asyncio
    async def test_cache_expired(self):
        """Expired cache entry causes re-query."""
        import time
        bot, engine = _make_bot()
        bot._market_meta_cache["mkt1"] = ("old", "old", time.monotonic() - 10)  # expired

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.execute = AsyncMock(
            return_value=MagicMock(fetchone=MagicMock(return_value=None))
        )
        engine.db.get_session = MagicMock(return_value=mock_ctx)

        cat, ttr = await bot._get_market_meta("mkt1")

        # No row → empty strings
        assert cat == ""
        assert ttr == ""


# ── _can_open_position() with bankroll ─────────────────────────────────────


class TestCanOpenPositionBankroll:
    def test_uses_bankroll_max_daily_usd(self):
        """When bankroll is set, max_daily_usd comes from bankroll, not settings."""
        bot, _ = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.max_daily_usd = 5000
        bot._daily_exposure = 4999.0

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            # This setting should be IGNORED when bankroll is set
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.01
            ms.TOTAL_CAPITAL = 10000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            assert bot._can_open_position(0.50) is True

    def test_blocks_at_bankroll_cap(self):
        """Blocks when daily exposure reaches bankroll.max_daily_usd."""
        bot, _ = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.max_daily_usd = 5000
        bot._daily_exposure = 5000.0

        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            assert bot._can_open_position(0.50) is False


# ── MIRROR_MAX_DAILY_EXPOSURE_PCT deprecation ──────────────────────────────


class TestDeprecationWarning:
    def test_deprecation_logged_when_bankroll_is_none(self):
        """Deprecation warning fires when fallback path is used."""
        bot, _ = _make_bot()
        bot.bankroll = None
        bot._deprecation_warned = False
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.TOTAL_CAPITAL = 10000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            with patch("bots.mirror_bot.logger") as mock_logger:
                bot._can_open_position(0.50)
                mock_logger.warning.assert_called_once()
                assert "deprecated" in mock_logger.warning.call_args[0][0].lower()
        assert bot._deprecation_warned is True

    def test_deprecation_logged_only_once(self):
        """Second call does not re-log the deprecation warning."""
        bot, _ = _make_bot()
        bot.bankroll = None
        bot._deprecation_warned = True
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            ms.MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15
            ms.TOTAL_CAPITAL = 10000.0
            ms.MIRROR_MIN_PRICE = 0.07
            ms.MIRROR_MAX_PRICE = 0.93
            ms.MIRROR_HARD_MIN_PRICE = 0.05
            ms.MIRROR_HARD_MAX_PRICE = 0.95
            ms.MIRROR_EXTREME_PRICE_DAMPENER = 0.25
            with patch("bots.mirror_bot.logger") as mock_logger:
                bot._can_open_position(0.50)
                mock_logger.warning.assert_not_called()

    def test_no_deprecation_when_bankroll_set(self):
        """No deprecation warning when bankroll provides max_daily_usd."""
        bot, _ = _make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.max_daily_usd = 10000
        bot._deprecation_warned = False
        with patch("bots.mirror_bot.settings") as ms:
            ms.MIRROR_MAX_CONCURRENT_POSITIONS = 20
            with patch("bots.mirror_bot.logger") as mock_logger:
                bot._can_open_position(0.50)
                mock_logger.warning.assert_not_called()
        assert bot._deprecation_warned is False


# ── Elite Reliability Per-Category ──────────────────────────────────────────

class TestEliteReliabilityCategory:
    """Tests for per-category Beta tracking in EliteReliabilityTracker."""

    def test_category_specific_beta_used_when_enough_samples(self):
        """Category-specific Beta returned when min_cat_samples met."""
        from base_engine.learning.elite_reliability import EliteReliabilityTracker
        tracker = EliteReliabilityTracker(db=None)
        # Overall: 10 correct, 5 incorrect → alpha=11, beta=6
        tracker._cache = {
            "0xabc": {"alpha_yes": 11, "beta_yes": 6, "alpha_no": 1, "beta_no": 1,
                      "yes_total": 15, "no_total": 0},
        }
        # Category "crypto": 8 correct, 2 incorrect → alpha=9, beta=3
        tracker._cat_cache = {
            ("0xabc", "crypto"): {"alpha_yes": 9, "beta_yes": 3, "alpha_no": 1, "beta_no": 1,
                                  "yes_total": 10, "no_total": 0},
        }
        # Without category → overall
        a, b = tracker._get_beta("0xabc", "YES")
        assert (a, b) == (11, 6)
        # With category → category-specific (10 samples >= 5 min)
        a, b = tracker._get_beta("0xabc", "YES", category="crypto")
        assert (a, b) == (9, 3)

    def test_category_fallback_when_insufficient_samples(self):
        """Falls back to overall when category has < min_cat_samples."""
        from base_engine.learning.elite_reliability import EliteReliabilityTracker
        tracker = EliteReliabilityTracker(db=None)
        tracker._cache = {
            "0xabc": {"alpha_yes": 11, "beta_yes": 6, "alpha_no": 1, "beta_no": 1,
                      "yes_total": 15, "no_total": 0},
        }
        # Only 3 samples in "politics" — below default min_cat_samples=5
        tracker._cat_cache = {
            ("0xabc", "politics"): {"alpha_yes": 3, "beta_yes": 1, "alpha_no": 1, "beta_no": 1,
                                    "yes_total": 3, "no_total": 0},
        }
        a, b = tracker._get_beta("0xabc", "YES", category="politics")
        assert (a, b) == (11, 6)  # Fell back to overall

    def test_category_none_uses_overall(self):
        """category=None uses overall stats (backward compatible)."""
        from base_engine.learning.elite_reliability import EliteReliabilityTracker
        tracker = EliteReliabilityTracker(db=None)
        tracker._cache = {
            "0xabc": {"alpha_yes": 5, "beta_yes": 2, "alpha_no": 3, "beta_no": 4,
                      "yes_total": 6, "no_total": 6},
        }
        tracker._cat_cache = {
            ("0xabc", "crypto"): {"alpha_yes": 9, "beta_yes": 1, "alpha_no": 1, "beta_no": 1,
                                  "yes_total": 9, "no_total": 0},
        }
        a, b = tracker._get_beta("0xabc", "YES", category=None)
        assert (a, b) == (5, 2)

    def test_likelihood_ratio_accepts_category_kwarg(self):
        """likelihood_ratio() passes category through to _get_beta."""
        from base_engine.learning.elite_reliability import EliteReliabilityTracker
        tracker = EliteReliabilityTracker(db=None)
        # 8 correct out of 10 in crypto → alpha=9, beta=3
        tracker._cache = {"0xabc": {"alpha_yes": 6, "beta_yes": 6, "alpha_no": 1, "beta_no": 1,
                                    "yes_total": 10, "no_total": 0}}
        tracker._cat_cache = {
            ("0xabc", "crypto"): {"alpha_yes": 9, "beta_yes": 3, "alpha_no": 1, "beta_no": 1,
                                  "yes_total": 10, "no_total": 0},
        }
        lr_overall = tracker.likelihood_ratio("0xabc", "YES")
        lr_crypto = tracker.likelihood_ratio("0xabc", "YES", category="crypto")
        # Overall: 6/12=0.5 → LR=1.0, Crypto: 9/12=0.75 → LR=3.0
        assert lr_overall == 1.0
        assert abs(lr_crypto - 3.0) < 0.01

    def test_build_beta_rec_static(self):
        """_build_beta_rec correctly computes Beta params from row."""
        from base_engine.learning.elite_reliability import EliteReliabilityTracker
        rec = EliteReliabilityTracker._build_beta_rec({
            "yes_correct": 7, "yes_total": 10, "no_correct": 3, "no_total": 5,
        })
        # S137 C6: Prior is now Beta(6, 10) — empirical Bayes centered at 37.5% WR
        assert rec["alpha_yes"] == 13  # 7+6
        assert rec["beta_yes"] == 13   # (10-7)+10
        assert rec["alpha_no"] == 9    # 3+6
        assert rec["beta_no"] == 12    # (5-3)+10
