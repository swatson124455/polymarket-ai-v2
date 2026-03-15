"""
Draft Feature Engineering for EsportsBot CatBoost model.

Computes champion/agent win rates, synergy pairs, counter-pick deltas,
ban impact, pool depth, and categorical pick-slot features from
PandaScore draft data stored in esports_training_data.game_state_json.

Games supported: LoL, Dota2, Valorant, R6.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from structlog import get_logger

from config.settings import settings

logger = get_logger()

# Max pick slots per team (LoL=5, Dota2=5, Valorant=5, R6=5)
_MAX_PICKS = 5
_NONE_SENTINEL = "__NONE__"


class DraftFeatureBuilder:
    """Builds numeric + categorical features from draft picks/bans.

    Usage:
        builder = DraftFeatureBuilder()
        await builder.fit_stats(db, "lol", min_samples=20)
        features = builder.build_features(draft_data, "lol")
    """

    def __init__(self) -> None:
        # Per-game champion stats: game -> {champ_name_lower: {wins: int, total: int}}
        self._champ_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
        # Per-game synergy stats: game -> {(c1, c2): {wins: int, total: int}}
        self._synergy_stats: Dict[str, Dict[Tuple[str, str], Dict[str, int]]] = {}
        # Per-game counter stats: game -> {(my_pick, their_pick): {wins: int, total: int}}
        self._counter_stats: Dict[str, Dict[Tuple[str, str], Dict[str, int]]] = {}
        # Per-game pool depth: game -> {team_lower: set of champs played}
        self._pool_depth: Dict[str, Dict[str, set]] = {}
        # Fitted games
        self._fitted_games: set = set()
        self._last_fit_time: Dict[str, float] = {}

    @property
    def fitted_games(self) -> set:
        return set(self._fitted_games)

    async def fit_stats(
        self, db, game: str, min_samples: int = 20
    ) -> Dict[str, Any]:
        """Compute champion win rates, synergy, counter-pick stats from DB.

        Queries esports_training_data WHERE game=:game, extracts draft
        from game_state_json, tallies stats.

        Args:
            db: Database session provider.
            game: Game slug (lol, dota2, valorant, r6).
            min_samples: Minimum games for a champion stat to be reliable.

        Returns:
            Summary stats dict for logging.
        """
        if db is None:
            return {"error": "no db"}

        _min_samples = int(getattr(settings, "ESPORTS_DRAFT_MIN_SAMPLES", min_samples))
        _min_cooccur = int(getattr(settings, "ESPORTS_DRAFT_SYNERGY_MIN_COOCCUR", 5))

        try:
            from sqlalchemy import text as _text
            async with db.get_session() as session:
                result = await session.execute(
                    _text("""
                    SELECT game_state_json, outcome, team_a, team_b
                    FROM esports_training_data
                    WHERE game = :game
                      AND game_state_json IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 5000
                    """),
                    {"game": game},
                )
                rows = result.fetchall()
        except Exception as exc:
            logger.warning("draft_features_fit_failed", game=game, error=str(exc))
            return {"error": str(exc)}

        if not rows:
            return {"rows": 0}

        champ_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"wins": 0, "total": 0})
        synergy_stats: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"wins": 0, "total": 0})
        counter_stats: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"wins": 0, "total": 0})
        pool_depth: Dict[str, set] = defaultdict(set)
        draft_count = 0

        for row in rows:
            gs = row.game_state_json if hasattr(row, "game_state_json") else row[0]
            outcome = row.outcome if hasattr(row, "outcome") else row[1]
            team_a_name = (row.team_a if hasattr(row, "team_a") else row[2] or "").lower().strip()
            team_b_name = (row.team_b if hasattr(row, "team_b") else row[3] or "").lower().strip()

            if not isinstance(gs, dict):
                continue
            draft = gs.get("draft")
            if not isinstance(draft, dict):
                continue

            a_picks = [str(p).lower().strip() for p in draft.get("team_a_picks", []) if p]
            b_picks = [str(p).lower().strip() for p in draft.get("team_b_picks", []) if p]
            if not a_picks and not b_picks:
                continue

            draft_count += 1
            team_a_won = int(outcome) == 1 if outcome is not None else None
            if team_a_won is None:
                continue

            # Champion win rates
            for champ in a_picks:
                champ_stats[champ]["total"] += 1
                if team_a_won:
                    champ_stats[champ]["wins"] += 1
            for champ in b_picks:
                champ_stats[champ]["total"] += 1
                if not team_a_won:
                    champ_stats[champ]["wins"] += 1

            # Synergy: all pairs on same team
            for i, c1 in enumerate(a_picks):
                for c2 in a_picks[i + 1:]:
                    pair = tuple(sorted([c1, c2]))
                    synergy_stats[pair]["total"] += 1
                    if team_a_won:
                        synergy_stats[pair]["wins"] += 1
            for i, c1 in enumerate(b_picks):
                for c2 in b_picks[i + 1:]:
                    pair = tuple(sorted([c1, c2]))
                    synergy_stats[pair]["total"] += 1
                    if not team_a_won:
                        synergy_stats[pair]["wins"] += 1

            # Counter-pick: my pick vs their pick
            for my_pick in a_picks:
                for their_pick in b_picks:
                    key = (my_pick, their_pick)
                    counter_stats[key]["total"] += 1
                    if team_a_won:
                        counter_stats[key]["wins"] += 1
            for my_pick in b_picks:
                for their_pick in a_picks:
                    key = (my_pick, their_pick)
                    counter_stats[key]["total"] += 1
                    if not team_a_won:
                        counter_stats[key]["wins"] += 1

            # Pool depth
            if team_a_name:
                pool_depth[team_a_name].update(a_picks)
            if team_b_name:
                pool_depth[team_b_name].update(b_picks)

        # Store (filter by min_samples for reliability)
        self._champ_stats[game] = {
            k: v for k, v in champ_stats.items() if v["total"] >= _min_samples
        }
        self._synergy_stats[game] = {
            k: v for k, v in synergy_stats.items() if v["total"] >= _min_cooccur
        }
        self._counter_stats[game] = {
            k: v for k, v in counter_stats.items() if v["total"] >= _min_cooccur
        }
        self._pool_depth[game] = dict(pool_depth)
        self._fitted_games.add(game)
        self._last_fit_time[game] = time.monotonic()

        summary = {
            "game": game,
            "rows_with_draft": draft_count,
            "unique_champions": len(champ_stats),
            "reliable_champions": len(self._champ_stats[game]),
            "synergy_pairs": len(self._synergy_stats[game]),
            "counter_pairs": len(self._counter_stats[game]),
            "teams_tracked": len(pool_depth),
        }
        logger.info("draft_features_fitted", **summary)
        return summary

    def build_features(
        self,
        draft_data: Optional[Dict],
        game: str,
        team_a: str = "",
        team_b: str = "",
    ) -> Dict[str, Any]:
        """Transform raw draft dict into feature dict for CatBoost.

        Returns dict with:
          Numeric: avg_champ_wr_a/b, synergy_score_a/b, counter_score_a/b,
                   ban_impact_a/b, pool_depth_a/b, draft_advantage
          Categorical: team_a_pick_0..4, team_b_pick_0..4

        Gracefully degrades: missing draft -> all zeros / _NONE_SENTINEL.
        """
        features: Dict[str, Any] = {}

        # Initialize all numeric features to 0.0
        for suffix in ("a", "b"):
            features[f"avg_champ_wr_{suffix}"] = 0.0
            features[f"synergy_score_{suffix}"] = 0.0
            features[f"counter_score_{suffix}"] = 0.0
            features[f"ban_impact_{suffix}"] = 0.0
            features[f"pool_depth_{suffix}"] = 0.0

        features["draft_advantage"] = 0.0

        # Categorical pick slots (padded)
        for team_prefix in ("team_a", "team_b"):
            for i in range(_MAX_PICKS):
                features[f"{team_prefix}_pick_{i}"] = _NONE_SENTINEL

        if not draft_data or not isinstance(draft_data, dict):
            return features

        a_picks_raw = draft_data.get("team_a_picks", [])
        b_picks_raw = draft_data.get("team_b_picks", [])
        a_bans_raw = draft_data.get("team_a_bans", [])
        b_bans_raw = draft_data.get("team_b_bans", [])

        a_picks = [str(p).lower().strip() for p in a_picks_raw if p]
        b_picks = [str(p).lower().strip() for p in b_picks_raw if p]
        a_bans = [str(b).lower().strip() for b in a_bans_raw if b]
        b_bans = [str(b).lower().strip() for b in b_bans_raw if b]

        # Categorical pick slots
        for i in range(_MAX_PICKS):
            features[f"team_a_pick_{i}"] = a_picks[i] if i < len(a_picks) else _NONE_SENTINEL
            features[f"team_b_pick_{i}"] = b_picks[i] if i < len(b_picks) else _NONE_SENTINEL

        # Get stats for this game
        champ_wr = self._champ_stats.get(game, {})
        synergy = self._synergy_stats.get(game, {})
        counter = self._counter_stats.get(game, {})
        pool = self._pool_depth.get(game, {})

        # Average champion win rate per team
        def _avg_wr(picks: List[str]) -> float:
            rates = []
            for c in picks:
                s = champ_wr.get(c)
                if s and s["total"] > 0:
                    rates.append(s["wins"] / s["total"])
            return sum(rates) / len(rates) if rates else 0.5

        features["avg_champ_wr_a"] = _avg_wr(a_picks)
        features["avg_champ_wr_b"] = _avg_wr(b_picks)

        # Ban impact: average win rate of banned champions (denying strong picks)
        features["ban_impact_a"] = _avg_wr(a_bans)  # team_a's bans deny these from team_b
        features["ban_impact_b"] = _avg_wr(b_bans)

        # Synergy score: sum of synergy deltas for all pairs on each team
        def _synergy_score(picks: List[str]) -> float:
            score = 0.0
            for i, c1 in enumerate(picks):
                for c2 in picks[i + 1:]:
                    pair = tuple(sorted([c1, c2]))
                    s = synergy.get(pair)
                    if s and s["total"] > 0:
                        pair_wr = s["wins"] / s["total"]
                        # Delta vs average individual win rates
                        wr1 = champ_wr.get(c1, {})
                        wr2 = champ_wr.get(c2, {})
                        avg_individual = 0.5
                        cnt = 0
                        if wr1 and wr1["total"] > 0:
                            avg_individual += wr1["wins"] / wr1["total"]
                            cnt += 1
                        if wr2 and wr2["total"] > 0:
                            avg_individual += wr2["wins"] / wr2["total"]
                            cnt += 1
                        if cnt > 0:
                            avg_individual = avg_individual / (cnt + 1) if cnt < 2 else (avg_individual - 0.5) / cnt
                        score += pair_wr - avg_individual
            return score

        features["synergy_score_a"] = _synergy_score(a_picks)
        features["synergy_score_b"] = _synergy_score(b_picks)

        # Counter score: sum of counter-pick deltas
        def _counter_score(my_picks: List[str], their_picks: List[str]) -> float:
            score = 0.0
            for my_c in my_picks:
                for their_c in their_picks:
                    s = counter.get((my_c, their_c))
                    if s and s["total"] > 0:
                        score += (s["wins"] / s["total"]) - 0.5  # delta vs even
            return score

        features["counter_score_a"] = _counter_score(a_picks, b_picks)
        features["counter_score_b"] = _counter_score(b_picks, a_picks)

        # Pool depth
        team_a_lower = team_a.lower().strip() if team_a else ""
        team_b_lower = team_b.lower().strip() if team_b else ""
        features["pool_depth_a"] = float(len(pool.get(team_a_lower, set()))) if team_a_lower else 0.0
        features["pool_depth_b"] = float(len(pool.get(team_b_lower, set()))) if team_b_lower else 0.0

        # Composite draft advantage
        wr_diff = features["avg_champ_wr_a"] - features["avg_champ_wr_b"]
        syn_diff = features["synergy_score_a"] - features["synergy_score_b"]
        ctr_diff = features["counter_score_a"] - features["counter_score_b"]
        features["draft_advantage"] = (wr_diff + syn_diff + ctr_diff) / 3.0

        return features

    def get_cat_feature_names(self) -> List[str]:
        """Return categorical feature column names (pick slots)."""
        names = []
        for team_prefix in ("team_a", "team_b"):
            for i in range(_MAX_PICKS):
                names.append(f"{team_prefix}_pick_{i}")
        return names

    def get_numeric_feature_names(self) -> List[str]:
        """Return numeric feature column names."""
        return [
            "avg_champ_wr_a", "avg_champ_wr_b",
            "synergy_score_a", "synergy_score_b",
            "counter_score_a", "counter_score_b",
            "ban_impact_a", "ban_impact_b",
            "pool_depth_a", "pool_depth_b",
            "draft_advantage",
        ]

    def get_all_feature_names(self) -> List[str]:
        """Return all feature names in order (numeric + categorical)."""
        return self.get_numeric_feature_names() + self.get_cat_feature_names()

    def needs_refit(self, game: str, interval_seconds: float = 3600.0) -> bool:
        """Check if stats need refreshing (default: 1h TTL)."""
        if game not in self._fitted_games:
            return True
        last = self._last_fit_time.get(game, 0.0)
        return (time.monotonic() - last) > interval_seconds
