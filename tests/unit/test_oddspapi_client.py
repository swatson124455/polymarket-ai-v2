"""Tests for OddsPapi esports odds client — fixtures, closing lines, CLV."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from esports.data.oddspapi_client import OddsPapiClient, _cache

_loop = asyncio.new_event_loop()


def run(coro):
    return _loop.run_until_complete(coro)


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


class TestOddsPapiClient:
    def test_init(self):
        client = OddsPapiClient(api_key="test_key")
        assert client._api_key == "test_key"

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_get_fixtures(self, mock_get):
        mock_get.return_value = [
            {
                "id": "id123",
                "participants": [
                    {"name": "Team Spirit"},
                    {"name": "OG"},
                ],
                "startTime": "2026-03-07T14:00:00Z",
                "status": "settled",
            },
        ]
        client = OddsPapiClient(api_key="key")
        result = run(client.get_fixtures("dota2", days_back=3))
        assert len(result) == 1
        assert result[0]["fixture_id"] == "id123"
        assert result[0]["home"] == "Team Spirit"
        assert result[0]["away"] == "OG"

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_get_fixtures_unknown_game(self, mock_get):
        client = OddsPapiClient(api_key="key")
        result = run(client.get_fixtures("unknown_game"))
        assert result == []
        mock_get.assert_not_called()

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_get_fixtures_api_failure(self, mock_get):
        mock_get.return_value = None
        client = OddsPapiClient(api_key="key")
        result = run(client.get_fixtures("cs2"))
        assert result == []

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_get_pinnacle_closing_line(self, mock_get):
        mock_get.return_value = {
            "fixtureId": "id123",
            "bookmakers": {
                "pinnacle": {
                    "markets": {
                        "171": {
                            "outcomes": {
                                "1": {
                                    "players": {
                                        "0": [
                                            {"price": 2.0, "createdAt": "2026-03-07T10:00:00Z"},
                                            {"price": 1.85, "createdAt": "2026-03-07T13:00:00Z"},
                                        ]
                                    }
                                },
                                "2": {
                                    "players": {
                                        "0": [
                                            {"price": 1.8, "createdAt": "2026-03-07T10:00:00Z"},
                                            {"price": 2.05, "createdAt": "2026-03-07T13:00:00Z"},
                                        ]
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
        client = OddsPapiClient(api_key="key")
        result = run(client.get_pinnacle_closing_line("id123"))
        assert result is not None
        # Home closing: 1.85 → implied prob: 1/1.85 = 0.5405
        # Away closing: 2.05 → implied prob: 1/2.05 = 0.4878
        # Normalized: 0.5405/(0.5405+0.4878) ≈ 0.5257
        assert 0.52 < result["closing_prob_home"] < 0.53
        assert 0.47 < result["closing_prob_away"] < 0.48
        assert result["closing_odds_home"] == 1.85
        assert result["closing_odds_away"] == 2.05

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_get_pinnacle_closing_line_no_data(self, mock_get):
        mock_get.return_value = None
        client = OddsPapiClient(api_key="key")
        result = run(client.get_pinnacle_closing_line("id123"))
        assert result is None

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_get_pinnacle_closing_line_empty_markets(self, mock_get):
        mock_get.return_value = {
            "bookmakers": {"pinnacle": {"markets": {}}},
        }
        client = OddsPapiClient(api_key="key")
        result = run(client.get_pinnacle_closing_line("id123"))
        assert result is None

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_compute_clv_positive(self, mock_get):
        """Positive CLV = our prediction was sharper than Pinnacle closing."""
        mock_get.return_value = {
            "bookmakers": {
                "pinnacle": {
                    "markets": {
                        "171": {
                            "outcomes": {
                                "1": {"players": {"0": [{"price": 1.80}]}},
                                "2": {"players": {"0": [{"price": 2.10}]}},
                            }
                        }
                    }
                }
            },
        }
        client = OddsPapiClient(api_key="key")
        # Closing: home=1.80 → prob≈0.5385 (normalized), away=2.10 → prob≈0.4615
        # Our prob for home: 0.60 → CLV = 0.60 - 0.5385 ≈ +0.06
        result = run(client.compute_clv("id123", our_prob=0.60, side="home"))
        assert result is not None
        assert result["clv"] > 0  # Positive CLV
        assert result["our_prob"] == 0.60

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_compute_clv_negative(self, mock_get):
        """Negative CLV = Pinnacle was sharper than our prediction."""
        mock_get.return_value = {
            "bookmakers": {
                "pinnacle": {
                    "markets": {
                        "171": {
                            "outcomes": {
                                "1": {"players": {"0": [{"price": 1.50}]}},
                                "2": {"players": {"0": [{"price": 2.80}]}},
                            }
                        }
                    }
                }
            },
        }
        client = OddsPapiClient(api_key="key")
        # Closing: home=1.50 → prob≈0.6510 (normalized)
        # Our prob for home: 0.55 → CLV = 0.55 - 0.6510 ≈ -0.10
        result = run(client.compute_clv("id123", our_prob=0.55, side="home"))
        assert result is not None
        assert result["clv"] < 0  # Negative CLV

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_compute_clv_away_side(self, mock_get):
        mock_get.return_value = {
            "bookmakers": {
                "pinnacle": {
                    "markets": {
                        "171": {
                            "outcomes": {
                                "1": {"players": {"0": [{"price": 2.00}]}},
                                "2": {"players": {"0": [{"price": 1.90}]}},
                            }
                        }
                    }
                }
            },
        }
        client = OddsPapiClient(api_key="key")
        result = run(client.compute_clv("id123", our_prob=0.60, side="away"))
        assert result is not None
        # closing_prob_away should be used
        assert result["closing_odds"] == 1.90

    @patch("esports.data.oddspapi_client._rate_limited_get")
    def test_compute_clv_no_closing_data(self, mock_get):
        mock_get.return_value = None
        client = OddsPapiClient(api_key="key")
        result = run(client.compute_clv("id123", our_prob=0.60))
        assert result is None

    def test_extract_prices_empty(self):
        result = OddsPapiClient._extract_prices({})
        assert result == []

    def test_extract_prices_valid(self):
        outcome = {
            "players": {
                "0": [
                    {"price": 1.85},
                    {"price": 1.90},
                    {"price": 1.82},
                ]
            }
        }
        result = OddsPapiClient._extract_prices(outcome)
        assert result == [1.85, 1.90, 1.82]
