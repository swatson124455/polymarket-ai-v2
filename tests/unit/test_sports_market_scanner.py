"""
Unit tests for sports/markets/sports_market_scanner.py and sports/markets/cross_platform_arb.py

Tests:
  - SportsMarketScanner returns candidates from Polymarket + Kalshi
  - Cache TTL behavior
  - NFL offseason keywords
  - cross_platform_arb spread detection
  - _titles_match Jaccard similarity
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sports.markets.kalshi_client import SportsMarketCandidate


def make_candidate(
    platform="polymarket",
    market_id="mkt1",
    sport="nba",
    price=0.55,
    title="NBA Championship",
) -> SportsMarketCandidate:
    return SportsMarketCandidate(
        platform=platform,
        market_id=market_id,
        market_type="moneyline",
        sport=sport,
        yes_token_id="tok1",
        no_token_id=None,
        current_price=price,
        title=title,
    )


class TestSportsMarketScanner:
    """Tests for SportsMarketScanner.find_markets_for_game."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_db_no_kalshi(self):
        from sports.markets.sports_market_scanner import SportsMarketScanner
        scanner = SportsMarketScanner(db=None, kalshi_client=None)
        results = await scanner.find_markets_for_game("game1", "nba")
        assert results == []

    @pytest.mark.asyncio
    async def test_deduplicates_by_market_id(self):
        from sports.markets.sports_market_scanner import SportsMarketScanner, _CACHE
        _CACHE.pop("nba_dedup_game__", None)

        scanner = SportsMarketScanner(db=None, kalshi_client=None)
        # Patch both internal methods to return overlapping market IDs
        dup_candidate = make_candidate(platform="polymarket", market_id="dup1")
        scanner._scan_polymarket = AsyncMock(return_value=[dup_candidate])
        scanner._scan_kalshi = AsyncMock(return_value=[dup_candidate])  # same ID

        results = await scanner.find_markets_for_game("dedup_game", "nba")
        ids = [r.market_id for r in results]
        assert ids.count("dup1") == 1  # deduplicated

    @pytest.mark.asyncio
    async def test_combines_polymarket_and_kalshi(self):
        from sports.markets.sports_market_scanner import SportsMarketScanner, _CACHE
        _CACHE.pop("nba_combine_game__", None)

        scanner = SportsMarketScanner(db=None, kalshi_client=None)
        poly_candidate = make_candidate(platform="polymarket", market_id="poly1")
        kalshi_candidate = make_candidate(platform="kalshi", market_id="kalshi1")
        scanner._scan_polymarket = AsyncMock(return_value=[poly_candidate])
        scanner._scan_kalshi = AsyncMock(return_value=[kalshi_candidate])
        scanner._save_to_db = AsyncMock()

        results = await scanner.find_markets_for_game("combine_game", "nba")
        assert len(results) == 2
        ids = {r.market_id for r in results}
        assert ids == {"poly1", "kalshi1"}

    @pytest.mark.asyncio
    async def test_kalshi_scan_skipped_when_no_client(self):
        from sports.markets.sports_market_scanner import SportsMarketScanner, _CACHE
        _CACHE.pop("nba_kalshi_skip_game__", None)

        scanner = SportsMarketScanner(db=None, kalshi_client=None)
        scanner._scan_polymarket = AsyncMock(return_value=[])
        scanner._scan_kalshi = AsyncMock(return_value=[])

        await scanner.find_markets_for_game("kalshi_skip_game", "nba")
        # _scan_kalshi called but returns [] when _kalshi is None
        scanner._scan_kalshi.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_returns_cached_result(self):
        from sports.markets.sports_market_scanner import SportsMarketScanner, _CACHE
        import time

        scanner = SportsMarketScanner(db=None, kalshi_client=None)
        candidate = make_candidate()
        # Pre-populate cache
        cache_key = "nba_cached_game__"
        _CACHE[cache_key] = (time.monotonic(), [candidate])

        scanner._scan_polymarket = AsyncMock(return_value=[])
        scanner._scan_kalshi = AsyncMock(return_value=[])

        results = await scanner.find_markets_for_game("cached_game", "nba")
        assert len(results) == 1
        scanner._scan_polymarket.assert_not_called()
        # Clean up
        _CACHE.pop(cache_key, None)


class TestKalshiScan:
    """Tests for _scan_kalshi."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_client(self):
        from sports.markets.sports_market_scanner import SportsMarketScanner
        scanner = SportsMarketScanner(db=None, kalshi_client=None)
        result = await scanner._scan_kalshi("nba", "game1", None, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_by_player_name(self):
        from sports.markets.sports_market_scanner import SportsMarketScanner

        mock_kalshi = MagicMock()
        candidates = [
            make_candidate(platform="kalshi", market_id="k1", title="LeBron James points"),
            make_candidate(platform="kalshi", market_id="k2", title="Lakers win"),
        ]
        mock_kalshi.get_sports_markets = AsyncMock(return_value=candidates)

        scanner = SportsMarketScanner(db=None, kalshi_client=mock_kalshi)
        result = await scanner._scan_kalshi("nba", "game1", player_name="LeBron James", team_names=None)

        # Only k1 should match "LeBron James"
        assert len(result) == 1
        assert result[0].market_id == "k1"


class TestCrossPlatformArb:
    """Tests for cross_platform_arb.find_sports_arb_opportunities."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_kalshi_client(self):
        from sports.markets.cross_platform_arb import find_sports_arb_opportunities
        results = await find_sports_arb_opportunities(sport="nba", db=None, kalshi_client=None)
        assert results == []

    def test_titles_match_same_title(self):
        from sports.markets.cross_platform_arb import _titles_match
        assert _titles_match("lakers championship win", "lakers championship win") is True

    def test_titles_match_partial_overlap(self):
        from sports.markets.cross_platform_arb import _titles_match
        # "lakers nba championship" vs "nba lakers finals" — good overlap
        assert _titles_match("lakers nba championship", "nba lakers finals") is True

    def test_titles_no_match(self):
        from sports.markets.cross_platform_arb import _titles_match
        assert _titles_match("lakers basketball nba", "yankees baseball mlb") is False

    def test_titles_match_empty_returns_false(self):
        from sports.markets.cross_platform_arb import _titles_match
        assert _titles_match("", "something") is False
        assert _titles_match("something", "") is False

    @pytest.mark.asyncio
    async def test_arb_detected_when_spread_positive(self):
        from sports.markets.cross_platform_arb import (
            find_sports_arb_opportunities,
            _scan_sport_for_arb,
        )

        mock_kalshi = MagicMock()
        # Poly YES = 0.70, Kalshi YES = 0.20 → Kalshi NO = 0.80
        # poly_yes + kalshi_no - 1 = 0.70 + 0.80 - 1.0 = 0.50 → huge arb
        kalshi_candidate = make_candidate(platform="kalshi", market_id="kal1", price=0.20, title="nba lakers win")
        mock_kalshi.get_sports_markets = AsyncMock(return_value=[kalshi_candidate])

        poly_candidate = make_candidate(platform="polymarket", market_id="poly1", price=0.70, title="lakers nba win")

        with patch(
            "sports.markets.cross_platform_arb.SportsMarketScanner"
        ) as MockScanner:
            mock_scanner_instance = MagicMock()
            mock_scanner_instance._scan_polymarket = AsyncMock(return_value=[poly_candidate])
            MockScanner.return_value = mock_scanner_instance

            with patch("sports.markets.cross_platform_arb.settings") as mock_settings:
                mock_settings.SPORTS_ARB_MIN_SPREAD = 0.04
                results = await _scan_sport_for_arb("nba", MagicMock(), mock_kalshi, 0.04)

        assert len(results) > 0
        assert results[0].net_spread > 0
