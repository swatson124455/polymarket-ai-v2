"""
Unit tests for esports/kelly/esports_bankroll_manager.py (EsportsBankrollManager).

Tests:
  - get_bet_size returns 0 when no edge (fair_prob <= market_price)
  - get_bet_size returns positive when edge exists
  - get_bet_size respects per-bet cap (ESPORTS_MAX_BET_USD)
  - get_bet_size respects daily cap (ESPORTS_MAX_DAILY_USD)
  - get_bet_size returns 0 when daily cap exhausted
  - get_bet_size returns 0 when result < $1 minimum
  - _get_daily_spent sums all 3 esports bot names
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from esports.kelly.esports_bankroll_manager import EsportsBankrollManager


def make_manager(daily_exposure=None):
    """
    Create an EsportsBankrollManager with a mocked order_gateway.

    Args:
        daily_exposure: Dict mapping bot_name -> float of daily exposure.
    """
    gw = MagicMock()
    gw._daily_exposure_usd = daily_exposure or {}
    return EsportsBankrollManager(order_gateway=gw)


# =========================================================================
# get_bet_size — No Edge
# =========================================================================


class TestNoEdge:
    @pytest.mark.asyncio
    async def test_zero_when_no_edge(self):
        """fair_prob <= market_price -> edge <= 0 -> returns 0."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.50, market_price=0.55, game="lol"
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_equal_price(self):
        """fair_prob == market_price -> edge = 0 -> returns 0."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.60, market_price=0.60, game="cs2"
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_fair_prob_zero(self):
        """fair_prob = 0 -> returns 0 (edge check short-circuit)."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.0, market_price=0.50, game="lol"
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_fair_prob_one(self):
        """fair_prob = 1.0 -> returns 0 (boundary check)."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=1.0, market_price=0.50, game="lol"
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_market_price_zero(self):
        """market_price = 0 -> returns 0 (boundary check)."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.60, market_price=0.0, game="lol"
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_market_price_one(self):
        """market_price = 1.0 -> returns 0 (boundary check)."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.60, market_price=1.0, game="lol"
            )
        assert result == 0.0


# =========================================================================
# get_bet_size — Positive Edge
# =========================================================================


class TestPositiveEdge:
    @pytest.mark.asyncio
    async def test_returns_positive_with_edge(self):
        """fair_prob > market_price -> positive bet size returned."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.65, market_price=0.50, game="lol"
            )
        assert result > 0.0

    @pytest.mark.asyncio
    async def test_kelly_formula_correctness(self):
        """Verify exact Kelly formula: fraction * capital * (edge / fair_prob)."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25

            # edge = 0.65 - 0.50 = 0.15
            # kelly_bet = 0.25 * 5000 * (0.15 / 0.65) = 1250 * 0.2308 = 288.46
            # Capped at per-bet max: min(288.46, 100) = 100
            result = await mgr.get_bet_size(
                fair_prob=0.65, market_price=0.50, game="lol"
            )
        assert result == pytest.approx(100.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_small_edge_uncapped(self):
        """Small edge -> kelly_bet below per-bet cap -> uncapped."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25

            # edge = 0.55 - 0.50 = 0.05
            # kelly_bet = 0.25 * 5000 * (0.05 / 0.55) = 1250 * 0.0909 = 113.64
            # Capped at per-bet max: min(113.64, 100) = 100
            # Still capped. Try even smaller edge.
            # edge = 0.52 - 0.50 = 0.02
            # kelly_bet = 0.25 * 5000 * (0.02 / 0.52) = 1250 * 0.0385 = 48.08
            # Uncapped (48.08 < 100)
            result = await mgr.get_bet_size(
                fair_prob=0.52, market_price=0.50, game="lol"
            )
        assert result == pytest.approx(48.08, abs=0.1)
        assert result < 100.0


# =========================================================================
# Per-Bet Cap
# =========================================================================


class TestPerBetCap:
    @pytest.mark.asyncio
    async def test_capped_at_max_bet_usd(self):
        """Large edge -> kelly_bet exceeds max -> capped at ESPORTS_MAX_BET_USD."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 50.0  # Low cap
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25

            # edge = 0.80 - 0.40 = 0.40
            # kelly_bet = 0.25 * 5000 * (0.40 / 0.80) = 1250 * 0.50 = 625.0
            # Capped at 50
            result = await mgr.get_bet_size(
                fair_prob=0.80, market_price=0.40, game="lol"
            )
        assert result == pytest.approx(50.0, abs=0.01)


# =========================================================================
# Daily Cap
# =========================================================================


class TestDailyCap:
    @pytest.mark.asyncio
    async def test_respects_daily_cap(self):
        """When daily exposure is partially spent, remaining amount limits bet."""
        # Already spent 480 of 500 daily cap
        mgr = make_manager(daily_exposure={"EsportsBot": 480.0})
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25

            # Kelly would say ~100 but only 20 remaining daily
            result = await mgr.get_bet_size(
                fair_prob=0.70, market_price=0.50, game="lol"
            )
        assert result == pytest.approx(20.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_zero_when_daily_cap_exhausted(self):
        """When daily cap fully spent, returns 0."""
        mgr = make_manager(daily_exposure={"EsportsBot": 500.0})
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25

            result = await mgr.get_bet_size(
                fair_prob=0.70, market_price=0.50, game="lol"
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_daily_over_spent(self):
        """When daily exposure exceeds cap, returns 0."""
        mgr = make_manager(daily_exposure={"EsportsBot": 600.0})
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25

            result = await mgr.get_bet_size(
                fair_prob=0.70, market_price=0.50, game="lol"
            )
        assert result == 0.0


# =========================================================================
# Minimum Bet Size
# =========================================================================


class TestMinimumBetSize:
    @pytest.mark.asyncio
    async def test_zero_when_below_one_dollar(self):
        """Bet < $1 is rounded down to 0 (not meaningful)."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 100.0  # Very small capital
            mock_settings.ESPORTS_MAX_BET_USD = 100.0
            mock_settings.ESPORTS_MAX_DAILY_USD = 500.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25

            # edge = 0.505 - 0.50 = 0.005
            # kelly_bet = 0.25 * 100 * (0.005 / 0.505) = 25 * 0.0099 = 0.2475
            # < $1 minimum -> return 0
            result = await mgr.get_bet_size(
                fair_prob=0.505, market_price=0.50, game="lol"
            )
        assert result == 0.0


# =========================================================================
# _get_daily_spent — sums all 3 esports bot names
# =========================================================================


class TestGetDailySpent:
    def test_sums_all_esports_bot_names(self):
        """_get_daily_spent sums EsportsBot + EsportsLiveBot."""
        mgr = make_manager(daily_exposure={
            "EsportsBot": 100.0,
            "EsportsLiveBot": 50.0,
            "ArbitrageBot": 999.0,  # Not included
        })
        total = mgr._get_daily_spent()
        assert total == pytest.approx(150.0, abs=0.01)

    def test_missing_bots_treated_as_zero(self):
        """Missing bot names default to 0."""
        mgr = make_manager(daily_exposure={"EsportsBot": 100.0})
        total = mgr._get_daily_spent()
        assert total == pytest.approx(100.0, abs=0.01)

    def test_empty_exposure_dict(self):
        """Empty daily_exposure dict -> 0.0 total."""
        mgr = make_manager(daily_exposure={})
        total = mgr._get_daily_spent()
        assert total == pytest.approx(0.0, abs=0.01)

    def test_no_order_gateway_returns_zero(self):
        """No order_gateway -> 0.0."""
        mgr = EsportsBankrollManager(order_gateway=None)
        total = mgr._get_daily_spent()
        assert total == 0.0

    def test_ignores_non_esports_bots(self):
        """Other bot names (SportsBot, WeatherBot) are not summed."""
        mgr = make_manager(daily_exposure={
            "SportsBot": 200.0,
            "WeatherBot": 300.0,
            "EsportsBot": 50.0,
        })
        total = mgr._get_daily_spent()
        assert total == pytest.approx(50.0, abs=0.01)


# =========================================================================
# get_daily_esports_exposure (async wrapper)
# =========================================================================


class TestGetDailyEsportsExposure:
    @pytest.mark.asyncio
    async def test_returns_same_as_sync(self):
        """Async wrapper returns same value as _get_daily_spent."""
        mgr = make_manager(daily_exposure={
            "EsportsBot": 100.0,
            "EsportsLiveBot": 50.0,
        })
        result = await mgr.get_daily_esports_exposure()
        assert result == pytest.approx(150.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_lock_guarded(self):
        """The method uses the daily lock (no assertion needed, just verify it runs)."""
        mgr = make_manager()
        result = await mgr.get_daily_esports_exposure()
        assert result == pytest.approx(0.0)


# =========================================================================
# Kelly fraction fallback
# =========================================================================


class TestKellyFraction:
    @pytest.mark.asyncio
    async def test_default_fraction_when_no_db(self):
        """Without db, uses ESPORTS_KELLY_DEFAULT_FRACTION."""
        mgr = make_manager()
        with patch("esports.kelly.esports_bankroll_manager.settings") as mock_settings:
            mock_settings.ESPORTS_TOTAL_CAPITAL = 5000.0
            mock_settings.ESPORTS_MAX_BET_USD = 1000.0  # High cap to avoid capping
            mock_settings.ESPORTS_MAX_DAILY_USD = 5000.0
            mock_settings.ESPORTS_KELLY_DEFAULT_FRACTION = 0.10

            # edge = 0.60 - 0.50 = 0.10
            # kelly_bet = 0.10 * 5000 * (0.10 / 0.60) = 500 * 0.1667 = 83.33
            result = await mgr.get_bet_size(
                fair_prob=0.60, market_price=0.50, game="lol", db=None
            )
        assert result == pytest.approx(83.33, abs=0.5)
