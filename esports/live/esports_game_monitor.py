"""
Esports Game Monitor — PandaScore polling for live match state.

Mirrors sports/live/game_monitor.py pattern. Polls PandaScore REST API
every 15s for live matches across all 4 game titles.

Each update is placed onto an asyncio.Queue for EsportsEventDetector to consume.

Usage::
    monitor = EsportsGameMonitor(update_queue, pandascore_client)
    asyncio.create_task(monitor.run_forever())
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from structlog import get_logger
from config.settings import settings

logger = get_logger()

_BASE_BACKOFF = int(getattr(settings, "ESPORTS_MONITOR_BASE_BACKOFF", 30))
_MAX_BACKOFF = int(getattr(settings, "ESPORTS_MONITOR_MAX_BACKOFF", 300))
_POLL_INTERVAL = int(getattr(settings, "ESPORTS_MONITOR_POLL_INTERVAL", 15))

# CS2 map CT win rates (professional average)
_MAP_SIDE_RATES = {
    "nuke": 0.57, "ancient": 0.55, "anubis": 0.54, "vertigo": 0.54,
    "inferno": 0.53, "mirage": 0.52, "dust2": 0.48,
}


@dataclass
class EsportsGameState:
    """Current snapshot of a live esports match."""
    match_id: str
    game: str                    # lol / cs2 / dota2 / valorant
    team_a: str = ""
    team_b: str = ""
    score_maps_a: int = 0       # Maps won (series level)
    score_maps_b: int = 0
    best_of: int = 1
    current_map: int = 0        # Which map is being played (1-indexed)
    status: str = "not_started"  # not_started / running / finished / canceled
    elapsed_pct: float = 0.0    # 0.0 → 1.0 (fraction of current map elapsed)
    last_updated: float = field(default_factory=time.monotonic)

    # Per-map game state (game-specific)
    game_state: Dict[str, Any] = field(default_factory=dict)
    # LoL: gold_diff, tower_diff, dragon_diff, baron, elder, alive_diff
    # CS2: round_score_a, round_score_b, team_money_a, team_money_b, loss_streak_a/b

    @property
    def score_diff(self) -> int:
        return abs(self.score_maps_a - self.score_maps_b)

    @property
    def leading_team(self) -> str:
        if self.score_maps_a > self.score_maps_b:
            return self.team_a
        elif self.score_maps_b > self.score_maps_a:
            return self.team_b
        return ""


class EsportsGameMonitor:
    """
    PandaScore polling monitor for live esports matches.

    Polls every 15s, pushes EsportsGameState to queue.
    """

    def __init__(self, update_queue: asyncio.Queue, pandascore_client=None) -> None:
        self._queue = update_queue
        self._ps = pandascore_client
        self._running = False
        self._consecutive_failures = 0
        self._active_games: Dict[str, EsportsGameState] = {}
        # Cache team win rates for strength diff: "game:team_id" → win_rate
        self._team_strength_cache: Dict[str, float] = {}
        # Optional Glicko-2 tracker (set by bot if available)
        self._glicko2_trackers: Dict[str, Any] = {}  # game → Glicko2Tracker
        # CS2 loss streak tracking between polls
        self._cs2_prev_scores: Dict[str, tuple] = {}   # match_id → (score_a, score_b)
        self._cs2_loss_streaks: Dict[str, tuple] = {}   # match_id → (streak_a, streak_b)
        # E3: Stale match detection — track last score change per match
        self._last_score_update: Dict[str, float] = {}  # match_id → monotonic timestamp
        self._prev_scores: Dict[str, tuple] = {}         # match_id → (maps_a, maps_b)
        # E4: Cancel queue — match_ids of canceled matches for position exit
        self._canceled_matches: asyncio.Queue = asyncio.Queue(maxsize=100)

    @property
    def active_games(self) -> Dict[str, EsportsGameState]:
        return dict(self._active_games)

    async def run_forever(self) -> None:
        """Poll PandaScore for live matches indefinitely."""
        if not self._ps:
            logger.info("EsportsGameMonitor: no PandaScore client — monitor inactive")
            return

        self._running = True
        logger.info("EsportsGameMonitor: starting live match monitor", poll_interval_s=_POLL_INTERVAL)

        while self._running:
            try:
                await self._poll_live_matches()
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                logger.info("EsportsGameMonitor: cancelled")
                break
            except Exception as exc:
                self._consecutive_failures += 1
                backoff = min(
                    _BASE_BACKOFF * (2 ** (self._consecutive_failures - 1)),
                    _MAX_BACKOFF,
                )
                logger.warning(
                    "EsportsGameMonitor: error — retrying",
                    error=str(exc),
                    backoff_s=backoff,
                    failures=self._consecutive_failures,
                )
                await asyncio.sleep(backoff)

    async def stop(self) -> None:
        """Signal the monitor to stop."""
        self._running = False

    async def _poll_live_matches(self) -> None:
        """Poll PandaScore for live matches across all games."""
        # E5: Adaptive polling — reduce frequency when API budget is low
        try:
            from esports.data.pandascore_client import PandaScoreClient
            remaining = PandaScoreClient.get_remaining_budget()
        except Exception:
            remaining = 999  # fail-open

        if remaining < 100:
            poll_sleep = 60
        elif remaining < 200:
            poll_sleep = 30
        else:
            poll_sleep = _POLL_INTERVAL

        # When budget is tight, only poll games with active matches
        games_to_poll = ("lol", "cs2", "dota2", "valorant")
        if remaining < 200 and self._active_games:
            active_game_titles = {s.game for s in self._active_games.values()}
            if active_game_titles:
                games_to_poll = tuple(active_game_titles)

        _poll_timeout = int(getattr(settings, "ESPORTS_LIVE_POLL_TIMEOUT", 10))
        for game in games_to_poll:
            try:
                matches = await asyncio.wait_for(
                    self._ps.get_live_matches(game=game), timeout=_poll_timeout
                )
                for match in matches:
                    state = self._parse_match_to_state(match, game)
                    if state and state.status == "running":
                        # E3: Track score changes for stale detection
                        mid = state.match_id
                        cur_score = (state.score_maps_a, state.score_maps_b)
                        prev_score = self._prev_scores.get(mid)
                        if prev_score is None or cur_score != prev_score:
                            self._last_score_update[mid] = time.monotonic()
                        self._prev_scores[mid] = cur_score

                        self._active_games[mid] = state
                        try:
                            self._queue.put_nowait(state)
                        except asyncio.QueueFull:
                            logger.debug(
                                "EsportsGameMonitor: queue full — dropping",
                                match_id=mid,
                            )
                    elif state and state.match_id in self._active_games:
                        if state.status in ("finished", "canceled"):
                            # E4: Push canceled matches for position exit
                            if state.status == "canceled":
                                try:
                                    self._canceled_matches.put_nowait(state.match_id)
                                except asyncio.QueueFull:
                                    pass
                            del self._active_games[state.match_id]
                            self._cs2_prev_scores.pop(state.match_id, None)
                            self._cs2_loss_streaks.pop(state.match_id, None)
                            self._last_score_update.pop(state.match_id, None)
                            self._prev_scores.pop(state.match_id, None)
            except asyncio.TimeoutError:
                logger.info("EsportsGameMonitor: poll timeout", game=game, timeout_s=_poll_timeout)
            except Exception as exc:
                logger.info("EsportsGameMonitor: poll error", game=game, error=str(exc))

        if self._active_games:
            logger.debug(
                "EsportsGameMonitor: active matches",
                count=len(self._active_games),
                match_ids=list(self._active_games.keys())[:10],
            )

        await asyncio.sleep(poll_sleep)

    def _parse_match_to_state(self, match, game: str) -> Optional[EsportsGameState]:
        """Convert PandaScore EsportsMatch to EsportsGameState."""
        try:
            from esports.data.pandascore_client import EsportsMatch
            if not isinstance(match, EsportsMatch):
                return None

            # Determine current map number
            current_map = match.score_a + match.score_b + 1

            # Estimate elapsed percentage from match timing
            # PandaScore doesn't provide precise in-game time — use rough estimate
            elapsed = 0.0
            if match.status == "running":
                elapsed = 0.5  # Mid-match default when we don't have precise timing

            # Build game-specific state from raw data
            game_state = self._extract_game_state(match.raw, game, match_id=str(match.match_id))

            # Add team strength diff from cached win rates
            game_state["team_strength_diff"] = self._get_cached_team_strength_diff(
                match.team_a_id, match.team_b_id, game,
            )

            return EsportsGameState(
                match_id=str(match.match_id),
                game=game,
                team_a=match.team_a,
                team_b=match.team_b,
                score_maps_a=match.score_a,
                score_maps_b=match.score_b,
                best_of=match.best_of,
                current_map=current_map,
                status="running" if match.status == "running" else match.status,
                elapsed_pct=elapsed,
                game_state=game_state,
            )
        except Exception as exc:
            logger.debug("EsportsGameMonitor: parse error", error=str(exc))
            return None

    def _extract_game_state(self, raw: Dict[str, Any], game: str, match_id: str = "") -> Dict[str, Any]:
        """Extract game-specific state from PandaScore raw match data."""
        state: Dict[str, Any] = {}
        games_list = raw.get("games", [])
        if not games_list:
            return state

        # Get the current (last) game in the series
        current_game = games_list[-1] if games_list else {}

        if game == "lol":
            # LoL: emit feature names matching lol_win_model.FEATURE_NAMES
            teams = current_game.get("teams", [])
            if len(teams) >= 2:
                t0 = teams[0] if isinstance(teams[0], dict) else {}
                t1 = teams[1] if isinstance(teams[1], dict) else {}
                t0s = t0.get("stats", {}) or {}
                t1s = t1.get("stats", {}) or {}
                gold_a = float(t0s.get("gold_earned", 0) or t0.get("gold_earned", 0) or 0)
                gold_b = float(t1s.get("gold_earned", 0) or t1.get("gold_earned", 0) or 0)
                total_gold = gold_a + gold_b
                # Model features (match lol_win_model.FEATURE_NAMES exactly)
                state["gold_pct_blue"] = gold_a / total_gold if total_gold > 0 else 0.5
                state["tower_kills_diff"] = int(t0s.get("tower_kills", 0) or t0.get("tower_kills", 0) or 0) - int(t1s.get("tower_kills", 0) or t1.get("tower_kills", 0) or 0)
                state["dragon_kills_diff"] = int(t0s.get("dragon_kills", 0) or t0.get("dragon_kills", 0) or 0) - int(t1s.get("dragon_kills", 0) or t1.get("dragon_kills", 0) or 0)
                state["dragon_soul_blue"] = 1.0 if int(t0s.get("dragon_kills", 0) or t0.get("dragon_kills", 0) or 0) >= 4 else 0.0
                state["herald_blue"] = 1.0 if int(t0s.get("herald_kill", 0) or t0s.get("rift_heralds", 0) or 0) > 0 else 0.0
                state["inhib_down_diff"] = float(int(t0s.get("inhibitor_kills", 0) or 0) - int(t1s.get("inhibitor_kills", 0) or 0))
                state["baron_buff_count_diff"] = float(int(t0s.get("baron_kills", 0) or 0) - int(t1s.get("baron_kills", 0) or 0))
                length = current_game.get("length", 0) or 0
                state["game_time_minutes"] = length / 60.0 if length else 0.0
                # Backward-compat keys for event detector
                state["gold_diff"] = gold_a - gold_b
                state["tower_diff"] = state["tower_kills_diff"]
                state["dragon_diff"] = state["dragon_kills_diff"]

        elif game == "cs2":
            # CS2: extract round scores + economy + map/side features
            teams = current_game.get("teams", [])
            if len(teams) >= 2:
                t0 = teams[0] if isinstance(teams[0], dict) else {}
                t1 = teams[1] if isinstance(teams[1], dict) else {}
                score_a = int(t0.get("score", 0) or 0)
                score_b = int(t1.get("score", 0) or 0)
                state["round_score_a"] = score_a
                state["round_score_b"] = score_b

                # Economy data (if available from PandaScore live REST)
                money_a = float(t0.get("money", 0) or 0)
                money_b = float(t1.get("money", 0) or 0)
                state["team_a_money"] = money_a
                state["team_b_money"] = money_b
                state["team_a_equip_value"] = float(t0.get("equipment_value", 0) or 0) or money_a
                state["team_b_equip_value"] = float(t1.get("equipment_value", 0) or 0) or money_b

                # Map CT win rate
                map_data = current_game.get("map", {}) or {}
                map_name = str(map_data.get("name", "")).lower() if isinstance(map_data, dict) else ""
                state["map_ct_rate"] = _MAP_SIDE_RATES.get(map_name, 0.50)

                # CT/T side determination
                current_round = score_a + score_b
                first_side = str(t0.get("first_side", "ct")).lower()
                if current_round < 12:
                    state["team_a_is_ct"] = 1.0 if first_side == "ct" else 0.0
                else:
                    state["team_a_is_ct"] = 0.0 if first_side == "ct" else 1.0

                # Loss streaks (tracked between polls)
                prev = self._cs2_prev_scores.get(match_id, (0, 0))
                streaks = self._cs2_loss_streaks.get(match_id, (0, 0))
                if match_id and (score_a, score_b) != prev:
                    new_a = score_a - prev[0]
                    new_b = score_b - prev[1]
                    if new_a > 0 and new_b == 0:
                        streaks = (0, streaks[1] + new_a)
                    elif new_b > 0 and new_a == 0:
                        streaks = (streaks[0] + new_b, 0)
                    else:
                        streaks = (0, 0)
                    self._cs2_prev_scores[match_id] = (score_a, score_b)
                    self._cs2_loss_streaks[match_id] = streaks
                state["team_a_loss_streak"] = float(streaks[0])
                state["team_b_loss_streak"] = float(streaks[1])

                # Defaults for features not available from REST polling
                state["bomb_planted"] = 0.0
                state["team_a_alive"] = 5.0
                state["team_b_alive"] = 5.0

        return state

    def _get_cached_team_strength_diff(
        self, team_a_id: int, team_b_id: int, game: str
    ) -> float:
        """Return team_strength_diff from Glicko-2 (preferred) or win-rate cache."""
        if not team_a_id or not team_b_id:
            return 0.0

        # Try Glicko-2 first
        tracker = self._glicko2_trackers.get(game)
        if tracker is not None:
            try:
                rating_a = tracker.get_rating(str(team_a_id))
                rating_b = tracker.get_rating(str(team_b_id))
                # Only use if both teams have been rated (phi < default 350)
                if rating_a.phi < 350.0 and rating_b.phi < 350.0:
                    return tracker.strength_diff(str(team_a_id), str(team_b_id))
            except Exception:
                pass

        # Fallback: raw win rate
        wr_a = self._team_strength_cache.get(f"{game}:{team_a_id}", 0.5)
        wr_b = self._team_strength_cache.get(f"{game}:{team_b_id}", 0.5)
        return wr_a - wr_b

    def is_stale(self, match_id: str, threshold_s: int = 0) -> bool:
        """Return True if a running match has had no score change for threshold_s seconds."""
        if threshold_s <= 0:
            threshold_s = int(getattr(settings, "ESPORTS_STALE_MATCH_SECONDS", 1800))
        last = self._last_score_update.get(match_id)
        if last is None:
            return False  # Unknown match — not stale
        return (time.monotonic() - last) > threshold_s

    def set_glicko2_tracker(self, game: str, tracker) -> None:
        """Inject a Glicko2Tracker for live inference (called by bot after training)."""
        self._glicko2_trackers[game] = tracker

    async def populate_team_strength(self, team_id: int, game: str) -> None:
        """Fetch and cache a team's win rate from PandaScore. Called by bot during scan."""
        cache_key = f"{game}:{team_id}"
        if cache_key in self._team_strength_cache:
            return
        if not self._ps or not team_id:
            return
        try:
            stats = await self._ps.get_team_stats(team_id, game)
            if stats and isinstance(stats, dict):
                wins = int(stats.get("wins", 0) or 0)
                losses = int(stats.get("losses", 0) or 0)
                total = wins + losses
                if total >= 5:
                    self._team_strength_cache[cache_key] = wins / total
                elif "winrate" in stats:
                    wr = float(stats.get("winrate", 0.5) or 0.5)
                    if 0.0 < wr <= 1.0:
                        self._team_strength_cache[cache_key] = wr
        except Exception as exc:
            logger.debug("EsportsGameMonitor: team strength fetch failed", team_id=team_id, error=str(exc))
