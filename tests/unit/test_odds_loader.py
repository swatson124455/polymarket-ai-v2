"""Tests for esports_v2/data/odds_loader.py"""
from __future__ import annotations

import json
import pytest

from esports_v2.data.odds_loader import OddsPapiLoader, make_match_key


# ── make_match_key ──────────────────────────────────────────────────

class TestMakeMatchKey:
    def test_basic(self):
        key = make_match_key("Team Alpha", "Team Beta", "2024-06-15T14:00:00Z")
        assert key == "team alpha||team beta||2024-06-15"

    def test_alphabetical_sort(self):
        """Same key regardless of team order."""
        k1 = make_match_key("Natus Vincere", "FaZe Clan", "2024-06-15T14:00:00Z")
        k2 = make_match_key("FaZe Clan", "Natus Vincere", "2024-06-15T14:00:00Z")
        assert k1 == k2

    def test_strips_whitespace(self):
        key = make_match_key("  Team A  ", "Team B", "2024-01-01")
        assert key == "team a||team b||2024-01-01"

    def test_date_truncated_to_day(self):
        key = make_match_key("A", "B", "2024-06-15T14:30:00Z")
        assert key.endswith("2024-06-15")

    def test_short_date(self):
        key = make_match_key("A", "B", "2024-06-15")
        assert key.endswith("2024-06-15")

    def test_empty_date(self):
        key = make_match_key("A", "B", "")
        assert key == "a||b||"

    def test_none_date(self):
        key = make_match_key("A", "B", None)
        assert "||" in key


# ── Odds extraction ─────────────────────────────────────────────────

class TestExtractClosingPrice:
    def test_single_player_entry(self):
        outcome = {
            "players": {
                "0": {"createdAt": "2024-06-15T13:00:00Z", "price": 1.85}
            }
        }
        price = OddsPapiLoader._extract_closing_price(outcome)
        assert price == 1.85

    def test_multiple_entries_picks_latest(self):
        outcome = {
            "players": {
                "0": {"createdAt": "2024-06-15T10:00:00Z", "price": 2.00},
                "1": {"createdAt": "2024-06-15T13:00:00Z", "price": 1.85},
                "2": {"createdAt": "2024-06-15T12:00:00Z", "price": 1.90},
            }
        }
        price = OddsPapiLoader._extract_closing_price(outcome)
        assert price == 1.85  # latest timestamp

    def test_direct_price_field(self):
        outcome = {"price": 2.10}
        price = OddsPapiLoader._extract_closing_price(outcome)
        assert price == 2.10

    def test_none_input(self):
        assert OddsPapiLoader._extract_closing_price(None) is None

    def test_empty_players(self):
        assert OddsPapiLoader._extract_closing_price({"players": {}}) is None


class TestGetBookmakerOutcomeId:
    def test_from_players(self):
        outcome = {
            "players": {
                "0": {"bookmakerOutcomeId": "home", "price": 1.85}
            }
        }
        assert OddsPapiLoader._get_bookmaker_outcome_id(outcome) == "home"

    def test_from_top_level(self):
        outcome = {"bookmakerOutcomeId": "away"}
        assert OddsPapiLoader._get_bookmaker_outcome_id(outcome) == "away"

    def test_none(self):
        assert OddsPapiLoader._get_bookmaker_outcome_id(None) is None


class TestFetchPinnacleOdds:
    def setup_method(self):
        self.loader = OddsPapiLoader.__new__(OddsPapiLoader)
        self.loader._api_key = "test"
        self.loader._session = None
        self.loader._request_count = 0

    def test_parses_standard_response(self):
        response = {
            "markets": {
                "101": {
                    "outcomes": {
                        "101": {
                            "players": {
                                "0": {
                                    "createdAt": "2024-06-15T13:50:00Z",
                                    "price": 1.85,
                                    "bookmakerOutcomeId": "home",
                                }
                            }
                        },
                        "103": {
                            "players": {
                                "0": {
                                    "createdAt": "2024-06-15T13:50:00Z",
                                    "price": 2.05,
                                    "bookmakerOutcomeId": "away",
                                }
                            }
                        },
                    }
                }
            }
        }
        # Mock _get to return our response
        self.loader._get = lambda path, params=None: response
        result = self.loader._fetch_pinnacle_odds("test_fixture")
        assert result == (1.85, 2.05)

    def test_returns_none_when_no_data(self):
        self.loader._get = lambda path, params=None: None
        result = self.loader._fetch_pinnacle_odds("test_fixture")
        assert result is None

    def test_returns_none_when_odds_below_1(self):
        response = {
            "markets": {
                "101": {
                    "outcomes": {
                        "101": {"players": {"0": {"createdAt": "2024-06-15T13:50:00Z", "price": 0.95, "bookmakerOutcomeId": "home"}}},
                        "103": {"players": {"0": {"createdAt": "2024-06-15T13:50:00Z", "price": 1.05, "bookmakerOutcomeId": "away"}}},
                    }
                }
            }
        }
        self.loader._get = lambda path, params=None: response
        result = self.loader._fetch_pinnacle_odds("test_fixture")
        assert result is None


# ── Save/load roundtrip ─────────────────────────────────────────────

class TestSaveLoadOdds:
    def test_roundtrip(self, tmp_path):
        odds = {
            "faze clan||natus vincere||2024-06-15": (1.85, 2.05),
            "g2 esports||team vitality||2024-06-16": (2.10, 1.75),
        }
        filepath = tmp_path / "odds.json"
        loader = OddsPapiLoader.__new__(OddsPapiLoader)
        loader.save_odds(odds, filepath)

        loaded = OddsPapiLoader.load_odds(filepath)
        assert loaded["faze clan||natus vincere||2024-06-15"] == (1.85, 2.05)
        assert loaded["g2 esports||team vitality||2024-06-16"] == (2.10, 1.75)

    def test_load_nonexistent(self, tmp_path):
        loaded = OddsPapiLoader.load_odds(tmp_path / "missing.json")
        assert loaded == {}


# ── Sport discovery ─────────────────────────────────────────────────

class TestDiscoverSports:
    def test_finds_cs2_and_lol(self):
        loader = OddsPapiLoader.__new__(OddsPapiLoader)
        loader._api_key = "test"
        loader._session = None
        loader._request_count = 0
        loader._sport_cache = {}

        fake_sports = [
            {"sportId": 17, "sportName": "Counter-Strike", "sportSlug": "counter-strike"},
            {"sportId": 22, "sportName": "League of Legends", "sportSlug": "league-of-legends"},
            {"sportId": 1, "sportName": "Soccer", "sportSlug": "soccer"},
        ]
        loader._get = lambda path, params=None: fake_sports

        mapping = loader.discover_sports()
        assert mapping["cs2"] == 17
        assert mapping["lol"] == 22
        assert "soccer" not in mapping

    def test_handles_empty_response(self):
        loader = OddsPapiLoader.__new__(OddsPapiLoader)
        loader._api_key = "test"
        loader._session = None
        loader._request_count = 0
        loader._sport_cache = {}
        loader._get = lambda path, params=None: []

        mapping = loader.discover_sports()
        assert mapping == {}
