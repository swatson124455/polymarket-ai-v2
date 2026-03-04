"""
Unit tests for sports/kelly/adaptive_kelly.py and sports/kelly/bankroll_manager.py

Tests:
  - compute_kelly_fraction Brier thresholds
  - get_kelly_fraction returns default when DB is None
  - Bankroll manager Kelly formula
  - Hard caps ($100/bet, $500/day)
  - Daily spent deduction
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestComputeKellyFraction:
    """Tests for compute_kelly_fraction."""

    def test_brier_above_threshold_returns_min(self):
        from sports.kelly.adaptive_kelly import compute_kelly_fraction
        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_MIN_FRACTION = 0.10
            mock_settings.SPORTS_KELLY_MAX_FRACTION = 0.50
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = compute_kelly_fraction(0.35)  # > 0.30
        assert result == 0.10

    def test_brier_below_threshold_returns_max(self):
        from sports.kelly.adaptive_kelly import compute_kelly_fraction
        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_MIN_FRACTION = 0.10
            mock_settings.SPORTS_KELLY_MAX_FRACTION = 0.50
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = compute_kelly_fraction(0.15)  # < 0.20
        assert result == 0.50

    def test_brier_none_returns_default(self):
        from sports.kelly.adaptive_kelly import compute_kelly_fraction
        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_MIN_FRACTION = 0.10
            mock_settings.SPORTS_KELLY_MAX_FRACTION = 0.50
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = compute_kelly_fraction(None)
        assert result == 0.25

    def test_brier_at_midpoint_interpolates(self):
        from sports.kelly.adaptive_kelly import compute_kelly_fraction
        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_MIN_FRACTION = 0.10
            mock_settings.SPORTS_KELLY_MAX_FRACTION = 0.50
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = compute_kelly_fraction(0.25)  # midpoint of 0.20–0.30
        # t = (0.30 - 0.25) / 0.10 = 0.5
        # result = 0.10 + 0.5 * (0.50 - 0.10) = 0.10 + 0.20 = 0.30
        assert abs(result - 0.30) < 0.001

    def test_brier_at_0_30_returns_min(self):
        from sports.kelly.adaptive_kelly import compute_kelly_fraction
        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_MIN_FRACTION = 0.10
            mock_settings.SPORTS_KELLY_MAX_FRACTION = 0.50
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = compute_kelly_fraction(0.30)
        assert result == 0.10  # brier > 0.30 is min, 0.30 is not > 0.30 so interpolation
        # Actually 0.30 is not > 0.30, so t = (0.30 - 0.30)/0.10 = 0.0 → result = min_f = 0.10
        assert result == 0.10

    def test_brier_at_0_20_returns_max(self):
        from sports.kelly.adaptive_kelly import compute_kelly_fraction
        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_MIN_FRACTION = 0.10
            mock_settings.SPORTS_KELLY_MAX_FRACTION = 0.50
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = compute_kelly_fraction(0.20)
        # brier < 0.20 is False (0.20 is not < 0.20)
        # falls through to interpolation: t = (0.30 - 0.20)/0.10 = 1.0 → max_f = 0.50
        assert result == 0.50


class TestGetKellyFraction:
    """Tests for get_kelly_fraction."""

    @pytest.mark.asyncio
    async def test_returns_default_when_no_db(self):
        from sports.kelly.adaptive_kelly import get_kelly_fraction
        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            mock_settings.SPORTS_CALIBRATION_UPDATE_INTERVAL = 3600
            result = await get_kelly_fraction("nba", "moneyline", db=None)
        assert result == 0.25

    @pytest.mark.asyncio
    async def test_uses_cache_when_available(self):
        from sports.kelly.adaptive_kelly import get_kelly_fraction, _FRACTION_CACHE
        import time

        # Pre-populate cache
        _FRACTION_CACHE[("nba", "moneyline")] = (time.monotonic(), 0.35)

        with patch("sports.kelly.adaptive_kelly.settings") as mock_settings:
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            mock_settings.SPORTS_CALIBRATION_UPDATE_INTERVAL = 3600

            mock_db = MagicMock()
            result = await get_kelly_fraction("nba", "moneyline", db=mock_db)

        assert result == 0.35
        # Clean up
        _FRACTION_CACHE.pop(("nba", "moneyline"), None)


class TestBankrollManager:
    """Tests for SportsBankrollManager.get_bet_size."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_edge(self):
        from sports.kelly.bankroll_manager import SportsBankrollManager
        mgr = SportsBankrollManager()
        result = await mgr.get_bet_size(
            fair_prob=0.50, market_price=0.52, sport="nba"
        )
        assert result == 0.0  # edge = 0.50 - 0.52 = -0.02 (negative)

    @pytest.mark.asyncio
    async def test_positive_edge_returns_positive_size(self):
        from sports.kelly.bankroll_manager import SportsBankrollManager
        mgr = SportsBankrollManager()
        with patch("sports.kelly.bankroll_manager.settings") as mock_settings:
            mock_settings.SPORTS_TOTAL_CAPITAL = 10000.0
            mock_settings.SPORTS_MAX_BET_USD = 100.0
            mock_settings.SPORTS_MAX_DAILY_USD = 500.0
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.65, market_price=0.55, sport="nba"
            )
        assert result > 0.0

    @pytest.mark.asyncio
    async def test_per_bet_cap_applied(self):
        from sports.kelly.bankroll_manager import SportsBankrollManager
        mgr = SportsBankrollManager()
        with patch("sports.kelly.bankroll_manager.settings") as mock_settings:
            mock_settings.SPORTS_TOTAL_CAPITAL = 100000.0   # huge capital → huge raw kelly
            mock_settings.SPORTS_MAX_BET_USD = 100.0        # but capped at $100
            mock_settings.SPORTS_MAX_DAILY_USD = 5000.0
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.80, market_price=0.50, sport="nba"
            )
        assert result <= 100.0

    @pytest.mark.asyncio
    async def test_daily_cap_applied(self):
        from sports.kelly.bankroll_manager import SportsBankrollManager

        mock_gw = MagicMock()
        mock_gw._daily_exposure_usd = {"SportsInjuryBot": 490.0}  # $490 already spent of $500
        mgr = SportsBankrollManager(order_gateway=mock_gw)

        with patch("sports.kelly.bankroll_manager.settings") as mock_settings:
            mock_settings.SPORTS_TOTAL_CAPITAL = 10000.0
            mock_settings.SPORTS_MAX_BET_USD = 100.0
            mock_settings.SPORTS_MAX_DAILY_USD = 500.0
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.65, market_price=0.55, sport="nba"
            )
        # Only $10 remaining daily → size should be ≤ $10
        assert result <= 10.0

    @pytest.mark.asyncio
    async def test_daily_cap_exhausted_returns_zero(self):
        from sports.kelly.bankroll_manager import SportsBankrollManager

        mock_gw = MagicMock()
        mock_gw._daily_exposure_usd = {"SportsInjuryBot": 500.0}  # daily cap fully used
        mgr = SportsBankrollManager(order_gateway=mock_gw)

        with patch("sports.kelly.bankroll_manager.settings") as mock_settings:
            mock_settings.SPORTS_TOTAL_CAPITAL = 10000.0
            mock_settings.SPORTS_MAX_BET_USD = 100.0
            mock_settings.SPORTS_MAX_DAILY_USD = 500.0
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.65, market_price=0.55, sport="nba"
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_below_minimum_meaningful_bet_returns_zero(self):
        from sports.kelly.bankroll_manager import SportsBankrollManager
        mgr = SportsBankrollManager()
        with patch("sports.kelly.bankroll_manager.settings") as mock_settings:
            mock_settings.SPORTS_TOTAL_CAPITAL = 10.0          # tiny capital → tiny kelly
            mock_settings.SPORTS_MAX_BET_USD = 100.0
            mock_settings.SPORTS_MAX_DAILY_USD = 500.0
            mock_settings.SPORTS_KELLY_DEFAULT_FRACTION = 0.25
            result = await mgr.get_bet_size(
                fair_prob=0.52, market_price=0.51, sport="nba"
            )
        assert result == 0.0   # kelly_bet < $1.0 → returns 0.0
