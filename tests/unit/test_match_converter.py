"""Tests for esports_v2/shadow/match_converter.py"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any, Dict

from esports_v2.shadow.match_converter import (
    build_feature_record,
    build_prediction_record,
    esports_match_to_db_row,
    esports_match_to_raw,
)
from esports_v2.data.normalizer import RawMatch
from esports_v2.ratings.trinity import TrinityPrediction


@dataclass
class FakeEsportsMatch:
    """Mimics pandascore_client.EsportsMatch for testing."""
    match_id: int = 12345
    game: str = "cs2"
    tournament: str = "ESL Pro League Season 19"
    team_a: str = "Natus Vincere"
    team_b: str = "FaZe Clan"
    team_a_id: int = 100
    team_b_id: int = 200
    score_a: int = 2
    score_b: int = 1
    best_of: int = 3
    status: str = "finished"
    scheduled_at: str = "2024-06-15T14:00:00Z"
    stream_url: str = ""
    league: str = "ESL"
    raw: Dict[str, Any] = field(default_factory=dict)


class TestEsportsMatchToRaw:
    def test_basic_conversion(self):
        m = FakeEsportsMatch()
        raw = esports_match_to_raw(m)
        assert raw.match_id == "ps_12345"
        assert raw.game == "cs2"
        assert raw.team_a == "Natus Vincere"
        assert raw.team_b == "FaZe Clan"
        assert raw.source == "pandascore"

    def test_winner_from_scores(self):
        m = FakeEsportsMatch(score_a=2, score_b=1)
        raw = esports_match_to_raw(m)
        assert raw.winner == "Natus Vincere"

    def test_winner_team_b(self):
        m = FakeEsportsMatch(score_a=0, score_b=2)
        raw = esports_match_to_raw(m)
        assert raw.winner == "FaZe Clan"

    def test_no_winner_for_upcoming(self):
        m = FakeEsportsMatch(status="not_started", score_a=0, score_b=0)
        raw = esports_match_to_raw(m)
        assert raw.winner is None

    def test_event_tier_detected(self):
        m = FakeEsportsMatch(tournament="ESL Pro League Season 19")
        raw = esports_match_to_raw(m)
        assert raw.event_tier == "a_tier"

    def test_lan_detected(self):
        m = FakeEsportsMatch(tournament="PGL CS2 Major Copenhagen")
        raw = esports_match_to_raw(m)
        assert raw.is_lan is True

    def test_date_preserved(self):
        m = FakeEsportsMatch(scheduled_at="2024-06-15T14:00:00Z")
        raw = esports_match_to_raw(m)
        assert raw.match_date == "2024-06-15T14:00:00Z"


class TestEsportsMatchToDbRow:
    def test_basic(self):
        m = FakeEsportsMatch()
        row = esports_match_to_db_row(m)
        assert row["match_id"] == "ps_12345"
        assert row["game"] == "cs2"
        assert row["team_a"] == "Natus Vincere"
        assert row["source"] == "pandascore_live"
        assert row["winner"] == "Natus Vincere"


class TestBuildFeatureRecord:
    def test_has_trinity_features(self):
        raw = RawMatch(
            match_id="ps_1", game="cs2",
            team_a="Team A", team_b="Team B",
            match_date="2024-01-01",
        )
        pred = TrinityPrediction(
            team_a="Team A", team_b="Team B",
            p_elo=0.6, p_glicko=0.58, p_openskill=0.62,
            trinity_spread=0.04, trinity_mean=0.60,
        )
        record = build_feature_record(raw, pred)
        assert record["p_elo"] == 0.6
        assert record["p_glicko"] == 0.58
        assert record["p_openskill"] == 0.62
        assert record["trinity_spread"] == 0.04
        assert record["trinity_mean"] == 0.60
        assert record["game"] == "cs2"

    def test_no_actual_key(self):
        raw = RawMatch(match_id="ps_1", game="lol", team_a="A", team_b="B")
        pred = TrinityPrediction(
            team_a="A", team_b="B",
            p_elo=0.5, p_glicko=0.5, p_openskill=0.5,
            trinity_spread=0.0, trinity_mean=0.5,
        )
        record = build_feature_record(raw, pred)
        assert "actual" not in record


class TestBuildPredictionRecord:
    def test_team_a_predicted(self):
        result = {"p_model": 0.65, "p_raw": 0.63, "conformal_set": [1],
                  "is_singleton": True, "kelly_fraction": 0.04, "stake": 80}
        pred = build_prediction_record(
            "ps_1", "cs2", "NAVI", "FaZe", result, market_price=0.55,
        )
        assert pred["predicted_winner"] == "NAVI"
        assert pred["p_model"] == 0.65
        assert pred["edge"] == pytest.approx(0.10)
        assert pred["actual_winner"] is None
        assert pred["correct"] is None
        assert pred["mode"] == "shadow"
        assert pred["model_version"] == "v2-trinity"

    def test_team_b_predicted(self):
        result = {"p_model": 0.35, "p_raw": 0.33, "conformal_set": [0],
                  "is_singleton": True, "kelly_fraction": 0.03, "stake": 60}
        pred = build_prediction_record(
            "ps_2", "lol", "T1", "Gen.G", result, market_price=0.55,
        )
        assert pred["predicted_winner"] == "Gen.G"

    def test_no_market_price(self):
        result = {"p_model": 0.7, "p_raw": 0.68, "conformal_set": [1],
                  "is_singleton": True, "kelly_fraction": 0.05, "stake": 100}
        pred = build_prediction_record(
            "ps_3", "cs2", "A", "B", result, market_price=None,
        )
        assert pred["market_price"] is None
        assert pred["edge"] is None

    def test_conformal_set_stringified(self):
        result = {"p_model": 0.6, "p_raw": 0.58, "conformal_set": [0, 1],
                  "is_singleton": False, "kelly_fraction": 0.0, "stake": 0}
        pred = build_prediction_record(
            "ps_4", "lol", "A", "B", result, market_price=0.5,
        )
        assert pred["conformal_set"] == ["0", "1"]
        assert pred["is_singleton"] is False

    def test_default_mode_is_shadow(self):
        """Item 4: default mode preserved as 'shadow' for back-compat with
        the 4 test sites above + shadow_report.py + any external scripts."""
        result = {"p_model": 0.6, "is_singleton": True, "kelly_fraction": 0.0, "stake": 0}
        pred = build_prediction_record(
            "ps_5", "cs2", "A", "B", result, market_price=0.5,
        )
        assert pred["mode"] == "shadow"

    def test_explicit_mode_propagates_to_record(self):
        """Item 4: writer accepts a mode parameter so EsportsBotV2 can stamp
        live-mode predictions with mode='live' once dry_run=False, letting
        eval queries split the boundary cleanly."""
        result = {"p_model": 0.7, "is_singleton": True, "kelly_fraction": 0.05, "stake": 100}
        pred = build_prediction_record(
            "ps_6", "lol", "A", "B", result, market_price=0.5, mode="live",
        )
        assert pred["mode"] == "live"
