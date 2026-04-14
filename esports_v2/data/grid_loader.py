"""
GRID Open Access + HLTV data loader for CS2 professional match results.

Two data sources for CS2:
  1. GRID Open Access — structured JSON match data (primary)
  2. HLTV results pages — scraped match results (supplementary)

This loader handles local JSON/CSV files exported from GRID or scraped from
HLTV. For live data ingestion (5v2-C), a separate pipeline will connect
to GRID's streaming API.

Usage::
    loader = GridLoader()
    matches = loader.load_json("path/to/grid_cs2_matches.json")

    loader2 = HLTVResultsLoader()
    matches2 = loader2.load_csv("path/to/hltv_results.csv")
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from esports_v2.data.normalizer import RawMatch

logger = logging.getLogger(__name__)

# CS2 event tier classification by event name keywords
S_TIER_KEYWORDS = ["major", "blast premier world final", "iem katowice", "iem cologne"]
A_TIER_KEYWORDS = ["blast premier", "esl pro league", "iem", "pgl"]
B_TIER_KEYWORDS = ["ccr", "dreamhack", "perfect world", "thunderpick"]


def _classify_tier(event_name: str) -> str:
    """Classify CS2 event tier by name keywords."""
    if not event_name:
        return "c_tier"
    name_lower = event_name.lower()
    for kw in S_TIER_KEYWORDS:
        if kw in name_lower:
            return "s_tier"
    for kw in A_TIER_KEYWORDS:
        if kw in name_lower:
            return "a_tier"
    for kw in B_TIER_KEYWORDS:
        if kw in name_lower:
            return "b_tier"
    return "c_tier"


def _is_lan_event(event_name: str) -> bool:
    """Heuristic: major events and IEM/PGL/BLAST finals are typically LAN."""
    if not event_name:
        return False
    name_lower = event_name.lower()
    lan_keywords = ["major", "iem", "blast premier", "pgl", "dreamhack open", "esl one"]
    return any(kw in name_lower for kw in lan_keywords)


class GridLoader:
    """
    Loads CS2 match data from GRID Open Access JSON exports.

    Expected JSON format: list of match objects, each containing:
      - id or matchId: unique identifier
      - teams: [{name, players: [{nickname}]}, ...]
      - seriesScore or maps: score per team
      - event: {name, tier}
      - startedAt or date: ISO timestamp
      - bestOf: series format
    """

    def __init__(self) -> None:
        self._loaded_count = 0
        self._skipped_count = 0

    @property
    def loaded_count(self) -> int:
        return self._loaded_count

    @property
    def skipped_count(self) -> int:
        return self._skipped_count

    def load_json(self, filepath: str | Path) -> List[RawMatch]:
        """
        Load a GRID JSON export file.

        Supports two formats:
          1. List of match objects: [{...}, ...]
          2. NDJSON (one JSON object per line)

        Returns list of RawMatch sorted by date.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"JSON not found: {filepath}")

        content = filepath.read_text(encoding="utf-8")

        # Try parsing as JSON array first
        try:
            data = json.loads(content)
            if isinstance(data, list):
                raw_matches = data
            elif isinstance(data, dict) and "matches" in data:
                raw_matches = data["matches"]
            elif isinstance(data, dict) and "data" in data:
                raw_matches = data["data"]
            else:
                raw_matches = [data]
        except json.JSONDecodeError:
            # Try NDJSON
            raw_matches = []
            for line in content.strip().split("\n"):
                line = line.strip()
                if line:
                    try:
                        raw_matches.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        logger.info("grid_loaded", file=str(filepath), records=len(raw_matches))

        matches = []
        for rm in raw_matches:
            match = self._parse_match(rm)
            if match:
                matches.append(match)
                self._loaded_count += 1
            else:
                self._skipped_count += 1

        matches.sort(key=lambda m: m.match_date or "")
        logger.info("grid_parsed", loaded=self._loaded_count, skipped=self._skipped_count)
        return matches

    def _parse_match(self, data: dict) -> Optional[RawMatch]:
        """Parse a single GRID match record into RawMatch."""
        # Extract match ID
        match_id = str(data.get("id") or data.get("matchId") or data.get("match_id", ""))
        if not match_id:
            return None

        # Extract teams
        teams = data.get("teams", [])
        if len(teams) < 2:
            # Try alternate format
            team_a_name = data.get("team1", data.get("team_a", ""))
            team_b_name = data.get("team2", data.get("team_b", ""))
            roster_a = None
            roster_b = None
        else:
            team_a_name = teams[0].get("name", "")
            team_b_name = teams[1].get("name", "")
            # Extract rosters
            roster_a = self._extract_roster(teams[0])
            roster_b = self._extract_roster(teams[1])

        if not team_a_name or not team_b_name:
            return None

        # Extract scores (use sentinel to distinguish 0 from missing)
        _s = object()
        raw_sa = data.get("score1", _s)
        score_a = _safe_int(raw_sa if raw_sa is not _s else data.get("score_a"))
        raw_sb = data.get("score2", _s)
        score_b = _safe_int(raw_sb if raw_sb is not _s else data.get("score_b"))

        # Extract winner
        winner = data.get("winner", data.get("winnerName"))
        if not winner:
            # Try score-based determination
            if score_a is not None and score_b is not None:
                if score_a > score_b:
                    winner = team_a_name
                elif score_b > score_a:
                    winner = team_b_name

        # Extract event info
        event = data.get("event", {})
        if isinstance(event, str):
            event_name = event
            event_tier = _classify_tier(event_name)
        else:
            event_name = event.get("name", data.get("event_name"))
            event_tier = event.get("tier") or _classify_tier(event_name or "")

        # Extract date
        date_str = data.get("startedAt") or data.get("date") or data.get("match_date")

        # Best-of
        best_of = _safe_int(data.get("bestOf") or data.get("best_of"))

        # Map (for per-map records)
        map_name = data.get("map") or data.get("mapName")

        return RawMatch(
            match_id=f"grid_{match_id}",
            game="cs2",
            event_name=event_name,
            event_tier=event_tier,
            team_a=team_a_name,
            team_b=team_b_name,
            winner=winner,
            score_a=score_a,
            score_b=score_b,
            best_of=best_of,
            map_name=map_name,
            patch=data.get("patch"),
            match_date=date_str,
            is_lan=_is_lan_event(event_name or ""),
            source="grid",
            roster_a=roster_a,
            roster_b=roster_b,
            raw_data=data,
        )

    def _extract_roster(self, team_data: dict) -> Optional[List[str]]:
        """Extract player names from a GRID team object."""
        players = team_data.get("players", [])
        if not players:
            return None
        names = []
        for p in players:
            name = p.get("nickname") or p.get("name") or p.get("ign", "")
            if name:
                names.append(name.strip())
        return names if names else None


class HLTVResultsLoader:
    """
    Loads CS2 match data from HLTV results exports (CSV format).

    Expected CSV columns:
      match_id, date, event, team1, team2, score1, score2, map, best_of,
      stars (event importance), lan
    """

    def __init__(self) -> None:
        self._loaded_count = 0
        self._skipped_count = 0

    @property
    def loaded_count(self) -> int:
        return self._loaded_count

    def load_csv(self, filepath: str | Path) -> List[RawMatch]:
        """Load HLTV results CSV. Returns sorted RawMatch list."""
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"CSV not found: {filepath}")

        matches = []
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                match = self._parse_row(row)
                if match:
                    matches.append(match)
                    self._loaded_count += 1
                else:
                    self._skipped_count += 1

        matches.sort(key=lambda m: m.match_date or "")
        logger.info("hltv_parsed", loaded=self._loaded_count, skipped=self._skipped_count)
        return matches

    def _parse_row(self, row: dict) -> Optional[RawMatch]:
        """Parse a single HLTV CSV row."""
        match_id = row.get("match_id") or row.get("id", "")
        team_a = (row.get("team1") or row.get("team_a", "")).strip()
        team_b = (row.get("team2") or row.get("team_b", "")).strip()

        if not team_a or not team_b:
            return None

        _s = object()
        raw_sa = row.get("score1", _s)
        score_a = _safe_int(raw_sa if raw_sa is not _s else row.get("score_a"))
        raw_sb = row.get("score2", _s)
        score_b = _safe_int(raw_sb if raw_sb is not _s else row.get("score_b"))

        winner = None
        if score_a is not None and score_b is not None:
            if score_a > score_b:
                winner = team_a
            elif score_b > score_a:
                winner = team_b

        event_name = (row.get("event") or row.get("event_name", "")).strip()
        is_lan = row.get("lan", "").strip().lower() in ("1", "true", "yes")
        date_str = row.get("date", "").strip() or None

        return RawMatch(
            match_id=f"hltv_{match_id}" if match_id else f"hltv_{team_a}_{team_b}_{date_str}",
            game="cs2",
            event_name=event_name or None,
            event_tier=_classify_tier(event_name),
            team_a=team_a,
            team_b=team_b,
            winner=winner,
            score_a=score_a,
            score_b=score_b,
            best_of=_safe_int(row.get("best_of")),
            map_name=(row.get("map") or "").strip() or None,
            match_date=date_str,
            is_lan=is_lan,
            source="hltv",
            raw_data=dict(row),
        )


def _safe_int(val) -> Optional[int]:
    """Safely convert to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
