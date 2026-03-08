"""
Esports Data Collector — fetches historical match data from PandaScore for model training.

Extracts game-state features from completed matches and stores in esports_training_data
table. Feeds LoLWinModel (9 features) and CS2EconomyModel (14 features).

PandaScore free tier only provides match-level data (winners, scores, game lengths)
but NOT per-game team stats or per-round economy. We use match outcomes + Glicko-2
team_strength_diff as primary training signal. During LIVE matches, the game monitor
provides real in-game features (gold, economy, etc).

Rate-limited: 0.5s per novel team-stats lookup (cached after first call).

Usage::
    collector = EsportsDataCollector(pandascore_client=client)
    stats = await collector.collect_historical(game="lol", days_back=90, db=db)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sa_text
from structlog import get_logger

logger = get_logger()

# LoL feature names (must match lol_win_model.FEATURE_NAMES)
# Only features reliably available from PandaScore in training AND live inference.
_LOL_FEATURES = [
    "game_time_minutes", "gold_pct_blue", "tower_kills_diff", "dragon_kills_diff",
    "dragon_soul_blue", "herald_blue", "inhib_down_diff", "baron_buff_count_diff",
    "team_strength_diff",
]

# CS2 round feature names (must match cs2_economy_model.ROUND_FEATURES)
_CS2_FEATURES = [
    "team_a_money", "team_b_money", "team_a_equip_value", "team_b_equip_value",
    "round_score_a", "round_score_b", "map_ct_rate", "team_a_is_ct",
    "team_a_loss_streak", "team_b_loss_streak", "bomb_planted",
    "team_a_alive", "team_b_alive", "team_strength_diff",
]

# CS2 map CT win rates (professional average)
_MAP_SIDE_RATES = {
    "nuke": 0.57, "ancient": 0.55, "anubis": 0.54, "vertigo": 0.54,
    "inferno": 0.53, "mirage": 0.52, "dust2": 0.48,
}


class EsportsDataCollector:
    """
    Collects historical match data from PandaScore and transforms it into
    training rows for LoL and CS2 models.
    """

    def __init__(self, pandascore_client) -> None:
        self._ps = pandascore_client
        self._team_strength_cache: Dict[str, float] = {}  # "game:team_id" -> win_rate
        # Glicko-2 trackers for team strength (per game)
        self._glicko2_trackers: Dict[str, Any] = {}  # game -> Glicko2Tracker

    async def collect_historical(
        self,
        game: str,
        days_back: int = 90,
        db=None,
    ) -> Dict[str, int]:
        """
        Collect historical matches for a game and store training data.

        Args:
            game: 'lol' or 'cs2' (other games supported later).
            days_back: Number of days of history to fetch.
            db: Database instance for DB writes (optional -- if None, returns data without persisting).

        Returns:
            Dict with stats: {'matches_fetched', 'rows_stored', 'errors'}.
        """
        stats = {"matches_fetched": 0, "rows_stored": 0, "errors": 0}

        # Fetch completed matches
        matches = await self._ps.get_past_matches(game=game, days_back=days_back)
        stats["matches_fetched"] = len(matches)

        if not matches:
            logger.info("EsportsDataCollector: no past matches found", game=game, days_back=days_back)
            return stats

        for match in matches:
            try:
                rows = await self._process_match(match, game)
                for row in rows:
                    if db is not None:
                        await self._store_row(db, row)
                    stats["rows_stored"] += 1
                # Update Glicko-2 ratings after processing match
                self._update_glicko2(match, game)
            except Exception as exc:
                stats["errors"] += 1
                logger.debug(
                    "EsportsDataCollector: match processing failed",
                    match_id=match.match_id,
                    error=str(exc),
                )

            # Yield control so other coroutines can run; actual API rate limiting
            # is handled inside _get_team_strength (0.5s per novel team lookup,
            # cached after first call). Sleeping 4s *per match* caused 962 x 4s =
            # 64-minute processing time -- always exceeding the 300s scan timeout.
            await asyncio.sleep(0)

        logger.info(
            "EsportsDataCollector: collection complete",
            game=game,
            days_back=days_back,
            matches_fetched=stats["matches_fetched"],
            rows_stored=stats["rows_stored"],
            errors=stats["errors"],
        )
        return stats

    async def _process_match(self, match, game: str) -> List[Dict[str, Any]]:
        """
        Process a single match into training rows.

        For LoL: 1 row per game in the series (match-level features).
        For CS2: 1 row per map (match-level features).
        """
        if game == "lol":
            return await self._process_lol_match(match)
        elif game == "cs2":
            return await self._process_cs2_match(match)
        else:
            # Generic processor for dota2, valorant, cod, r6, sc2, rl
            return await self._process_generic_match(match, game)

    # -- LoL extraction ------------------------------------------------------

    async def _process_lol_match(self, match) -> List[Dict[str, Any]]:
        """
        Extract LoL training data from match-level data.

        PandaScore free tier provides match outcomes but not per-game team stats.
        We create 1 row per game using the embedded games array (which has winners)
        and team_strength_diff from Glicko-2. In-game features (gold, towers) are
        set to neutral defaults -- the model learns primarily from team strength.
        During LIVE matches, the game monitor provides real in-game features.
        """
        raw = getattr(match, "raw", {}) or {}
        games_list = raw.get("games", [])
        if not games_list:
            # Single-game match -- use match-level outcome
            games_list = [{"winner": raw.get("winner", {}), "length": 0}]

        # Compute team strength diff + Glicko-2 metadata once per match
        team_str_diff = await self._compute_team_strength_diff(match, "lol")
        glicko2_meta = self._get_glicko2_metadata(match, "lol")

        # Determine patch from match raw data
        patch = ""
        vv = raw.get("videogame_version", {})
        if isinstance(vv, dict):
            patch = str(vv.get("name", ""))

        rows = []
        for g in games_list:
            if not isinstance(g, dict):
                continue

            winner = g.get("winner", {})
            if not isinstance(winner, dict) or not winner.get("id"):
                continue

            winner_id = winner.get("id", -1)
            # team_a = first opponent = "blue" side
            blue_win = 1 if winner_id == match.team_a_id else 0

            # Game length
            length = g.get("length", 0) or 0
            game_time_minutes = length / 60.0 if length else 30.0

            # Build feature dict. In-game features (gold, towers, dragons)
            # are neutral (PandaScore free tier has no per-game team stats).
            # Glicko-2 metadata features (uncertainty, volatility) are REAL
            # and provide signal beyond team_strength_diff alone.
            # During LIVE matches, the game monitor provides real in-game features.
            game_state = {
                "game_time_minutes": game_time_minutes,
                "gold_pct_blue": 0.5,           # Neutral (no label leakage)
                "tower_kills_diff": 0.0,         # Neutral
                "dragon_kills_diff": 0.0,        # Neutral
                "matchup_uncertainty": glicko2_meta["matchup_uncertainty"],
                "rd_asymmetry": glicko2_meta["rd_asymmetry"],
                "team_a_volatility": glicko2_meta["team_a_volatility"],
                "team_b_volatility": glicko2_meta["team_b_volatility"],
                "team_strength_diff": team_str_diff,
            }

            rows.append({
                "match_id": f"{match.match_id}_g{g.get('position', 0)}",
                "game": "lol",
                "team_a": match.team_a,
                "team_b": match.team_b,
                "patch": patch,
                "game_state_json": game_state,
                "outcome": blue_win,
                "snapshot_type": "match",
                "tournament": match.tournament,
                "scheduled_at": match.scheduled_at or None,
            })

        return rows

    def _extract_lol_features(
        self, blue: Dict, red: Dict, game_data: Dict
    ) -> Dict[str, float]:
        """Extract 8 reliable LoL features from PandaScore game-level team stats.

        NOTE: Only used if paid-tier game detail data is available (not currently used).
        """
        # Game duration
        length = game_data.get("length", 0) or 0
        game_time_minutes = length / 60.0 if length else 30.0

        # Team-level stats from PandaScore
        blue_stats = blue.get("stats", {}) or {}
        red_stats = red.get("stats", {}) or {}

        blue_gold = float(blue_stats.get("gold_earned", 0) or 0)
        red_gold = float(red_stats.get("gold_earned", 0) or 0)
        total_gold = blue_gold + red_gold

        # Build feature dict -- only 8 reliable features
        features: Dict[str, float] = {
            "game_time_minutes": game_time_minutes,
            "gold_pct_blue": blue_gold / total_gold if total_gold > 0 else 0.5,
            "tower_kills_diff": float(blue_stats.get("tower_kills", 0) or 0) - float(red_stats.get("tower_kills", 0) or 0),
            "dragon_kills_diff": float(blue_stats.get("dragon_kills", 0) or 0) - float(red_stats.get("dragon_kills", 0) or 0),
            "dragon_soul_blue": 1.0 if int(blue_stats.get("dragon_kills", 0) or 0) >= 4 else 0.0,
            "herald_blue": float(int(blue_stats.get("herald_kill", 0) or blue_stats.get("rift_heralds", 0) or 0) > 0),
            "inhib_down_diff": float(blue_stats.get("inhibitor_kills", 0) or 0) - float(red_stats.get("inhibitor_kills", 0) or 0),
            "baron_buff_count_diff": float(blue_stats.get("baron_kills", 0) or 0) - float(red_stats.get("baron_kills", 0) or 0),
        }

        return features

    # -- CS2 extraction ------------------------------------------------------

    async def _process_cs2_match(self, match) -> List[Dict[str, Any]]:
        """
        Extract CS2 training data from match-level data.

        PandaScore free tier provides match outcomes but not per-round economy.
        We create 1 row per map using the embedded games array (which has winners
        and basic metadata). team_strength_diff from Glicko-2 is the primary
        feature. During LIVE matches, the game monitor provides real economy data.
        """
        raw = getattr(match, "raw", {}) or {}
        games_list = raw.get("games", [])
        if not games_list:
            games_list = [{"winner": raw.get("winner", {})}]

        # Compute team strength diff once per match (cached)
        team_str_diff = await self._compute_team_strength_diff(match, "cs2")

        rows = []
        for g_idx, g in enumerate(games_list):
            if not isinstance(g, dict):
                continue

            winner = g.get("winner", {})
            if not isinstance(winner, dict) or not winner.get("id"):
                continue

            winner_id = winner.get("id", -1)
            # team_a = first opponent
            team_a_won = 1 if winner_id == match.team_a_id else 0

            # Map name for CT rate
            map_name = ""
            map_data = g.get("map")
            if isinstance(map_data, dict):
                map_name = str(map_data.get("name", "")).lower()
            ct_rate = _MAP_SIDE_RATES.get(map_name, 0.50)

            # Build feature dict with neutral economy defaults.
            # team_strength_diff + map_ct_rate are the real signals.
            # Economy features are neutral (PandaScore free tier has no round data).
            # During LIVE matches, the game monitor provides real economy data.
            # NOTE: round_score_a/b are neutral 6.0 (not outcome-derived) to avoid
            # label leakage. Old code used 8/5 or 5/8 based on winner — tautological.
            game_state = {
                "team_a_money": 4150.0,   # Neutral: typical round-start
                "team_b_money": 4150.0,
                "team_a_equip_value": 4150.0,
                "team_b_equip_value": 4150.0,
                "round_score_a": 6.0,     # Neutral half-score (no label leakage)
                "round_score_b": 6.0,
                "map_ct_rate": ct_rate,
                "team_a_is_ct": 0.5,      # Unknown side -- neutral
                "team_a_loss_streak": 0.0,
                "team_b_loss_streak": 0.0,
                "bomb_planted": 0.0,
                "team_a_alive": 5.0,
                "team_b_alive": 5.0,
                "team_strength_diff": team_str_diff,
            }

            rows.append({
                "match_id": f"{match.match_id}_g{g_idx}",
                "game": "cs2",
                "team_a": match.team_a,
                "team_b": match.team_b,
                "patch": "",
                "game_state_json": game_state,
                "outcome": team_a_won,
                "snapshot_type": "match",
                "tournament": match.tournament,
                "scheduled_at": match.scheduled_at or None,
            })

        return rows

    # -- Generic extraction (all other games) ---------------------------------

    async def _process_generic_match(self, match, game: str) -> List[Dict[str, Any]]:
        """Generic processor for games without dedicated feature extraction.

        Works for dota2, valorant, cod, r6, sc2, rl.
        Stores outcome + team_strength_diff + Glicko-2 metadata for Glicko-2 training.
        Creates 1 row per game in the series.
        """
        raw = getattr(match, "raw", {}) or {}
        games_list = raw.get("games", [])
        if not games_list:
            games_list = [{"winner": raw.get("winner", {}), "length": 0}]

        team_str_diff = await self._compute_team_strength_diff(match, game)
        glicko2_meta = self._get_glicko2_metadata(match, game)

        rows = []
        for g_idx, g in enumerate(games_list):
            if not isinstance(g, dict):
                continue
            winner = g.get("winner", {})
            if not isinstance(winner, dict) or not winner.get("id"):
                continue
            winner_id = winner.get("id", -1)
            team_a_won = 1 if winner_id == match.team_a_id else 0

            game_state = {
                "team_strength_diff": team_str_diff,
                "matchup_uncertainty": glicko2_meta["matchup_uncertainty"],
                "rd_asymmetry": glicko2_meta["rd_asymmetry"],
                "team_a_volatility": glicko2_meta["team_a_volatility"],
                "team_b_volatility": glicko2_meta["team_b_volatility"],
            }
            rows.append({
                "match_id": f"{match.match_id}_g{g_idx}",
                "game": game,
                "team_a": match.team_a,
                "team_b": match.team_b,
                "patch": "",
                "game_state_json": game_state,
                "outcome": team_a_won,
                "snapshot_type": "match",
                "tournament": match.tournament,
                "scheduled_at": match.scheduled_at or None,
            })
        return rows

    # -- Team strength -------------------------------------------------------

    async def _get_team_strength(self, team_id: int, game: str) -> float:
        """Get team win rate from PandaScore (cached per session).

        Returns win rate 0.0-1.0, or 0.5 if unavailable.
        """
        cache_key = f"{game}:{team_id}"
        if cache_key in self._team_strength_cache:
            return self._team_strength_cache[cache_key]

        win_rate = 0.5  # default
        try:
            # Rate limit here -- only when actually calling the API (cache miss above).
            # 0.5s per novel team x ~200 unique teams = ~100s, well under 300s timeout.
            await asyncio.sleep(0.5)
            stats = await self._ps.get_team_stats(team_id, game)
            if stats and isinstance(stats, dict):
                # PandaScore team stats: look for win/loss counts
                wins = int(stats.get("wins", 0) or 0)
                losses = int(stats.get("losses", 0) or 0)
                total = wins + losses
                if total >= 5:  # need enough games for meaningful rate
                    win_rate = wins / total
                elif "winrate" in stats:
                    wr = float(stats.get("winrate", 0.5) or 0.5)
                    if 0.0 < wr <= 1.0:
                        win_rate = wr
        except Exception as exc:
            logger.debug("EsportsDataCollector: team strength fetch failed", team_id=team_id, error=str(exc))

        self._team_strength_cache[cache_key] = win_rate
        return win_rate

    async def _compute_team_strength_diff(self, match, game: str) -> float:
        """Compute team_strength_diff using Glicko-2 (preferred) or raw win-rate (fallback)."""
        team_a_id = getattr(match, "team_a_id", 0)
        team_b_id = getattr(match, "team_b_id", 0)
        if not team_a_id or not team_b_id:
            return 0.0

        # Try Glicko-2 first (if tracker has seen both teams)
        tracker = self._glicko2_trackers.get(game)
        if tracker is not None and tracker.match_count >= 10:
            rating_a = tracker.get_rating(str(team_a_id))
            rating_b = tracker.get_rating(str(team_b_id))
            # Only use Glicko-2 if both teams have been rated (phi < default)
            if rating_a.phi < 350.0 and rating_b.phi < 350.0:
                return tracker.strength_diff(str(team_a_id), str(team_b_id))

        # Fallback: raw PandaScore win rate
        wr_a = await self._get_team_strength(team_a_id, game)
        wr_b = await self._get_team_strength(team_b_id, game)
        return wr_a - wr_b

    def _get_glicko2_metadata(self, match, game: str) -> Dict[str, float]:
        """Extract per-team Glicko-2 metadata (uncertainty, volatility) for training features.

        Called BEFORE _update_glicko2() so ratings reflect pre-match state.
        Returns defaults when tracker is unavailable.
        """
        defaults = {
            "matchup_uncertainty": 1.0,  # max uncertainty
            "rd_asymmetry": 0.0,
            "team_a_volatility": 1.0,
            "team_b_volatility": 1.0,
        }
        tracker = self._glicko2_trackers.get(game)
        if tracker is None or tracker.match_count < 10:
            return defaults
        team_a_id = str(getattr(match, "team_a_id", 0))
        team_b_id = str(getattr(match, "team_b_id", 0))
        if not team_a_id or team_a_id == "0" or not team_b_id or team_b_id == "0":
            return defaults
        rating_a = tracker.get_rating(team_a_id)
        rating_b = tracker.get_rating(team_b_id)
        return {
            "matchup_uncertainty": (rating_a.phi + rating_b.phi) / 700.0,
            "rd_asymmetry": (rating_a.phi - rating_b.phi) / 350.0,
            "team_a_volatility": rating_a.sigma / 0.06,
            "team_b_volatility": rating_b.sigma / 0.06,
        }

    def _update_glicko2(self, match, game: str) -> None:
        """Update Glicko-2 ratings after processing a match result."""
        try:
            from esports.models.glicko2 import Glicko2Tracker

            if game not in self._glicko2_trackers:
                self._glicko2_trackers[game] = Glicko2Tracker()

            tracker = self._glicko2_trackers[game]
            team_a_id = str(getattr(match, "team_a_id", 0))
            team_b_id = str(getattr(match, "team_b_id", 0))
            if not team_a_id or team_a_id == "0" or not team_b_id or team_b_id == "0":
                return

            # Determine winner from match result
            winner_id = getattr(match, "winner_id", None)
            if winner_id is not None:
                winner = "a" if str(winner_id) == team_a_id else "b"
            else:
                score_a = getattr(match, "score_a", 0) or 0
                score_b = getattr(match, "score_b", 0) or 0
                if score_a > score_b:
                    winner = "a"
                elif score_b > score_a:
                    winner = "b"
                else:
                    return  # Can't determine winner

            tracker.process_match(team_a_id, team_b_id, winner=winner)
        except Exception:
            pass  # Glicko-2 update is best-effort

    # -- DB persistence ------------------------------------------------------

    async def _store_row(self, db, row: Dict[str, Any]) -> None:
        """Store a single training data row in esports_training_data.

        Args:
            db: Database instance (has get_session()) -- NOT a raw session.
        """
        try:
            game_state_str = json.dumps(row["game_state_json"])
            params = {
                "match_id": row["match_id"],
                "game": row["game"],
                "team_a": row.get("team_a", ""),
                "team_b": row.get("team_b", ""),
                "patch": row.get("patch", ""),
                "game_state_json": game_state_str,
                "outcome": row["outcome"],
                "snapshot_type": row.get("snapshot_type", "match"),
                "tournament": row.get("tournament", ""),
                "scheduled_at": row.get("scheduled_at"),
            }
            async with db.get_session() as session:
                await session.execute(
                    sa_text("""
                        INSERT INTO esports_training_data
                            (match_id, game, team_a, team_b, patch, game_state_json,
                             outcome, snapshot_type, tournament, scheduled_at)
                        VALUES
                            (:match_id, :game, :team_a, :team_b, :patch, :game_state_json::jsonb,
                             :outcome, :snapshot_type, :tournament, :scheduled_at)
                        ON CONFLICT DO NOTHING
                    """),
                    params,
                )
                await session.commit()
        except Exception as exc:
            logger.debug(
                "EsportsDataCollector: store failed",
                match_id=row.get("match_id"),
                error=str(exc),
            )

    async def get_training_data(
        self, db, game: str, limit: int = 5000
    ) -> List[Dict[str, Any]]:
        """
        Load training data from DB for a game.

        Returns list of dicts with feature keys + 'outcome' (0/1) + 'patch'.
        Ready to feed into LoLWinModel.train() or CS2EconomyModel.train().

        Args:
            db: Database instance (has get_session()).
        """
        if db is None:
            return []

        try:
            async with db.get_session() as session:
                result = await session.execute(
                    sa_text("""
                        SELECT match_id, game, patch, game_state_json, outcome, scheduled_at
                        FROM esports_training_data
                        WHERE game = :game
                        ORDER BY created_at DESC
                        LIMIT :limit
                    """),
                    {"game": game, "limit": limit},
                )
                rows = result.fetchall()

            training_data = []
            for row in rows:
                state = row.game_state_json if isinstance(row.game_state_json, dict) else {}
                if game == "lol":
                    # Merge feature dict + label + patch
                    entry = dict(state)
                    entry["blue_win"] = int(row.outcome)
                    entry["patch"] = row.patch or ""
                    entry["match_id"] = row.match_id or ""
                    # Neutralize label-leaked features from old data.
                    # Old collector set gold=0.55 if win, towers=2.0 if win, etc.
                    # These encode the outcome into features → tautological training.
                    entry["gold_pct_blue"] = 0.5
                    entry["tower_kills_diff"] = 0.0
                    entry["dragon_kills_diff"] = 0.0
                    entry["dragon_soul_blue"] = 0.0
                    entry["inhib_down_diff"] = 0.0
                    entry["baron_buff_count_diff"] = 0.0
                    training_data.append(entry)
                elif game == "cs2":
                    entry = dict(state)
                    entry["team_a_won_round"] = int(row.outcome)
                    entry["match_id"] = row.match_id or ""
                    # Neutralize label-leaked round_score from old data.
                    # Old collector set round_score=8/5 based on winner → label leakage.
                    entry["round_score_a"] = 6.0
                    entry["round_score_b"] = 6.0
                    training_data.append(entry)
                else:
                    # Generic: dota2, valorant, cod, r6, sc2, rl
                    entry = dict(state)
                    entry["team_a_won"] = int(row.outcome)
                    entry["match_id"] = row.match_id or ""
                    training_data.append(entry)

            logger.info(
                "EsportsDataCollector: loaded training data",
                game=game,
                rows=len(training_data),
            )
            return training_data

        except Exception as exc:
            logger.warning("EsportsDataCollector: load training data failed", error=str(exc))
            return []
