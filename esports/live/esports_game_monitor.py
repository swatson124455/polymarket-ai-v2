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

logger = get_logger()

_BASE_BACKOFF = 30
_MAX_BACKOFF = 300
_POLL_INTERVAL = 15  # seconds between polls


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
        for game in ("lol", "cs2", "dota2", "valorant"):
            try:
                matches = await self._ps.get_live_matches(game=game)
                for match in matches:
                    state = self._parse_match_to_state(match, game)
                    if state and state.status == "running":
                        self._active_games[state.match_id] = state
                        try:
                            self._queue.put_nowait(state)
                        except asyncio.QueueFull:
                            logger.debug(
                                "EsportsGameMonitor: queue full — dropping",
                                match_id=state.match_id,
                            )
                    elif state and state.match_id in self._active_games:
                        if state.status in ("finished", "canceled"):
                            del self._active_games[state.match_id]
            except Exception as exc:
                logger.debug("EsportsGameMonitor: poll error", game=game, error=str(exc))

        if self._active_games:
            logger.debug(
                "EsportsGameMonitor: active matches",
                count=len(self._active_games),
                match_ids=list(self._active_games.keys())[:10],
            )

        await asyncio.sleep(_POLL_INTERVAL)

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
            game_state = self._extract_game_state(match.raw, game)

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

    def _extract_game_state(self, raw: Dict[str, Any], game: str) -> Dict[str, Any]:
        """Extract game-specific state from PandaScore raw match data."""
        state: Dict[str, Any] = {}
        games_list = raw.get("games", [])
        if not games_list:
            return state

        # Get the current (last) game in the series
        current_game = games_list[-1] if games_list else {}

        if game == "lol":
            # LoL-specific: extract gold diff, towers, dragons from game data
            teams = current_game.get("teams", [])
            if len(teams) >= 2:
                state["gold_a"] = int(teams[0].get("gold_earned", 0) or 0)
                state["gold_b"] = int(teams[1].get("gold_earned", 0) or 0)
                state["gold_diff"] = state["gold_a"] - state["gold_b"]
                state["tower_diff"] = int(teams[0].get("tower_kills", 0) or 0) - int(teams[1].get("tower_kills", 0) or 0)
                state["dragon_diff"] = int(teams[0].get("dragon_kills", 0) or 0) - int(teams[1].get("dragon_kills", 0) or 0)

        elif game == "cs2":
            # CS2-specific: extract round scores, economy
            teams = current_game.get("teams", [])
            if len(teams) >= 2:
                state["round_score_a"] = int(teams[0].get("score", 0) or 0)
                state["round_score_b"] = int(teams[1].get("score", 0) or 0)

        return state
