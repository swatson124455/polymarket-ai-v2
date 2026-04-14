"""Tests for GRID/HLTV CS2 data loaders."""
import csv
import json
import tempfile
from pathlib import Path

import pytest

from esports_v2.data.grid_loader import GridLoader, HLTVResultsLoader, _classify_tier


class TestClassifyTier:
    def test_major_is_s_tier(self):
        assert _classify_tier("PGL CS2 Major Copenhagen 2024") == "s_tier"

    def test_esl_pro_league_is_a_tier(self):
        assert _classify_tier("ESL Pro League Season 19") == "a_tier"

    def test_unknown_is_c_tier(self):
        assert _classify_tier("Random Online Cup") == "c_tier"

    def test_empty_is_c_tier(self):
        assert _classify_tier("") == "c_tier"


class TestGridLoader:

    def _make_grid_match(
        self,
        match_id: str = "12345",
        team_a: str = "NAVI",
        team_b: str = "FaZe",
        winner: str = "NAVI",
        score_a: int = 2,
        score_b: int = 1,
        event_name: str = "IEM Katowice 2025",
        date: str = "2025-02-15T14:00:00Z",
        best_of: int = 3,
        roster_a: list = None,
        roster_b: list = None,
    ) -> dict:
        return {
            "id": match_id,
            "teams": [
                {
                    "name": team_a,
                    "players": [{"nickname": p} for p in (roster_a or ["s1mple", "b1t", "electroNic", "Perfecto", "npl"])],
                },
                {
                    "name": team_b,
                    "players": [{"nickname": p} for p in (roster_b or ["rain", "karrigan", "broky", "ropz", "Twistzz"])],
                },
            ],
            "winner": winner,
            "score1": score_a,
            "score2": score_b,
            "event": {"name": event_name},
            "startedAt": date,
            "bestOf": best_of,
        }

    def test_load_single_match(self, tmp_path):
        json_path = tmp_path / "test.json"
        data = [self._make_grid_match()]
        json_path.write_text(json.dumps(data))

        loader = GridLoader()
        matches = loader.load_json(json_path)

        assert len(matches) == 1
        m = matches[0]
        assert m.match_id == "grid_12345"
        assert m.game == "cs2"
        assert m.team_a == "NAVI"
        assert m.team_b == "FaZe"
        assert m.winner == "NAVI"
        assert m.score_a == 2
        assert m.score_b == 1
        assert m.best_of == 3
        assert m.source == "grid"

    def test_rosters_extracted(self, tmp_path):
        json_path = tmp_path / "test.json"
        data = [self._make_grid_match()]
        json_path.write_text(json.dumps(data))

        loader = GridLoader()
        matches = loader.load_json(json_path)

        m = matches[0]
        assert m.roster_a == ["s1mple", "b1t", "electroNic", "Perfecto", "npl"]
        assert m.roster_b == ["rain", "karrigan", "broky", "ropz", "Twistzz"]

    def test_event_tier_classification(self, tmp_path):
        json_path = tmp_path / "test.json"
        data = [
            self._make_grid_match(match_id="1", event_name="PGL Major Copenhagen 2024"),
            self._make_grid_match(match_id="2", event_name="ESL Pro League Season 19", date="2025-03-01T00:00:00Z"),
        ]
        json_path.write_text(json.dumps(data))

        loader = GridLoader()
        matches = loader.load_json(json_path)

        major = next(m for m in matches if m.match_id == "grid_1")
        esl = next(m for m in matches if m.match_id == "grid_2")
        assert major.event_tier == "s_tier"
        assert major.is_lan is True
        assert esl.event_tier == "a_tier"

    def test_winner_from_scores(self, tmp_path):
        """When no explicit winner, determine from scores."""
        json_path = tmp_path / "test.json"
        match_data = self._make_grid_match()
        del match_data["winner"]  # remove explicit winner
        match_data["score1"] = 2
        match_data["score2"] = 0
        json_path.write_text(json.dumps([match_data]))

        loader = GridLoader()
        matches = loader.load_json(json_path)

        assert matches[0].winner == "NAVI"

    def test_sorted_by_date(self, tmp_path):
        json_path = tmp_path / "test.json"
        data = [
            self._make_grid_match(match_id="late", date="2025-06-01T00:00:00Z"),
            self._make_grid_match(match_id="early", date="2025-01-01T00:00:00Z"),
        ]
        json_path.write_text(json.dumps(data))

        loader = GridLoader()
        matches = loader.load_json(json_path)

        assert matches[0].match_id == "grid_early"
        assert matches[1].match_id == "grid_late"

    def test_ndjson_format(self, tmp_path):
        """Test newline-delimited JSON (one object per line)."""
        json_path = tmp_path / "test.ndjson"
        m1 = self._make_grid_match(match_id="1", date="2025-01-01T00:00:00Z")
        m2 = self._make_grid_match(match_id="2", date="2025-02-01T00:00:00Z")
        json_path.write_text(json.dumps(m1) + "\n" + json.dumps(m2))

        loader = GridLoader()
        matches = loader.load_json(json_path)

        assert len(matches) == 2

    def test_file_not_found(self):
        loader = GridLoader()
        with pytest.raises(FileNotFoundError):
            loader.load_json("nonexistent.json")

    def test_matches_wrapper_format(self, tmp_path):
        """Test {matches: [...]} wrapper format."""
        json_path = tmp_path / "test.json"
        data = {"matches": [self._make_grid_match()]}
        json_path.write_text(json.dumps(data))

        loader = GridLoader()
        matches = loader.load_json(json_path)

        assert len(matches) == 1


class TestHLTVResultsLoader:

    def _write_hltv_csv(self, filepath: Path, rows: list[dict]) -> None:
        fieldnames = ["match_id", "date", "event", "team1", "team2", "score1", "score2", "map", "best_of", "lan"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def test_load_basic(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        self._write_hltv_csv(csv_path, [{
            "match_id": "999",
            "date": "2025-03-15",
            "event": "IEM Katowice 2025",
            "team1": "NAVI",
            "team2": "FaZe",
            "score1": "2",
            "score2": "1",
            "map": "",
            "best_of": "3",
            "lan": "true",
        }])

        loader = HLTVResultsLoader()
        matches = loader.load_csv(csv_path)

        assert len(matches) == 1
        m = matches[0]
        assert m.match_id == "hltv_999"
        assert m.game == "cs2"
        assert m.team_a == "NAVI"
        assert m.winner == "NAVI"
        assert m.score_a == 2
        assert m.score_b == 1
        assert m.is_lan is True
        assert m.source == "hltv"

    def test_team2_wins(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        self._write_hltv_csv(csv_path, [{
            "match_id": "100",
            "date": "2025-01-01",
            "event": "Test",
            "team1": "A",
            "team2": "B",
            "score1": "0",
            "score2": "2",
            "map": "",
            "best_of": "3",
            "lan": "false",
        }])

        loader = HLTVResultsLoader()
        matches = loader.load_csv(csv_path)

        assert matches[0].winner == "B"

    def test_no_rosters_in_hltv(self, tmp_path):
        """HLTV CSV doesn't include rosters."""
        csv_path = tmp_path / "test.csv"
        self._write_hltv_csv(csv_path, [{
            "match_id": "1", "date": "2025-01-01", "event": "Test",
            "team1": "A", "team2": "B", "score1": "1", "score2": "0",
            "map": "", "best_of": "1", "lan": "0",
        }])

        loader = HLTVResultsLoader()
        matches = loader.load_csv(csv_path)

        assert matches[0].roster_a is None
        assert matches[0].roster_b is None
