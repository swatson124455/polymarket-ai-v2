"""Tests for Oracle's Elixir LoL data loader."""
import csv
import tempfile
from pathlib import Path

import pytest

from esports_v2.data.oracle_loader import OracleElixirLoader, _detect_column


def _write_oracle_csv(filepath: Path, rows: list[dict]) -> None:
    """Write a mock Oracle's Elixir CSV."""
    fieldnames = [
        "gameid", "league", "split", "year", "patch", "date",
        "side", "position", "playername", "teamname", "result", "gamelength",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _make_game_rows(
    gameid: str = "GAME1",
    blue_team: str = "T1",
    red_team: str = "Gen.G",
    blue_wins: bool = True,
    league: str = "LCK",
    date: str = "2025-03-15",
    patch: str = "15.5",
    blue_players: list[str] = None,
    red_players: list[str] = None,
) -> list[dict]:
    """Generate a full game's 12 rows (2 team + 10 player)."""
    if blue_players is None:
        blue_players = ["Zeus", "Oner", "Faker", "Gumayusi", "Keria"]
    if red_players is None:
        red_players = ["Kiin", "Canyon", "Chovy", "Peyz", "Lehends"]

    rows = []
    positions = ["top", "jng", "mid", "bot", "sup"]

    # Blue team summary
    rows.append({
        "gameid": gameid, "league": league, "split": "Spring",
        "year": "2025", "patch": patch, "date": date,
        "side": "Blue", "position": "team", "playername": "",
        "teamname": blue_team, "result": "1" if blue_wins else "0",
        "gamelength": "1800",
    })
    # Blue players
    for i, pos in enumerate(positions):
        rows.append({
            "gameid": gameid, "league": league, "split": "Spring",
            "year": "2025", "patch": patch, "date": date,
            "side": "Blue", "position": pos, "playername": blue_players[i],
            "teamname": blue_team, "result": "1" if blue_wins else "0",
            "gamelength": "1800",
        })
    # Red team summary
    rows.append({
        "gameid": gameid, "league": league, "split": "Spring",
        "year": "2025", "patch": patch, "date": date,
        "side": "Red", "position": "team", "playername": "",
        "teamname": red_team, "result": "0" if blue_wins else "1",
        "gamelength": "1800",
    })
    # Red players
    for i, pos in enumerate(positions):
        rows.append({
            "gameid": gameid, "league": league, "split": "Spring",
            "year": "2025", "patch": patch, "date": date,
            "side": "Red", "position": pos, "playername": red_players[i],
            "teamname": red_team, "result": "0" if blue_wins else "1",
            "gamelength": "1800",
        })
    return rows


class TestOracleElixirLoader:

    def test_load_single_game(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        rows = _make_game_rows()
        _write_oracle_csv(csv_path, rows)

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        assert len(matches) == 1
        assert loader.loaded_count == 1
        assert loader.skipped_count == 0

        m = matches[0]
        assert m.match_id == "oe_GAME1"
        assert m.game == "lol"
        assert m.team_a == "T1"
        assert m.team_b == "Gen.G"
        assert m.winner == "T1"  # blue wins
        assert m.source == "oracle_elixir"
        assert m.patch == "15.5"
        assert m.match_date == "2025-03-15"
        assert m.is_lan is False  # LCK is not international

    def test_rosters_extracted(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        rows = _make_game_rows()
        _write_oracle_csv(csv_path, rows)

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        m = matches[0]
        assert m.roster_a == ["Zeus", "Oner", "Faker", "Gumayusi", "Keria"]
        assert m.roster_b == ["Kiin", "Canyon", "Chovy", "Peyz", "Lehends"]

    def test_red_side_wins(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        rows = _make_game_rows(blue_wins=False)
        _write_oracle_csv(csv_path, rows)

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        assert matches[0].winner == "Gen.G"

    def test_tier_classification(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        # LCK = a_tier, MSI = s_tier
        rows = _make_game_rows(gameid="G1", league="LCK")
        rows += _make_game_rows(gameid="G2", league="MSI", date="2025-05-01")
        _write_oracle_csv(csv_path, rows)

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        lck_match = next(m for m in matches if m.match_id == "oe_G1")
        msi_match = next(m for m in matches if m.match_id == "oe_G2")
        assert lck_match.event_tier == "a_tier"
        assert msi_match.event_tier == "s_tier"
        assert msi_match.is_lan is True

    def test_multiple_games_sorted_by_date(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        rows = _make_game_rows(gameid="LATE", date="2025-06-01")
        rows += _make_game_rows(gameid="EARLY", date="2025-01-01")
        _write_oracle_csv(csv_path, rows)

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        assert len(matches) == 2
        assert matches[0].match_id == "oe_EARLY"
        assert matches[1].match_id == "oe_LATE"

    def test_incomplete_game_skipped(self, tmp_path):
        """Game with only one team row should be skipped."""
        csv_path = tmp_path / "test.csv"
        # Only blue side rows
        rows = _make_game_rows()[:6]  # team summary + 5 players for blue only
        _write_oracle_csv(csv_path, rows)

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        assert len(matches) == 0
        assert loader.skipped_count == 1

    def test_file_not_found_raises(self):
        loader = OracleElixirLoader()
        with pytest.raises(FileNotFoundError):
            loader.load_csv("nonexistent.csv")

    def test_load_multiple_csvs(self, tmp_path):
        csv1 = tmp_path / "2024.csv"
        csv2 = tmp_path / "2025.csv"
        _write_oracle_csv(csv1, _make_game_rows(gameid="G1", date="2024-06-01"))
        _write_oracle_csv(csv2, _make_game_rows(gameid="G2", date="2025-03-01"))

        loader = OracleElixirLoader()
        matches = loader.load_csvs([csv1, csv2])

        assert len(matches) == 2
        assert matches[0].match_id == "oe_G1"  # earlier date
        assert matches[1].match_id == "oe_G2"

    def test_raw_data_populated(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        _write_oracle_csv(csv_path, _make_game_rows())

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        assert matches[0].raw_data["league"] == "LCK"
        assert matches[0].raw_data["year"] == "2025"


class TestDetectColumn:
    """Test column alias detection for CSV format variations."""

    def test_preferred_found(self):
        assert _detect_column(["gameid", "date"], "gameid", ["game_id"]) == "gameid"

    def test_alias_found(self):
        assert _detect_column(["game_id", "date"], "gameid", ["game_id", "matchid"]) == "game_id"

    def test_second_alias(self):
        assert _detect_column(["matchid", "date"], "gameid", ["game_id", "matchid"]) == "matchid"

    def test_missing_raises(self):
        with pytest.raises(ValueError, match="Required column 'gameid' not found"):
            _detect_column(["foo", "bar"], "gameid", ["game_id", "matchid"])

    def test_csv_with_alias_column(self, tmp_path):
        """Full integration: CSV uses 'game_id' instead of 'gameid'."""
        csv_path = tmp_path / "test.csv"
        # Write CSV with 'game_id' column instead of 'gameid'
        rows = _make_game_rows()
        fieldnames = [
            "game_id", "league", "split", "year", "patch", "date",
            "side", "position", "playername", "teamname", "result", "gamelength",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                # Rename gameid -> game_id
                new_row = {("game_id" if k == "gameid" else k): v for k, v in row.items()}
                writer.writerow(new_row)

        loader = OracleElixirLoader()
        matches = loader.load_csv(csv_path)

        assert len(matches) == 1
        assert matches[0].match_id == "oe_GAME1"
