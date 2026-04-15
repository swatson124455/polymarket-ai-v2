"""Tests for esports_v2/data/pandascore_loader.py"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from esports_v2.data.pandascore_loader import PandaScoreLoader, _classify_tier, _is_lan_event


# ── Tier classification ──────────────��───────────────────────────────

class TestClassifyTier:
    def test_s_tier_major(self):
        assert _classify_tier("PGL CS2 Major Copenhagen 2024") == "s_tier"

    def test_a_tier_esl(self):
        assert _classify_tier("ESL Pro League Season 19") == "a_tier"

    def test_a_tier_iem(self):
        assert _classify_tier("IEM Dallas 2024") == "a_tier"

    def test_b_tier_dreamhack(self):
        assert _classify_tier("DreamHack Open Summer 2024") == "b_tier"

    def test_c_tier_unknown(self):
        assert _classify_tier("Random Online Cup") == "c_tier"

    def test_empty_string(self):
        assert _classify_tier("") == "c_tier"


class TestIsLanEvent:
    def test_major_is_lan(self):
        assert _is_lan_event("PGL CS2 Major") is True

    def test_iem_is_lan(self):
        assert _is_lan_event("IEM Katowice 2024") is True

    def test_online_cup_not_lan(self):
        assert _is_lan_event("Online Regional Qualifier") is False

    def test_empty_not_lan(self):
        assert _is_lan_event("") is False


# ── PandaScore match parsing ───────────────────────��─────────────────

def _make_ps_match(
    match_id=12345,
    team_a="Natus Vincere",
    team_b="FaZe Clan",
    score_a=2,
    score_b=1,
    status="finished",
    scheduled_at="2024-06-15T14:00:00Z",
    tournament_name="IEM Cologne 2024",
    league_name="Intel Extreme Masters",
    best_of=3,
    winner_name=None,
    roster_a=None,
    roster_b=None,
):
    """Build a realistic PandaScore match JSON."""
    opponents = [
        {
            "opponent": {
                "id": 100,
                "name": team_a,
                "players": [{"name": p} for p in (roster_a or [])],
            }
        },
        {
            "opponent": {
                "id": 200,
                "name": team_b,
                "players": [{"name": p} for p in (roster_b or [])],
            }
        },
    ]
    results = [{"score": score_a}, {"score": score_b}]
    match = {
        "id": match_id,
        "opponents": opponents,
        "results": results,
        "status": status,
        "scheduled_at": scheduled_at,
        "number_of_games": best_of,
        "tournament": {"name": tournament_name},
        "league": {"name": league_name},
        "videogame": {"slug": "csgo"},
    }
    if winner_name:
        match["winner"] = {"name": winner_name}
    return match


class TestParseMatch:
    def setup_method(self):
        self.loader = PandaScoreLoader.__new__(PandaScoreLoader)
        self.loader._session = None
        self.loader._request_count = 0

    def test_basic_cs2_match(self):
        raw = _make_ps_match()
        m = self.loader._parse_match(raw, "cs2")
        assert m is not None
        assert m.match_id == "ps_12345"
        assert m.game == "cs2"
        assert m.team_a == "Natus Vincere"
        assert m.team_b == "FaZe Clan"
        assert m.score_a == 2
        assert m.score_b == 1
        assert m.best_of == 3

    def test_winner_from_score(self):
        raw = _make_ps_match(score_a=2, score_b=0)
        m = self.loader._parse_match(raw, "cs2")
        assert m.winner == "Natus Vincere"

    def test_winner_from_winner_field(self):
        raw = _make_ps_match(winner_name="FaZe Clan", score_a=1, score_b=2)
        m = self.loader._parse_match(raw, "cs2")
        assert m.winner == "FaZe Clan"

    def test_event_tier_detected(self):
        raw = _make_ps_match(tournament_name="ESL Pro League Season 19")
        m = self.loader._parse_match(raw, "cs2")
        assert m.event_tier == "a_tier"

    def test_lan_detected(self):
        raw = _make_ps_match(tournament_name="PGL CS2 Major Copenhagen")
        m = self.loader._parse_match(raw, "cs2")
        assert m.is_lan is True

    def test_roster_extracted(self):
        raw = _make_ps_match(
            roster_a=["s1mple", "electronic", "b1t", "Aleksib", "jL"],
            roster_b=["rain", "karrigan", "broky", "ropz", "frozen"],
        )
        m = self.loader._parse_match(raw, "cs2")
        assert m.roster_a == ["s1mple", "electronic", "b1t", "Aleksib", "jL"]
        assert m.roster_b == ["rain", "karrigan", "broky", "ropz", "frozen"]

    def test_no_opponents_returns_none(self):
        raw = {"id": 1, "opponents": [], "results": []}
        m = self.loader._parse_match(raw, "cs2")
        assert m is None

    def test_no_id_returns_none(self):
        raw = _make_ps_match()
        del raw["id"]
        m = self.loader._parse_match(raw, "cs2")
        assert m is None

    def test_date_preserved(self):
        raw = _make_ps_match(scheduled_at="2024-06-15T14:00:00Z")
        m = self.loader._parse_match(raw, "cs2")
        assert m.match_date == "2024-06-15T14:00:00Z"

    def test_source_is_pandascore(self):
        raw = _make_ps_match()
        m = self.loader._parse_match(raw, "cs2")
        assert m.source == "pandascore"


# ── Save/load JSON roundtrip ─────────────────────────────────────────

class TestSaveJson:
    def test_save_and_reload(self, tmp_path):
        loader = PandaScoreLoader.__new__(PandaScoreLoader)
        loader._session = None
        loader._request_count = 0

        from esports_v2.data.normalizer import RawMatch
        matches = [
            RawMatch(
                match_id="ps_1",
                game="cs2",
                team_a="Team A",
                team_b="Team B",
                winner="Team A",
                score_a=2,
                score_b=1,
                best_of=3,
                match_date="2024-01-15T12:00:00Z",
                source="pandascore",
                event_name="Test Event",
                event_tier="b_tier",
            )
        ]

        out_path = tmp_path / "test.json"
        loader.save_json(matches, out_path)

        # Verify saved JSON is GRID-compatible
        with open(out_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["teams"][0]["name"] == "Team A"
        assert data[0]["teams"][1]["name"] == "Team B"
        assert data[0]["winner"] == "Team A"
        assert data[0]["score1"] == 2
        assert data[0]["score2"] == 1

        # Verify GridLoader can read it
        from esports_v2.data.grid_loader import GridLoader
        grid_loader = GridLoader()
        loaded = grid_loader.load_json(out_path)
        assert len(loaded) == 1
        assert loaded[0].team_a == "Team A"
        assert loaded[0].game == "cs2"
