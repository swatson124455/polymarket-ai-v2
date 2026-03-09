"""Tests for OpenDota API client — team search, form, hero stats."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from esports.data.opendota_client import OpenDotaClient, _cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear module-level cache between tests."""
    _cache.clear()
    yield
    _cache.clear()


_loop = asyncio.new_event_loop()


def run(coro):
    """Helper to run async coroutines in tests."""
    return _loop.run_until_complete(coro)


class TestOpenDotaClient:
    def test_init(self):
        client = OpenDotaClient()
        assert client is not None

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_search_team_exact_name(self, mock_get):
        mock_get.return_value = [
            {"team_id": 111, "name": "Team Spirit", "tag": "TS"},
            {"team_id": 222, "name": "OG", "tag": "OG"},
            {"team_id": 333, "name": "Tundra Esports", "tag": "Tundra"},
        ]
        client = OpenDotaClient()
        result = run(client.search_team("Team Spirit"))
        assert result == 111

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_search_team_by_tag(self, mock_get):
        mock_get.return_value = [
            {"team_id": 111, "name": "Team Spirit", "tag": "TS"},
            {"team_id": 222, "name": "OG", "tag": "OG"},
        ]
        client = OpenDotaClient()
        result = run(client.search_team("OG"))
        # "OG" matches name exactly before tag
        assert result == 222

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_search_team_substring(self, mock_get):
        mock_get.return_value = [
            {"team_id": 111, "name": "Team Spirit", "tag": "TS"},
            {"team_id": 222, "name": "Virtus.pro", "tag": "VP"},
        ]
        client = OpenDotaClient()
        result = run(client.search_team("spirit"))
        # "spirit" is substring of "team spirit"
        assert result == 111

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_search_team_not_found(self, mock_get):
        mock_get.return_value = [
            {"team_id": 111, "name": "Team Spirit", "tag": "TS"},
        ]
        client = OpenDotaClient()
        result = run(client.search_team("Nonexistent Team"))
        assert result is None

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_search_team_empty_name(self, mock_get):
        mock_get.return_value = [{"team_id": 111, "name": "OG", "tag": "OG"}]
        client = OpenDotaClient()
        result = run(client.search_team(""))
        assert result is None

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_search_team_api_failure(self, mock_get):
        mock_get.return_value = None
        client = OpenDotaClient()
        result = run(client.search_team("OG"))
        assert result is None

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_team_heroes(self, mock_get):
        mock_get.return_value = [
            {"hero_id": 1, "games_played": 50, "wins": 30},
            {"hero_id": 2, "games_played": 40, "wins": 25},
            {"hero_id": 3, "games_played": 0, "wins": 0},  # Should be excluded
        ]
        client = OpenDotaClient()
        result = run(client.get_team_heroes(12345, limit=5))
        assert len(result) == 2  # hero 3 excluded (0 games)
        assert result[0]["hero_id"] == 1
        assert result[0]["win_rate"] == 0.6
        assert result[1]["hero_id"] == 2

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_team_heroes_empty(self, mock_get):
        mock_get.return_value = None
        client = OpenDotaClient()
        result = run(client.get_team_heroes(12345))
        assert result == []

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_hero_matchups(self, mock_get):
        mock_get.return_value = [
            {"hero_id": 10, "games_played": 100, "wins": 60},
            {"hero_id": 20, "games_played": 5, "wins": 3},    # Too few games
            {"hero_id": 30, "games_played": 50, "wins": 25},  # Exactly 0.5 → 0.0
        ]
        client = OpenDotaClient()
        result = run(client.get_hero_matchups(hero_id=1))
        assert 10 in result
        assert result[10] == 0.1  # (60/100) - 0.5
        assert 20 not in result   # < 10 games
        assert 30 in result
        assert result[30] == 0.0  # (25/50) - 0.5

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_team_form(self, mock_get):
        # Simulate 3 matches: W, W, L
        mock_get.return_value = [
            {"match_id": 1, "radiant_win": True, "radiant": True, "duration": 2000},
            {"match_id": 2, "radiant_win": False, "radiant": False, "duration": 2500},
            {"match_id": 3, "radiant_win": True, "radiant": False, "duration": 1800},
        ]
        client = OpenDotaClient()
        result = run(client.get_team_form(12345, last_n=10))
        assert result["matches_played"] == 3
        assert result["win_rate"] == round(2 / 3, 4)
        assert result["form_string"] == "WWL"

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_team_form_no_matches(self, mock_get):
        mock_get.return_value = []
        client = OpenDotaClient()
        result = run(client.get_team_form(12345))
        assert result["win_rate"] == 0.5
        assert result["matches_played"] == 0

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_hero_stats(self, mock_get):
        mock_get.return_value = [
            {
                "id": 1,
                "localized_name": "Anti-Mage",
                "1_pick": 100, "1_win": 55,
                "2_pick": 200, "2_win": 110,
                "3_pick": 0, "3_win": 0,
                "4_pick": 0, "4_win": 0,
                "5_pick": 0, "5_win": 0,
                "6_pick": 0, "6_win": 0,
                "7_pick": 0, "7_win": 0,
                "8_pick": 0, "8_win": 0,
            },
        ]
        client = OpenDotaClient()
        result = run(client.get_hero_stats())
        assert 1 in result
        assert result[1]["localized_name"] == "Anti-Mage"
        assert result[1]["pick_count"] == 300
        assert result[1]["win_rate"] == round(165 / 300, 4)

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_team_enrichment_success(self, mock_get):
        """Test enrichment returns form + hero pool data."""
        call_count = [0]
        async def mock_get_side_effect(path, params=None):
            call_count[0] += 1
            if path == "/teams":
                return [{"team_id": 111, "name": "Team Spirit", "tag": "TS"}]
            if path == "/teams/111/matches":
                return [
                    {"match_id": 1, "radiant_win": True, "radiant": True, "duration": 2000},
                    {"match_id": 2, "radiant_win": True, "radiant": True, "duration": 2200},
                ]
            if path == "/teams/111/heroes":
                return [
                    {"hero_id": 1, "games_played": 10, "wins": 7},
                    {"hero_id": 2, "games_played": 8, "wins": 5},
                    {"hero_id": 3, "games_played": 3, "wins": 2},  # < 5 games
                ]
            return None

        mock_get.side_effect = mock_get_side_effect
        client = OpenDotaClient()
        result = run(client.get_team_enrichment("Team Spirit"))
        assert result is not None
        assert result["form_wr"] == 1.0  # 2/2 wins
        assert result["form_matches"] == 2.0
        assert result["hero_pool_depth"] == 2.0  # heroes 1 & 2 qualify (≥5 games, >45% WR)

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_get_team_enrichment_team_not_found(self, mock_get):
        mock_get.return_value = []
        client = OpenDotaClient()
        result = run(client.get_team_enrichment("Unknown Team"))
        assert result is None

    @patch("esports.data.opendota_client._rate_limited_get")
    def test_search_team_prefers_shortest_substring(self, mock_get):
        """When substring matching, prefer shortest name (most specific)."""
        mock_get.return_value = [
            {"team_id": 111, "name": "Team Spirit Academy", "tag": "TSA"},
            {"team_id": 222, "name": "Team Spirit", "tag": "TS"},
        ]
        client = OpenDotaClient()
        # "team spirit" matches both, should prefer shorter name
        result = run(client.search_team("team spirit"))
        # Exact match should find 222 first (pass 1)
        assert result == 222
