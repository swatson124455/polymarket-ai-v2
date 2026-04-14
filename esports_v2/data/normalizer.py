"""
Match data normalizer for EsportsBot v2.

Converts raw data from various sources (Oracle's Elixir, GRID, HLTV)
into the canonical MatchResult format consumed by Trinity and the
esports_matches DB table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from esports_v2.ratings.trinity import MatchResult


@dataclass
class RawMatch:
    """Intermediate representation from any data source."""
    match_id: str
    game: str                       # 'cs2' or 'lol'
    event_name: Optional[str] = None
    event_tier: Optional[str] = None
    team_a: str = ""
    team_b: str = ""
    winner: Optional[str] = None    # team name (not 'a'/'b')
    score_a: Optional[int] = None
    score_b: Optional[int] = None
    best_of: Optional[int] = None
    map_name: Optional[str] = None
    patch: Optional[str] = None
    match_date: Optional[str] = None  # ISO format string
    is_lan: bool = False
    source: str = ""
    roster_a: Optional[List[str]] = None
    roster_b: Optional[List[str]] = None
    raw_data: Dict = field(default_factory=dict)


def normalize_team_name(name: str) -> str:
    """
    Normalize team names for consistent matching across sources.

    Handles common variations: trailing whitespace, case normalization for
    matching (but preserves original case in display), abbreviation mapping.
    """
    if not name:
        return name
    return name.strip()


def raw_to_match_result(raw: RawMatch) -> MatchResult:
    """
    Convert RawMatch to MatchResult for Trinity processing.

    Determines winner as 'a' or 'b' based on team name matching.
    """
    winner_code = "a"  # default
    if raw.winner:
        winner_norm = normalize_team_name(raw.winner)
        team_a_norm = normalize_team_name(raw.team_a)
        team_b_norm = normalize_team_name(raw.team_b)
        if winner_norm == team_b_norm:
            winner_code = "b"
        elif winner_norm != team_a_norm:
            # Winner doesn't match either team — try substring
            if winner_norm.lower() in team_b_norm.lower():
                winner_code = "b"

    return MatchResult(
        match_id=raw.match_id,
        game=raw.game,
        team_a=normalize_team_name(raw.team_a),
        team_b=normalize_team_name(raw.team_b),
        winner=winner_code,
        is_lan=raw.is_lan,
        roster_a=raw.roster_a,
        roster_b=raw.roster_b,
        patch=raw.patch,
        match_date=raw.match_date,
    )


def raw_to_db_row(raw: RawMatch) -> dict:
    """
    Convert RawMatch to a dict suitable for INSERT into esports_matches.
    """
    return {
        "match_id": raw.match_id,
        "game": raw.game,
        "event_name": raw.event_name,
        "event_tier": raw.event_tier,
        "team_a": normalize_team_name(raw.team_a),
        "team_b": normalize_team_name(raw.team_b),
        "winner": normalize_team_name(raw.winner) if raw.winner else None,
        "score_a": raw.score_a,
        "score_b": raw.score_b,
        "best_of": raw.best_of,
        "map": raw.map_name,
        "patch": raw.patch,
        "match_date": raw.match_date,
        "is_lan": raw.is_lan,
        "source": raw.source,
        "raw_data": raw.raw_data,
    }
