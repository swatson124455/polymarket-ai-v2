"""
Esports Data Collector — fetches historical match data from PandaScore for model training.

Extracts game-state features from completed matches and stores in esports_training_data
table. Feeds LoLWinModel (17 features) and CS2EconomyModel (13 round features).

Rate-limited: 1 request / 4 seconds to stay under 1K req/hour (PandaScore free tier).

Usage::
    collector = EsportsDataCollector(pandascore_client=client)
    stats = await collector.collect_historical(game="lol", days_back=90, db=db)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

# LoL feature names (must match lol_win_model.FEATURE_NAMES)
_LOL_FEATURES = [
    "game_time_minutes", "gold_pct_blue", "team_xp_diff", "alive_diff",
    "tower_kills_diff", "dragon_kills_diff", "dragon_soul_blue", "herald_blue",
    "inhib_down_diff", "baron_buff_blue", "elder_buff_blue", "baron_buff_count_diff",
    "gold_diff_top", "gold_diff_jungle", "gold_diff_mid", "gold_diff_adc", "gold_diff_support",
]

# CS2 round feature names (must match cs2_economy_model.ROUND_FEATURES)
_CS2_FEATURES = [
    "team_a_money", "team_b_money", "team_a_equip_value", "team_b_equip_value",
    "round_score_a", "round_score_b", "map_ct_rate", "team_a_is_ct",
    "team_a_loss_streak", "team_b_loss_streak", "bomb_planted",
    "team_a_alive", "team_b_alive",
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
            db: AsyncSession for DB writes (optional — if None, returns data without persisting).

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
            except Exception as exc:
                stats["errors"] += 1
                logger.debug(
                    "EsportsDataCollector: match processing failed",
                    match_id=match.match_id,
                    error=str(exc),
                )

            # Rate limit between match detail fetches
            await asyncio.sleep(4.0)

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

        For LoL: 1 row per game (game-level features at end-of-game state).
        For CS2: 1 row per round (round-level economy + outcome).
        """
        if game == "lol":
            return await self._process_lol_match(match)
        elif game == "cs2":
            return await self._process_cs2_match(match)
        return []

    # ── LoL extraction ──────────────────────────────────────────────────

    async def _process_lol_match(self, match) -> List[Dict[str, Any]]:
        """
        Extract LoL game-level features from match detail.

        PandaScore provides per-game data including team stats at end-of-game.
        We extract the 17 FEATURE_NAMES and blue_win label.
        """
        games = await self._ps.get_match_games_detail(match.match_id)
        if not games:
            return []

        rows = []
        for g in games:
            if not isinstance(g, dict):
                continue

            winner = g.get("winner", {})
            if not isinstance(winner, dict) or not winner.get("id"):
                continue

            teams = g.get("teams", [])
            if len(teams) < 2:
                continue

            # PandaScore uses "teams" array: index 0 = blue, index 1 = red
            blue_team = teams[0] if isinstance(teams[0], dict) else {}
            red_team = teams[1] if isinstance(teams[1], dict) else {}

            blue_id = blue_team.get("team", {}).get("id", 0) if isinstance(blue_team.get("team"), dict) else 0
            winner_id = winner.get("id", -1)
            blue_win = 1 if blue_id == winner_id else 0

            # Extract game-level stats into features
            game_state = self._extract_lol_features(blue_team, red_team, g)

            # Determine patch from match data
            patch = ""
            detail_data = g.get("detail", {}) or {}
            if isinstance(detail_data, dict):
                patch = str(detail_data.get("patch", ""))
            if not patch:
                raw = getattr(match, "raw", {})
                if isinstance(raw, dict):
                    patch = str(raw.get("patch", {}).get("name", "")) if isinstance(raw.get("patch"), dict) else ""

            rows.append({
                "match_id": str(match.match_id),
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
        """Extract 17 LoL features from PandaScore game-level team stats."""
        # Game duration
        length = game_data.get("length", 0) or 0
        game_time_minutes = length / 60.0 if length else 30.0

        # Team-level stats from PandaScore
        blue_stats = blue.get("stats", {}) or {}
        red_stats = red.get("stats", {}) or {}

        blue_gold = float(blue_stats.get("gold_earned", 0) or 0)
        red_gold = float(red_stats.get("gold_earned", 0) or 0)
        total_gold = blue_gold + red_gold

        # Build feature dict
        features: Dict[str, float] = {
            "game_time_minutes": game_time_minutes,
            "gold_pct_blue": blue_gold / total_gold if total_gold > 0 else 0.5,
            "team_xp_diff": 0.0,  # PandaScore doesn't always provide XP
            "alive_diff": 0.0,    # End-of-game: not meaningful (one nexus dead)
            "tower_kills_diff": float(blue_stats.get("tower_kills", 0) or 0) - float(red_stats.get("tower_kills", 0) or 0),
            "dragon_kills_diff": float(blue_stats.get("dragon_kills", 0) or 0) - float(red_stats.get("dragon_kills", 0) or 0),
            "dragon_soul_blue": 1.0 if int(blue_stats.get("dragon_kills", 0) or 0) >= 4 else 0.0,
            "herald_blue": float(blue_stats.get("herald_kill", 0) or blue_stats.get("rift_heralds", 0) or 0) > 0,
            "inhib_down_diff": float(blue_stats.get("inhibitor_kills", 0) or 0) - float(red_stats.get("inhibitor_kills", 0) or 0),
            "baron_buff_blue": float(blue_stats.get("baron_kills", 0) or 0) > 0,
            "elder_buff_blue": 0.0,  # PandaScore doesn't reliably track elder
            "baron_buff_count_diff": float(blue_stats.get("baron_kills", 0) or 0) - float(red_stats.get("baron_kills", 0) or 0),
            # Role gold diffs — PandaScore doesn't always provide per-role gold
            # Use 0.0 as placeholder; trainer handles missing features
            "gold_diff_top": 0.0,
            "gold_diff_jungle": 0.0,
            "gold_diff_mid": 0.0,
            "gold_diff_adc": 0.0,
            "gold_diff_support": 0.0,
        }

        # Try to extract per-role gold from player stats
        blue_players = blue.get("players", []) or []
        red_players = red.get("players", []) or []
        if len(blue_players) >= 5 and len(red_players) >= 5:
            role_map = {"top": 0, "jun": 1, "mid": 2, "adc": 3, "sup": 4}
            role_keys = ["gold_diff_top", "gold_diff_jungle", "gold_diff_mid", "gold_diff_adc", "gold_diff_support"]

            for bp, rp, key in zip(blue_players[:5], red_players[:5], role_keys):
                bp_stats = bp.get("stats", {}) or {} if isinstance(bp, dict) else {}
                rp_stats = rp.get("stats", {}) or {} if isinstance(rp, dict) else {}
                bg = float(bp_stats.get("gold_earned", 0) or 0)
                rg = float(rp_stats.get("gold_earned", 0) or 0)
                features[key] = bg - rg

        # Convert bools to float
        for k, v in features.items():
            features[k] = float(v)

        return features

    # ── CS2 extraction ──────────────────────────────────────────────────

    async def _process_cs2_match(self, match) -> List[Dict[str, Any]]:
        """
        Extract CS2 round-level features from match detail.

        PandaScore provides round-by-round data for CS2 matches.
        """
        games = await self._ps.get_match_games_detail(match.match_id)
        if not games:
            return []

        rows = []
        for g_idx, g in enumerate(games):
            if not isinstance(g, dict):
                continue

            rounds = g.get("rounds", [])
            if not rounds or not isinstance(rounds, list):
                continue

            map_name = str(g.get("map", {}).get("name", "")).lower() if isinstance(g.get("map"), dict) else ""
            ct_rate = _MAP_SIDE_RATES.get(map_name, 0.50)

            winner = g.get("winner", {})
            winner_id = winner.get("id", -1) if isinstance(winner, dict) else -1

            teams = g.get("teams", [])
            if len(teams) < 2:
                continue

            team_a_data = teams[0] if isinstance(teams[0], dict) else {}
            team_b_data = teams[1] if isinstance(teams[1], dict) else {}
            team_a_id = team_a_data.get("team", {}).get("id", 0) if isinstance(team_a_data.get("team"), dict) else 0
            team_b_id = team_b_data.get("team", {}).get("id", 0) if isinstance(team_b_data.get("team"), dict) else 0

            score_a = 0
            score_b = 0
            loss_streak_a = 0
            loss_streak_b = 0

            for r_idx, rnd in enumerate(rounds):
                if not isinstance(rnd, dict):
                    continue

                round_winner_id = None
                rw = rnd.get("winner", {})
                if isinstance(rw, dict):
                    round_winner_id = rw.get("id")
                elif isinstance(rw, (int, str)):
                    round_winner_id = int(rw)

                # Determine if team_a is CT in this round (first 12 rounds = starting side)
                # PandaScore: first_side in teams array
                team_a_is_ct = 1.0
                first_side_a = str(team_a_data.get("first_side", "ct")).lower()
                if r_idx < 12:
                    team_a_is_ct = 1.0 if first_side_a == "ct" else 0.0
                else:
                    # After halftime: sides swap
                    team_a_is_ct = 0.0 if first_side_a == "ct" else 1.0

                round_state = {
                    "team_a_money": float(rnd.get("team_a_economy", 0) or 0),
                    "team_b_money": float(rnd.get("team_b_economy", 0) or 0),
                    "team_a_equip_value": float(rnd.get("team_a_equipment_value", 0) or rnd.get("team_a_economy", 0) or 0),
                    "team_b_equip_value": float(rnd.get("team_b_equipment_value", 0) or rnd.get("team_b_economy", 0) or 0),
                    "round_score_a": float(score_a),
                    "round_score_b": float(score_b),
                    "map_ct_rate": ct_rate,
                    "team_a_is_ct": team_a_is_ct,
                    "team_a_loss_streak": float(loss_streak_a),
                    "team_b_loss_streak": float(loss_streak_b),
                    "bomb_planted": 0.0,  # Historical data is end-of-round
                    "team_a_alive": 5.0,  # Pre-round: all alive
                    "team_b_alive": 5.0,
                }

                # Outcome
                if round_winner_id is not None:
                    team_a_won = 1 if int(round_winner_id) == int(team_a_id) else 0
                else:
                    continue  # Skip rounds without clear winner

                rows.append({
                    "match_id": f"{match.match_id}_g{g_idx}_r{r_idx}",
                    "game": "cs2",
                    "team_a": match.team_a,
                    "team_b": match.team_b,
                    "patch": "",
                    "game_state_json": round_state,
                    "outcome": team_a_won,
                    "snapshot_type": "round",
                    "tournament": match.tournament,
                    "scheduled_at": match.scheduled_at or None,
                })

                # Update running state
                if team_a_won:
                    score_a += 1
                    loss_streak_a = 0
                    loss_streak_b += 1
                else:
                    score_b += 1
                    loss_streak_b = 0
                    loss_streak_a += 1

        return rows

    # ── DB persistence ──────────────────────────────────────────────────

    async def _store_row(self, db, row: Dict[str, Any]) -> None:
        """Store a single training data row in esports_training_data."""
        try:
            game_state_str = json.dumps(row["game_state_json"])
            await db.execute(
                """
                INSERT INTO esports_training_data
                    (match_id, game, team_a, team_b, patch, game_state_json,
                     outcome, snapshot_type, tournament, scheduled_at)
                VALUES
                    (:match_id, :game, :team_a, :team_b, :patch, :game_state_json::jsonb,
                     :outcome, :snapshot_type, :tournament, :scheduled_at)
                ON CONFLICT DO NOTHING
                """,
                {
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
                },
            )
            await db.commit()
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
        """
        if db is None:
            return []

        try:
            snapshot_type = "match" if game == "lol" else "round"
            result = await db.execute(
                """
                SELECT match_id, game, patch, game_state_json, outcome, scheduled_at
                FROM esports_training_data
                WHERE game = :game AND snapshot_type = :snapshot_type
                ORDER BY created_at DESC
                LIMIT :limit
                """,
                {"game": game, "snapshot_type": snapshot_type, "limit": limit},
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
                    training_data.append(entry)
                elif game == "cs2":
                    entry = dict(state)
                    entry["team_a_won_round"] = int(row.outcome)
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
