"""
Oracle's Elixir data loader for LoL professional match results.

Oracle's Elixir (oracleselixir.com) provides CSV exports of pro LoL match
data. Each CSV has row-per-player-per-game format:
  - 12 rows per game (5 players + 1 team summary row x 2 teams)
  - Team summary rows have position == 'team'
  - Player rows have position in {top, jng, mid, bot, sup}

This loader:
  1. Reads CSV files (local or downloaded)
  2. Groups rows by gameid to reconstruct matches
  3. Extracts team names, winner, rosters, patch, date
  4. Outputs RawMatch objects ready for normalizer

Data source: https://oracleselixir.com/tools/downloads
File format: yearly CSVs (2024, 2025, 2026)

Usage::
    loader = OracleElixirLoader()
    matches = loader.load_csv("path/to/2025_LoL_esports_match_data_from_OraclesElixir.csv")
    # Returns list of RawMatch sorted by date
"""
from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from esports_v2.data.normalizer import RawMatch

logger = logging.getLogger(__name__)

# Oracle's Elixir CSV columns we care about
COL_GAMEID = "gameid"
COL_LEAGUE = "league"
COL_SPLIT = "split"
COL_YEAR = "year"
COL_PATCH = "patch"
COL_DATE = "date"
COL_SIDE = "side"           # Blue or Red
COL_POSITION = "position"   # top, jng, mid, bot, sup, team
COL_PLAYERNAME = "playername"
COL_TEAMNAME = "teamname"
COL_RESULT = "result"       # 1 = win, 0 = loss
COL_GAMELENGTH = "gamelength"

# Tier mapping for leagues
TIER_MAP: Dict[str, str] = {
    # S-tier (international)
    "MSI": "s_tier", "Worlds": "s_tier",
    # A-tier (major regions)
    "LCK": "a_tier", "LPL": "a_tier", "LEC": "a_tier", "LCS": "a_tier",
    "LCK CL": "b_tier", "LFL": "b_tier", "PCS": "b_tier",
    "VCS": "b_tier", "CBLOL": "b_tier", "LJL": "b_tier", "LLA": "b_tier",
}

# Positional role mapping
ROLE_MAP: Dict[str, str] = {
    "top": "top",
    "jng": "jungle",
    "mid": "mid",
    "bot": "adc",
    "sup": "support",
}


def _detect_column(fieldnames: list, preferred: str, aliases: List[str]) -> str:
    """
    Find the actual column name from a list of known aliases.

    Returns the preferred name if found, otherwise tries aliases in order.
    Raises ValueError if none found — fail loudly with a clear error.
    """
    if preferred in fieldnames:
        return preferred
    for alias in aliases:
        if alias in fieldnames:
            logger.info("oracle_column_alias", preferred=preferred, actual=alias)
            return alias
    raise ValueError(
        f"Required column '{preferred}' not found in CSV. "
        f"Tried aliases: {aliases}. "
        f"Available columns: {fieldnames[:20]}"
    )


class OracleElixirLoader:
    """Loads and parses Oracle's Elixir LoL match CSVs."""

    def __init__(self) -> None:
        self._loaded_count = 0
        self._skipped_count = 0

    @property
    def loaded_count(self) -> int:
        return self._loaded_count

    @property
    def skipped_count(self) -> int:
        return self._skipped_count

    def load_csv(self, filepath: str | Path) -> List[RawMatch]:
        """
        Load a single Oracle's Elixir CSV and return sorted RawMatch list.

        Args:
            filepath: Path to the CSV file.

        Returns:
            List of RawMatch sorted by match_date ascending.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"CSV not found: {filepath}")

        # Group rows by gameid (handle column name variations across CSV versions)
        games: Dict[str, List[dict]] = defaultdict(list)
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Detect gameid column name — Oracle's Elixir has used different names
            if reader.fieldnames:
                gameid_col = _detect_column(reader.fieldnames, COL_GAMEID, ["game_id", "matchid", "match_id"])
            else:
                gameid_col = COL_GAMEID
            for row in reader:
                gid = row.get(gameid_col, "").strip()
                if gid:
                    games[gid].append(row)

        logger.info("oracle_elixir_loaded", file=str(filepath), games=len(games))

        # Convert each game group to a RawMatch
        matches = []
        for gameid, rows in games.items():
            match = self._parse_game(gameid, rows)
            if match:
                matches.append(match)
                self._loaded_count += 1
            else:
                self._skipped_count += 1

        # Sort by date
        matches.sort(key=lambda m: m.match_date or "")
        logger.info(
            "oracle_elixir_parsed",
            loaded=self._loaded_count,
            skipped=self._skipped_count,
        )
        return matches

    def load_csvs(self, filepaths: List[str | Path]) -> List[RawMatch]:
        """Load multiple CSVs and return combined sorted RawMatch list."""
        all_matches = []
        for fp in filepaths:
            all_matches.extend(self.load_csv(fp))
        all_matches.sort(key=lambda m: m.match_date or "")
        return all_matches

    def _parse_game(self, gameid: str, rows: List[dict]) -> Optional[RawMatch]:
        """
        Parse a single game's rows into a RawMatch.

        Expected: 12 rows (6 per side). Team summary rows (position='team')
        give match-level data. Player rows give roster data.
        """
        # Separate team summary rows from player rows
        team_rows = [r for r in rows if r.get(COL_POSITION) == "team"]
        player_rows = [r for r in rows if r.get(COL_POSITION) != "team"]

        if len(team_rows) < 2:
            logger.debug("oracle_skip_incomplete", gameid=gameid, team_rows=len(team_rows))
            return None

        # Identify Blue and Red side teams
        blue_team = None
        red_team = None
        for tr in team_rows:
            side = tr.get(COL_SIDE, "").strip()
            if side == "Blue":
                blue_team = tr
            elif side == "Red":
                red_team = tr

        if not blue_team or not red_team:
            logger.debug("oracle_skip_no_sides", gameid=gameid)
            return None

        team_a = blue_team.get(COL_TEAMNAME, "").strip()
        team_b = red_team.get(COL_TEAMNAME, "").strip()

        if not team_a or not team_b:
            return None

        # Determine winner
        winner = None
        try:
            if int(blue_team.get(COL_RESULT, 0)) == 1:
                winner = team_a
            elif int(red_team.get(COL_RESULT, 0)) == 1:
                winner = team_b
        except (ValueError, TypeError):
            pass

        # Extract rosters
        roster_a = []  # Blue side
        roster_b = []  # Red side
        for pr in player_rows:
            pname = pr.get(COL_PLAYERNAME, "").strip()
            side = pr.get(COL_SIDE, "").strip()
            if pname:
                if side == "Blue":
                    roster_a.append(pname)
                elif side == "Red":
                    roster_b.append(pname)

        # Extract metadata
        league = blue_team.get(COL_LEAGUE, "").strip()
        patch = blue_team.get(COL_PATCH, "").strip() or None
        date_str = blue_team.get(COL_DATE, "").strip() or None
        year = blue_team.get(COL_YEAR, "").strip()

        # Determine tier
        event_tier = TIER_MAP.get(league, "c_tier")

        # Determine if LAN (international events are typically LAN)
        is_lan = league in ("MSI", "Worlds")

        return RawMatch(
            match_id=f"oe_{gameid}",
            game="lol",
            event_name=f"{league} {year}".strip() if league else None,
            event_tier=event_tier,
            team_a=team_a,
            team_b=team_b,
            winner=winner,
            score_a=None,  # Oracle's Elixir is per-game, not per-series
            score_b=None,
            best_of=None,
            patch=patch,
            match_date=date_str,
            is_lan=is_lan,
            source="oracle_elixir",
            roster_a=roster_a if roster_a else None,
            roster_b=roster_b if roster_b else None,
            raw_data={
                "league": league,
                "split": blue_team.get(COL_SPLIT, "").strip(),
                "year": year,
                "gamelength": blue_team.get(COL_GAMELENGTH, ""),
            },
        )
