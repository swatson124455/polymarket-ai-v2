"""Tests for match data normalizer."""
import pytest

from esports_v2.data.normalizer import (
    RawMatch,
    normalize_team_name,
    raw_to_match_result,
    raw_to_db_row,
)


class TestNormalizeTeamName:
    def test_strips_whitespace(self):
        assert normalize_team_name("  NAVI  ") == "NAVI"

    def test_empty_string(self):
        assert normalize_team_name("") == ""


class TestRawToMatchResult:
    def test_team_a_wins(self):
        raw = RawMatch(
            match_id="m1", game="cs2",
            team_a="NAVI", team_b="FaZe", winner="NAVI",
        )
        mr = raw_to_match_result(raw)
        assert mr.winner == "a"

    def test_team_b_wins(self):
        raw = RawMatch(
            match_id="m1", game="cs2",
            team_a="NAVI", team_b="FaZe", winner="FaZe",
        )
        mr = raw_to_match_result(raw)
        assert mr.winner == "b"

    def test_rosters_passed_through(self):
        raw = RawMatch(
            match_id="m1", game="lol",
            team_a="T1", team_b="Gen.G", winner="T1",
            roster_a=["Faker", "Zeus"],
            roster_b=["Chovy", "Canyon"],
        )
        mr = raw_to_match_result(raw)
        assert mr.roster_a == ["Faker", "Zeus"]
        assert mr.roster_b == ["Chovy", "Canyon"]

    def test_whitespace_in_winner(self):
        raw = RawMatch(
            match_id="m1", game="cs2",
            team_a="NAVI", team_b=" FaZe ", winner=" FaZe ",
        )
        mr = raw_to_match_result(raw)
        assert mr.winner == "b"


class TestRawToDbRow:
    def test_all_fields_present(self):
        raw = RawMatch(
            match_id="m1", game="cs2",
            team_a="NAVI", team_b="FaZe", winner="NAVI",
            score_a=2, score_b=1, best_of=3,
            event_name="IEM Katowice", event_tier="s_tier",
            is_lan=True, source="grid",
            match_date="2025-02-15",
        )
        row = raw_to_db_row(raw)
        assert row["match_id"] == "m1"
        assert row["game"] == "cs2"
        assert row["team_a"] == "NAVI"
        assert row["winner"] == "NAVI"
        assert row["score_a"] == 2
        assert row["is_lan"] is True
        assert row["source"] == "grid"
