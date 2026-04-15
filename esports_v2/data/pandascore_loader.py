"""
PandaScore historical match loader for EsportsBot v2.

Synchronous REST client that fetches finished CS2 (and optionally LoL) matches
from PandaScore's free-tier API. Outputs RawMatch objects consistent with
Oracle's Elixir and GRID loaders.

PandaScore still uses the "csgo" slug for CS2.

Rate limit: 1000 req/hour (free tier). This loader self-throttles to stay
under budget.

Usage::
    loader = PandaScoreLoader(api_key="...")
    matches = loader.fetch_cs2_matches(days_back=730)
    loader.save_json(matches, "data/cs2/pandascore_cs2.json")
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from esports_v2.data.normalizer import RawMatch

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.pandascore.co"
_REQ_DELAY = 4.0  # seconds between requests (900 req/hr, under 1K limit)
_MAX_PAGES = 50   # safety cap: 50 * 100 = 5000 matches max per call
_PER_PAGE = 100   # PandaScore max per page

# PandaScore uses "csgo" slug for CS2
GAME_SLUGS = {
    "cs2": "csgo",
    "lol": "lol",
}

# Tier classification by league/tournament name
S_TIER_KEYWORDS = ["major", "blast premier world final", "iem katowice", "iem cologne"]
A_TIER_KEYWORDS = ["blast premier", "esl pro league", "iem", "pgl"]
B_TIER_KEYWORDS = ["ccr", "dreamhack", "perfect world", "thunderpick"]


def _classify_tier(event_name: str) -> str:
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
    if not event_name:
        return False
    name_lower = event_name.lower()
    lan_keywords = ["major", "iem", "blast premier", "pgl", "dreamhack open", "esl one"]
    return any(kw in name_lower for kw in lan_keywords)


class PandaScoreLoader:
    """Synchronous PandaScore REST client for historical match data."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("PandaScore API key required")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })
        self._request_count = 0

    def fetch_matches(self, game: str, days_back: int = 730) -> List[RawMatch]:
        """
        Fetch finished matches for a game from the last N days.

        Args:
            game: 'cs2' or 'lol'.
            days_back: How far back to fetch (default 2 years).

        Returns:
            List of RawMatch sorted by date ascending.
        """
        if game not in GAME_SLUGS:
            raise ValueError(f"Unsupported game: {game}. Use one of: {list(GAME_SLUGS)}")

        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        since = (now - _dt.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        slug = GAME_SLUGS[game]

        all_matches: List[RawMatch] = []
        page = 1

        logger.info(f"pandascore_fetch_start game={game} slug={slug} days_back={days_back}")

        while page <= _MAX_PAGES:
            params = {
                "per_page": _PER_PAGE,
                "page": page,
                "sort": "scheduled_at",
                "range[scheduled_at]": f"{since},{until}",
                "filter[status]": "finished",
            }

            data = self._get(f"/{slug}/matches/past", params=params)
            if not data or not isinstance(data, list) or len(data) == 0:
                break

            for raw in data:
                match = self._parse_match(raw, game)
                if match:
                    all_matches.append(match)

            logger.info(
                f"pandascore_page game={game} page={page} "
                f"batch={len(data)} total={len(all_matches)}"
            )

            if len(data) < _PER_PAGE:
                break

            page += 1
            time.sleep(_REQ_DELAY)

        all_matches.sort(key=lambda m: m.match_date or "")
        logger.info(f"pandascore_fetch_done game={game} matches={len(all_matches)} requests={self._request_count}")
        return all_matches

    def fetch_cs2_matches(self, days_back: int = 730) -> List[RawMatch]:
        """Convenience: fetch CS2 matches."""
        return self.fetch_matches("cs2", days_back)

    def fetch_lol_matches(self, days_back: int = 730) -> List[RawMatch]:
        """Convenience: fetch LoL matches."""
        return self.fetch_matches("lol", days_back)

    def save_json(self, matches: List[RawMatch], filepath: str | Path) -> None:
        """Save matches to JSON for reuse by GridLoader or direct backtest loading."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        records = []
        for m in matches:
            records.append({
                "id": m.match_id.replace("ps_", ""),
                "teams": [
                    {"name": m.team_a, "players": [{"nickname": p} for p in (m.roster_a or [])]},
                    {"name": m.team_b, "players": [{"nickname": p} for p in (m.roster_b or [])]},
                ],
                "winner": m.winner,
                "score1": m.score_a,
                "score2": m.score_b,
                "bestOf": m.best_of,
                "event": {"name": m.event_name, "tier": m.event_tier},
                "startedAt": m.match_date,
                "map": m.map_name,
                "patch": m.patch,
                "source": "pandascore",
            })

        with open(filepath, "w") as f:
            json.dump(records, f, indent=2, default=str)
        logger.info(f"pandascore_saved path={filepath} count={len(records)}")

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[list]:
        """HTTP GET with retry."""
        for attempt in range(3):
            try:
                resp = self._session.get(
                    f"{_BASE_URL}{path}",
                    params=params,
                    timeout=15,
                )
                self._request_count += 1

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "10"))
                    logger.warning(f"pandascore_rate_limited retry_after={retry_after}")
                    time.sleep(retry_after)
                    continue

                if resp.status_code == 404:
                    return None

                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as e:
                logger.warning(f"pandascore_request_error attempt={attempt+1} error={e}")
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))

        return None

    def _parse_match(self, raw: dict, game: str) -> Optional[RawMatch]:
        """Parse a PandaScore match JSON into RawMatch."""
        match_id = raw.get("id")
        if not match_id:
            return None

        # Teams
        opponents = raw.get("opponents", [])
        if len(opponents) < 2:
            return None

        team_a_data = opponents[0].get("opponent", {}) if isinstance(opponents[0], dict) else {}
        team_b_data = opponents[1].get("opponent", {}) if isinstance(opponents[1], dict) else {}
        team_a = team_a_data.get("name", "")
        team_b = team_b_data.get("name", "")

        if not team_a or not team_b:
            return None

        # Score
        results = raw.get("results", [])
        score_a = int(results[0].get("score", 0)) if len(results) > 0 else None
        score_b = int(results[1].get("score", 0)) if len(results) > 1 else None

        # Winner from PandaScore
        winner_data = raw.get("winner")
        winner = None
        if isinstance(winner_data, dict):
            winner = winner_data.get("name")
        elif score_a is not None and score_b is not None:
            if score_a > score_b:
                winner = team_a
            elif score_b > score_a:
                winner = team_b

        # Tournament / league
        league = raw.get("league", {})
        tournament = raw.get("tournament", {})
        event_name = (
            tournament.get("name", "") if isinstance(tournament, dict) else ""
        ) or (
            league.get("name", "") if isinstance(league, dict) else ""
        )

        # Rosters
        roster_a = self._extract_roster(team_a_data)
        roster_b = self._extract_roster(team_b_data)

        # Date
        date_str = raw.get("scheduled_at") or raw.get("begin_at")

        # Best-of
        best_of = raw.get("number_of_games")
        if best_of is not None:
            best_of = int(best_of)

        return RawMatch(
            match_id=f"ps_{match_id}",
            game=game,
            event_name=event_name or None,
            event_tier=_classify_tier(event_name or ""),
            team_a=team_a,
            team_b=team_b,
            winner=winner,
            score_a=score_a,
            score_b=score_b,
            best_of=best_of,
            patch=None,
            match_date=date_str,
            is_lan=_is_lan_event(event_name or ""),
            source="pandascore",
            roster_a=roster_a,
            roster_b=roster_b,
            raw_data=raw,
        )

    @staticmethod
    def _extract_roster(team_data: dict) -> Optional[List[str]]:
        """Extract player names from PandaScore opponent data."""
        players = team_data.get("players", [])
        if not players or not isinstance(players, list):
            return None
        names = []
        for p in players:
            if isinstance(p, dict):
                name = p.get("name") or p.get("slug", "")
                if name:
                    names.append(name.strip())
        return names if names else None
