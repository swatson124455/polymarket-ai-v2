"""Tests for Ballchasing Rocket League replay stats client."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from esports.data.ballchasing_client import BallchasingClient, _cache

_loop = asyncio.new_event_loop()


def run(coro):
    return _loop.run_until_complete(coro)


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


class TestBallchasingClient:
    def test_init(self):
        client = BallchasingClient(api_key="test_key")
        assert client._api_key == "test_key"

    @patch("esports.data.ballchasing_client._rate_limited_get")
    def test_search_replays(self, mock_get):
        mock_get.return_value = {
            "list": [
                {
                    "id": "abc123",
                    "date": "2026-03-07T14:00:00Z",
                    "blue": {"name": "NRG", "goals": 3},
                    "orange": {"name": "G2", "goals": 2},
                    "duration": 300,
                    "map_code": "stadium_p",
                },
            ],
        }
        client = BallchasingClient(api_key="key")
        result = run(client.search_replays(player_name="Squishy"))
        assert len(result) == 1
        assert result[0]["id"] == "abc123"
        assert result[0]["blue_team"] == "NRG"
        assert result[0]["blue_goals"] == 3

    @patch("esports.data.ballchasing_client._rate_limited_get")
    def test_search_replays_empty(self, mock_get):
        mock_get.return_value = {"list": []}
        client = BallchasingClient(api_key="key")
        result = run(client.search_replays())
        assert result == []

    @patch("esports.data.ballchasing_client._rate_limited_get")
    def test_search_replays_api_failure(self, mock_get):
        mock_get.return_value = None
        client = BallchasingClient(api_key="key")
        result = run(client.search_replays())
        assert result == []

    @patch("esports.data.ballchasing_client._rate_limited_get")
    def test_get_replay_stats(self, mock_get):
        mock_get.return_value = {
            "id": "abc123",
            "date": "2026-03-07T14:00:00Z",
            "duration": 305,
            "blue": {
                "name": "NRG",
                "stats": {
                    "core": {"goals": 3, "shots": 10, "saves": 5, "assists": 2},
                },
                "players": [
                    {"stats": {"boost": {"bpm": 180, "amount_stolen": 50}}},
                    {"stats": {"boost": {"bpm": 200, "amount_stolen": 60}}},
                    {"stats": {"boost": {"bpm": 160, "amount_stolen": 40}}},
                ],
            },
            "orange": {
                "name": "G2",
                "stats": {
                    "core": {"goals": 2, "shots": 8, "saves": 3, "assists": 1},
                },
                "players": [
                    {"stats": {"boost": {"bpm": 170, "amount_stolen": 45}}},
                    {"stats": {"boost": {"bpm": 190, "amount_stolen": 55}}},
                    {"stats": {"boost": {"bpm": 150, "amount_stolen": 35}}},
                ],
            },
        }
        client = BallchasingClient(api_key="key")
        result = run(client.get_replay_stats("abc123"))
        assert result is not None
        assert result["blue"]["name"] == "NRG"
        assert result["blue"]["goals"] == 3
        assert result["blue"]["player_count"] == 3
        # avg_bpm: (180+200+160)/3 = 180.0
        assert result["blue"]["avg_bpm"] == 180.0
        assert result["orange"]["goals"] == 2

    @patch("esports.data.ballchasing_client._rate_limited_get")
    def test_get_replay_stats_api_failure(self, mock_get):
        mock_get.return_value = None
        client = BallchasingClient(api_key="key")
        result = run(client.get_replay_stats("missing"))
        assert result is None

    @patch("esports.data.ballchasing_client._rate_limited_get")
    def test_get_team_aggregate_stats(self, mock_get):
        """Test aggregate stats across multiple replays."""
        call_count = [0]

        async def mock_side_effect(path, api_key, params=None, cache_key=None):
            call_count[0] += 1
            if path == "/replays":
                return {
                    "list": [
                        {"id": "r1", "date": "2026-03-07", "blue": {"name": "NRG", "goals": 3},
                         "orange": {"name": "G2", "goals": 2}, "duration": 300, "map_code": "std"},
                        {"id": "r2", "date": "2026-03-06", "blue": {"name": "Faze", "goals": 1},
                         "orange": {"name": "NRG", "goals": 4}, "duration": 280, "map_code": "std"},
                    ]
                }
            if path == "/replays/r1":
                return {
                    "id": "r1", "date": "2026-03-07", "duration": 300,
                    "blue": {
                        "name": "NRG",
                        "stats": {"core": {"goals": 3, "shots": 12, "saves": 4, "assists": 2}},
                        "players": [{"stats": {"boost": {"bpm": 180, "amount_stolen": 50}}}],
                    },
                    "orange": {
                        "name": "G2",
                        "stats": {"core": {"goals": 2, "shots": 8, "saves": 3, "assists": 1}},
                        "players": [{"stats": {"boost": {"bpm": 170, "amount_stolen": 45}}}],
                    },
                }
            if path == "/replays/r2":
                return {
                    "id": "r2", "date": "2026-03-06", "duration": 280,
                    "blue": {
                        "name": "Faze",
                        "stats": {"core": {"goals": 1, "shots": 6, "saves": 5, "assists": 0}},
                        "players": [{"stats": {"boost": {"bpm": 160, "amount_stolen": 30}}}],
                    },
                    "orange": {
                        "name": "NRG",
                        "stats": {"core": {"goals": 4, "shots": 14, "saves": 2, "assists": 3}},
                        "players": [{"stats": {"boost": {"bpm": 200, "amount_stolen": 70}}}],
                    },
                }
            return None

        mock_get.side_effect = mock_side_effect
        client = BallchasingClient(api_key="key")
        result = run(client.get_team_aggregate_stats("NRG", days_back=30))
        assert result is not None
        assert result["games_found"] == 2.0
        # Game 1 (blue): 3 goals, 12 shots, 4 saves, bpm=180
        # Game 2 (orange): 4 goals, 14 shots, 2 saves, bpm=200
        # Totals: 7 goals, 26 shots, 6 saves
        assert result["goals_per_game"] == 3.5   # 7/2
        assert result["shots_per_game"] == 13.0   # 26/2
        assert result["saves_per_game"] == 3.0    # 6/2
        assert result["avg_bpm"] == 190.0          # (180+200)/2

    @patch("esports.data.ballchasing_client._rate_limited_get")
    def test_get_team_aggregate_stats_not_found(self, mock_get):
        mock_get.return_value = {"list": []}
        client = BallchasingClient(api_key="key")
        result = run(client.get_team_aggregate_stats("Unknown Team"))
        assert result is None

    def test_extract_team_stats_empty(self):
        result = BallchasingClient._extract_team_stats({})
        assert result["name"] == ""
        assert result["goals"] == 0
        assert result["player_count"] == 0

    def test_extract_team_stats_with_data(self):
        data = {
            "name": "NRG",
            "stats": {"core": {"goals": 5, "shots": 15, "saves": 7, "assists": 3}},
            "players": [
                {"stats": {"boost": {"bpm": 180, "amount_stolen": 50}}},
                {"stats": {"boost": {"bpm": 200, "amount_stolen": 60}}},
            ],
        }
        result = BallchasingClient._extract_team_stats(data)
        assert result["name"] == "NRG"
        assert result["goals"] == 5
        assert result["player_count"] == 2
        assert result["avg_bpm"] == 190.0
