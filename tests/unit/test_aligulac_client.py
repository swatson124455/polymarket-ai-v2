"""Tests for Aligulac SC2 API client — player search, ratings, predictions."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from esports.data.aligulac_client import AligulacClient, _cache

_loop = asyncio.new_event_loop()


def run(coro):
    return _loop.run_until_complete(coro)


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


class TestAligulacClient:
    def test_init(self):
        client = AligulacClient(api_key="test_key")
        assert client._api_key == "test_key"

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_search_player_exact_tag(self, mock_get):
        mock_get.return_value = {
            "players": [
                {"id": 485, "tag": "Serral"},
                {"id": 999, "tag": "SerralFan"},
            ]
        }
        client = AligulacClient(api_key="key")
        result = run(client.search_player("Serral"))
        assert result == 485

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_search_player_first_result(self, mock_get):
        mock_get.return_value = {
            "players": [
                {"id": 49, "tag": "Maru"},
            ]
        }
        client = AligulacClient(api_key="key")
        result = run(client.search_player("mar"))
        # No exact tag match, returns first result
        assert result == 49

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_search_player_not_found(self, mock_get):
        mock_get.return_value = {"players": []}
        client = AligulacClient(api_key="key")
        result = run(client.search_player("NonexistentPlayer"))
        assert result is None

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_search_player_empty_name(self, mock_get):
        client = AligulacClient(api_key="key")
        result = run(client.search_player(""))
        assert result is None
        mock_get.assert_not_called()

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_search_player_api_failure(self, mock_get):
        mock_get.return_value = None
        client = AligulacClient(api_key="key")
        result = run(client.search_player("Serral"))
        assert result is None

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_get_player(self, mock_get):
        mock_get.return_value = {
            "id": 485,
            "tag": "Serral",
            "race": "Z",
            "country": "FI",
            "current_rating": {
                "rating": 2800.5,
                "rating_vp": 200.1,
                "rating_vt": 150.3,
                "rating_vz": 100.7,
                "dev": 45.2,
            },
        }
        client = AligulacClient(api_key="key")
        result = run(client.get_player(485))
        assert result is not None
        assert result["tag"] == "Serral"
        assert result["race"] == "Z"
        assert result["rating"] == 2800.5
        assert result["dev"] == 45.2

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_get_player_no_rating(self, mock_get):
        mock_get.return_value = {
            "id": 999,
            "tag": "Newbie",
            "race": "T",
            "country": "US",
            "current_rating": None,
        }
        client = AligulacClient(api_key="key")
        result = run(client.get_player(999))
        assert result is not None
        assert result["rating"] == 0.0

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_predict_match(self, mock_get):
        mock_get.return_value = {
            "proba": 0.72,
            "probb": 0.28,
            "rta": 2800.0,
            "rtb": 2600.0,
        }
        client = AligulacClient(api_key="key")
        result = run(client.predict_match(485, 49, best_of=5))
        assert result is not None
        assert result["prob_a"] == 0.72
        assert result["prob_b"] == 0.28
        assert result["rating_a"] == 2800.0

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_predict_match_even_bo_rounds_up(self, mock_get):
        """Even best_of should be rounded up to next odd number."""
        mock_get.return_value = {
            "proba": 0.5, "probb": 0.5, "rta": 0, "rtb": 0,
        }
        client = AligulacClient(api_key="key")
        run(client.predict_match(1, 2, best_of=4))
        # Should have called with bo=5 (4 rounded up to 5)
        call_args = mock_get.call_args
        assert call_args is not None
        params = call_args.kwargs.get("params") or call_args[1].get("params", {})
        assert params.get("bo") == 5

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_predict_match_api_failure(self, mock_get):
        mock_get.return_value = None
        client = AligulacClient(api_key="key")
        result = run(client.predict_match(485, 49))
        assert result is None

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_get_player_enrichment(self, mock_get):
        call_count = [0]

        async def mock_side_effect(url, params=None, cache_key=None):
            call_count[0] += 1
            if "search" in url:
                q = (params or {}).get("q", "")
                if q.lower() == "serral":
                    return {"players": [{"id": 485, "tag": "Serral"}]}
                if q.lower() == "maru":
                    return {"players": [{"id": 49, "tag": "Maru"}]}
                return {"players": []}
            if "predictmatch" in url:
                return {
                    "proba": 0.65,
                    "probb": 0.35,
                    "rta": 2800.0,
                    "rtb": 2600.0,
                }
            return None

        mock_get.side_effect = mock_side_effect
        client = AligulacClient(api_key="key")
        result = run(client.get_player_enrichment("Serral", "Maru", best_of=5))
        assert result is not None
        assert result["aligulac_prob_a"] == 0.65
        assert result["rating_diff"] == 200.0

    @patch("esports.data.aligulac_client._rate_limited_get")
    def test_get_player_enrichment_player_not_found(self, mock_get):
        async def mock_side_effect(url, params=None, cache_key=None):
            if "search" in url:
                return {"players": []}
            return None

        mock_get.side_effect = mock_side_effect
        client = AligulacClient(api_key="key")
        result = run(client.get_player_enrichment("Unknown", "Player"))
        assert result is None
