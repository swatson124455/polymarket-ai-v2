"""
Live Game Monitor — WebSocket push feed from SportsDataIO.

Maintains a live game state dict per game_id, updated by real-time
push events from the SportsDataIO WebSocket API.

Reconnect strategy: 30×2^n capped at 300s (same as whale_tracker.py).

Each update is placed onto an asyncio.Queue for event_detector to consume.

Gated: requires SPORTS_DATA_IO_API_KEY and BOT_ENABLED_SPORTS_LIVE=true.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from structlog import get_logger

logger = get_logger()

_BASE_BACKOFF = 30
_MAX_BACKOFF = 300


@dataclass
class GameState:
    """Current snapshot of a live game."""
    game_id: str
    sport: str
    home_team: str = ""
    away_team: str = ""
    score_home: int = 0
    score_away: int = 0
    elapsed_pct: float = 0.0       # 0.0 → 1.0 (fraction of game elapsed)
    period: int = 0
    status: str = "scheduled"      # scheduled / live / final / postponed
    last_updated: float = field(default_factory=time.monotonic)
    raw_events: list = field(default_factory=list)   # recent play-by-play events

    @property
    def score_diff(self) -> int:
        return abs(self.score_home - self.score_away)

    @property
    def leading_team(self) -> str:
        if self.score_home > self.score_away:
            return self.home_team
        elif self.score_away > self.score_home:
            return self.away_team
        return ""


class GameMonitor:
    """
    SportsDataIO WebSocket monitor for live game state.

    Usage::
        monitor = GameMonitor(update_queue)
        asyncio.create_task(monitor.run_forever())

    Update queue items are GameState objects.
    """

    def __init__(self, update_queue: asyncio.Queue) -> None:
        self._queue = update_queue
        self._running = False
        self._consecutive_failures = 0
        self._active_games: Dict[str, GameState] = {}
        # I28: Persistent HTTP client — created once in _monitor_live_games, closed in stop()
        # Avoids 400-600ms TCP handshake overhead per 30s poll cycle
        self._http_client = None
        # SportsDataIO WebSocket endpoints per sport
        self._ws_endpoints = {
            "nba": "wss://api.sportsdata.io/v3/nba/pbp/json/PlayByPlay",
            "nfl": "wss://api.sportsdata.io/v3/nfl/pbp/json/PlayByPlay",
            "mlb": "wss://api.sportsdata.io/v3/mlb/pbp/json/PlayByPlay",
            "nhl": "wss://api.sportsdata.io/v3/nhl/pbp/json/PlayByPlay",
        }

    @property
    def active_games(self) -> Dict[str, GameState]:
        return dict(self._active_games)

    async def run_forever(self) -> None:
        """Connect to SportsDataIO WebSocket feeds indefinitely."""
        from config.settings import settings

        api_key = getattr(settings, "SPORTS_DATA_IO_API_KEY", None)
        if not api_key:
            logger.info(
                "GameMonitor: no SPORTS_DATA_IO_API_KEY — monitor inactive",
                hint="Set SPORTS_DATA_IO_API_KEY in .env",
            )
            return

        self._running = True
        logger.info("GameMonitor: starting live game monitor")

        while self._running:
            try:
                await self._monitor_live_games(api_key)
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                logger.info("GameMonitor: cancelled")
                break
            except Exception as exc:
                self._consecutive_failures += 1
                backoff = min(
                    _BASE_BACKOFF * (2 ** (self._consecutive_failures - 1)),
                    _MAX_BACKOFF,
                )
                logger.warning(
                    "GameMonitor: error — reconnecting",
                    error=str(exc),
                    backoff_s=backoff,
                    consecutive_failures=self._consecutive_failures,
                )
                await asyncio.sleep(backoff)

    async def stop(self) -> None:
        """Signal the monitor to stop and close the persistent HTTP client."""
        self._running = False
        # I28: Close persistent HTTP client on stop
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None

    # ─── Implementation ───────────────────────────────────────────────────────

    async def _monitor_live_games(self, api_key: str) -> None:
        """
        Poll SportsDataIO REST API for live game scores and build GameState updates.

        Note: SportsDataIO WebSocket push feeds require a higher tier subscription.
        This implementation uses polling (HTTP GET every 30s) as a fallback that
        works with Standard tier ($150/mo). Upgrade to Enterprise for true WebSocket push.
        """
        from config.settings import settings
        import httpx

        base_url = getattr(settings, "SPORTS_DATA_IO_BASE_URL", "https://api.sportsdata.io/v3")
        poll_interval = 30  # seconds between polls

        sport_endpoints = {
            "nba": f"{base_url}/nba/scores/json/GamesByDate/{{date}}?key={api_key}",
            "nfl": f"{base_url}/nfl/scores/json/ScoresByDate/{{date}}?key={api_key}",
            "mlb": f"{base_url}/mlb/scores/json/GamesByDate/{{date}}?key={api_key}",
            "nhl": f"{base_url}/nhl/scores/json/GamesByDate/{{date}}?key={api_key}",
        }

        # I28: Create one persistent client for the lifetime of _monitor_live_games
        import datetime as _dt
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=3.0)
            )

        logger.info("GameMonitor: polling live games", poll_interval_s=poll_interval)

        while self._running:
            # I26: Recompute today inside the poll loop — survives midnight boundary
            today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

            for sport, url_template in sport_endpoints.items():
                try:
                    url = url_template.format(date=today)
                    resp = await self._http_client.get(url)
                    if resp.status_code != 200:
                        continue
                    games = resp.json()
                    if not isinstance(games, list):
                        continue

                    for game in games:
                        game_state = self._parse_game(game, sport)
                        if game_state and game_state.status == "live":
                            self._active_games[game_state.game_id] = game_state
                            try:
                                self._queue.put_nowait(game_state)
                            except asyncio.QueueFull:
                                logger.debug(
                                    "GameMonitor: update queue full — dropping game state",
                                    game_id=game_state.game_id,
                                )
                        elif game_state and game_state.game_id in self._active_games:
                            # I27: Remove postponed/cancelled games — not just final
                            if game_state.status in ("final", "postponed", "cancelled"):
                                del self._active_games[game_state.game_id]
                                logger.debug(
                                    "GameMonitor: game removed from active",
                                    game_id=game_state.game_id,
                                    status=game_state.status,
                                )

                except Exception as exc:
                    logger.debug("GameMonitor: sport poll error", sport=sport, error=str(exc))

            if self._active_games:
                logger.debug(
                    "GameMonitor: active games",
                    count=len(self._active_games),
                    game_ids=list(self._active_games.keys()),
                )

            await asyncio.sleep(poll_interval)

    def _parse_game(self, raw: Dict[str, Any], sport: str) -> Optional[GameState]:
        """Parse a SportsDataIO game JSON blob into a GameState."""
        try:
            game_id = str(raw.get("GameID", raw.get("GameId", "")))
            if not game_id:
                return None

            status_raw = str(raw.get("Status", "")).lower()
            if "progress" in status_raw or "inprogress" in status_raw or "live" in status_raw:
                status = "live"
            elif "final" in status_raw or "complete" in status_raw:
                status = "final"
            elif "postpone" in status_raw or "cancel" in status_raw:
                status = "postponed"
            else:
                status = "scheduled"

            # Score parsing differs slightly per sport
            score_home = int(raw.get("HomeScore", raw.get("HomeTeamScore", 0)) or 0)
            score_away = int(raw.get("AwayScore", raw.get("AwayTeamScore", 0)) or 0)

            # Elapsed percentage (sport-specific)
            elapsed_pct = 0.0
            if sport == "nba":
                quarter = int(raw.get("Quarter", 0) or 0)
                minutes_remaining = float(raw.get("TimeRemainingMinutes", 12) or 12)
                # 4 quarters, 12 min each = 48 min total
                if quarter > 0:
                    elapsed_pct = ((quarter - 1) * 12 + (12 - minutes_remaining)) / 48.0
            elif sport == "nfl":
                quarter = int(raw.get("Quarter", 0) or 0)
                if quarter > 0:
                    elapsed_pct = min(1.0, quarter / 4.0)
            elif sport == "mlb":
                inning = int(raw.get("Inning", 0) or 0)
                elapsed_pct = min(1.0, inning / 9.0)
            elif sport == "nhl":
                period = int(raw.get("Period", 0) or 0)
                elapsed_pct = min(1.0, period / 3.0)

            return GameState(
                game_id=game_id,
                sport=sport,
                home_team=str(raw.get("HomeTeam", "")),
                away_team=str(raw.get("AwayTeam", "")),
                score_home=score_home,
                score_away=score_away,
                elapsed_pct=min(1.0, max(0.0, elapsed_pct)),
                period=int(raw.get("Quarter", raw.get("Period", raw.get("Inning", 0))) or 0),
                status=status,
            )
        except Exception as exc:
            logger.debug("GameMonitor: parse error", error=str(exc))
            return None
