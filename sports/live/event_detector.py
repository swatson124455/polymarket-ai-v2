"""
Live Event Detector — classifies game state updates into betting signals.

Detectors:
  - BlowoutDetector:    score_diff exceeds threshold at late game stage
  - PlayerGoingOff:     star player going off (high stats in short time)
  - MomentumShift:      run of consecutive scoring by one team

Thresholds (env-configurable via settings):
  NBA: score_diff > 20 at elapsed_pct > 60%
  NFL: score_diff > 17 (3 scores) at elapsed > 75%
  Soccer: goal_diff > 2 at elapsed > 70%
  Tennis: (handled by withdrawal in news pipeline)
  NHL: score_diff > 3 at elapsed > 70%
  MLB: run_diff > 7 at elapsed (innings) > 67%

Returns a list of LiveEvent objects per GameState update.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from structlog import get_logger

from sports.live.game_monitor import GameState

logger = get_logger()


@dataclass
class LiveEvent:
    """A detected in-game betting signal."""
    game_id: str
    sport: str
    event_type: str          # blowout / momentum_shift / high_value_player_out
    description: str
    confidence: float        # 0.0 – 1.0
    elapsed_pct: float
    score_diff: int
    market_side: str         # "YES" (bet on leader winning) or "NO" (against loser)
    edge_estimate: float     # estimated probability edge (0.0 – 0.30)


class EventDetector:
    """
    Processes GameState snapshots and emits LiveEvent signals.

    Usage::
        detector = EventDetector()
        events = detector.detect(game_state)
        for event in events:
            # place bet based on event
    """

    def __init__(self) -> None:
        self._triggered_games: Dict[str, set] = {}   # game_id → set of triggered event_types
        # I25: Track last blowout score_diff per game so we can re-trigger when lead grows ≥5 more
        self._last_blowout_diff: Dict[str, int] = {}  # game_id → score_diff at last blowout signal

    def detect(self, state: GameState) -> List[LiveEvent]:
        """
        Classify a GameState update and return detected LiveEvents.

        Blowout: re-triggers when score_diff grows by ≥5 points beyond last trigger (I25).
        Momentum: one trigger per game (unchanged).
        """
        if state.status != "live":
            return []

        events = []
        triggered = self._triggered_games.setdefault(state.game_id, set())

        # I25: Blowout detection — re-trigger if score_diff grows ≥5 beyond last trigger
        blowout = self._check_blowout(state)
        if blowout:
            last_diff = self._last_blowout_diff.get(state.game_id, -1)
            if "blowout" not in triggered or state.score_diff >= last_diff + 5:
                events.append(blowout)
                triggered.add("blowout")
                self._last_blowout_diff[state.game_id] = state.score_diff

        # Momentum shift detection (one trigger per game — unchanged)
        momentum = self._check_momentum(state)
        if momentum and "momentum_shift" not in triggered:
            events.append(momentum)
            triggered.add("momentum_shift")

        # Clear finished games from tracking
        if state.status in ("final", "postponed", "cancelled"):
            self._triggered_games.pop(state.game_id, None)
            self._last_blowout_diff.pop(state.game_id, None)

        # Periodic prune to prevent unbounded growth
        if len(self._triggered_games) > 200:
            self.prune_stale_games()

        return events

    def reset_game(self, game_id: str) -> None:
        """Clear trigger history for a game (e.g. on overtime)."""
        self._triggered_games.pop(game_id, None)
        self._last_blowout_diff.pop(game_id, None)

    def prune_stale_games(self, max_age_hours: float = 6.0) -> None:
        """Remove tracking data for games not updated in max_age_hours."""
        # Called periodically — since we don't have timestamps here,
        # just cap the dict sizes instead
        _MAX_TRACKED = 200
        if len(self._triggered_games) > _MAX_TRACKED:
            # Remove oldest entries (first inserted)
            keys = list(self._triggered_games.keys())
            for k in keys[:len(keys) - _MAX_TRACKED]:
                self._triggered_games.pop(k, None)
                self._last_blowout_diff.pop(k, None)

    # ─── Blowout Detector ────────────────────────────────────────────────────

    def _check_blowout(self, state: GameState) -> Optional[LiveEvent]:
        """Detect blowout conditions per sport."""
        from config.settings import settings

        sport = state.sport
        diff = state.score_diff
        elapsed = state.elapsed_pct

        if sport == "nba":
            threshold = int(getattr(settings, "SPORTS_NBA_BLOWOUT_THRESHOLD", 20))
            min_elapsed = 0.60
            if diff >= threshold and elapsed >= min_elapsed:
                confidence = min(0.90, 0.65 + (diff - threshold) * 0.01 + (elapsed - min_elapsed) * 0.20)
                edge = min(0.25, 0.10 + (diff - threshold) * 0.005)
                return LiveEvent(
                    game_id=state.game_id,
                    sport=sport,
                    event_type="blowout",
                    description=f"NBA blowout: {state.leading_team} leads by {diff} pts at {elapsed:.0%} elapsed",
                    confidence=confidence,
                    elapsed_pct=elapsed,
                    score_diff=diff,
                    market_side="YES",
                    edge_estimate=edge,
                )

        elif sport == "nfl":
            threshold = int(getattr(settings, "SPORTS_NFL_BLOWOUT_THRESHOLD", 17))
            min_elapsed = 0.75
            if diff >= threshold and elapsed >= min_elapsed:
                confidence = min(0.88, 0.65 + (diff - threshold) * 0.008 + (elapsed - min_elapsed) * 0.15)
                edge = min(0.22, 0.10 + (diff - threshold) * 0.004)
                return LiveEvent(
                    game_id=state.game_id,
                    sport=sport,
                    event_type="blowout",
                    description=f"NFL blowout: {state.leading_team} leads by {diff} pts at {elapsed:.0%} elapsed",
                    confidence=confidence,
                    elapsed_pct=elapsed,
                    score_diff=diff,
                    market_side="YES",
                    edge_estimate=edge,
                )

        elif sport == "soccer":
            threshold = int(getattr(settings, "SPORTS_SOCCER_BLOWOUT_GOALS", 2))
            min_elapsed = 0.70
            if diff >= threshold and elapsed >= min_elapsed:
                confidence = min(0.87, 0.65 + (diff - threshold) * 0.05 + (elapsed - min_elapsed) * 0.20)
                edge = min(0.20, 0.08 + (diff - threshold) * 0.04)
                return LiveEvent(
                    game_id=state.game_id,
                    sport=sport,
                    event_type="blowout",
                    description=f"Soccer blowout: {state.leading_team} leads {diff}-0 at {elapsed:.0%} elapsed",
                    confidence=confidence,
                    elapsed_pct=elapsed,
                    score_diff=diff,
                    market_side="YES",
                    edge_estimate=edge,
                )

        elif sport == "nhl":
            min_elapsed = 0.70
            if diff >= 3 and elapsed >= min_elapsed:
                confidence = min(0.85, 0.65 + (diff - 3) * 0.05 + (elapsed - min_elapsed) * 0.15)
                edge = min(0.18, 0.08 + (diff - 3) * 0.03)
                return LiveEvent(
                    game_id=state.game_id,
                    sport=sport,
                    event_type="blowout",
                    description=f"NHL blowout: {state.leading_team} leads by {diff} at {elapsed:.0%} elapsed",
                    confidence=confidence,
                    elapsed_pct=elapsed,
                    score_diff=diff,
                    market_side="YES",
                    edge_estimate=edge,
                )

        elif sport == "mlb":
            min_elapsed = 0.67  # after 6th inning
            if diff >= 7 and elapsed >= min_elapsed:
                confidence = min(0.88, 0.65 + (diff - 7) * 0.02 + (elapsed - min_elapsed) * 0.15)
                edge = min(0.20, 0.08 + (diff - 7) * 0.012)
                return LiveEvent(
                    game_id=state.game_id,
                    sport=sport,
                    event_type="blowout",
                    description=f"MLB blowout: {state.leading_team} leads by {diff} runs at {elapsed:.0%} elapsed",
                    confidence=confidence,
                    elapsed_pct=elapsed,
                    score_diff=diff,
                    market_side="YES",
                    edge_estimate=edge,
                )

        return None

    # ─── Momentum Shift Detector ─────────────────────────────────────────────

    def _check_momentum(self, state: GameState) -> Optional[LiveEvent]:
        """
        Detect momentum shift: team going on a significant run.

        Uses recent score events in state.raw_events if available.
        Falls back to score diff change between snapshots.
        """
        # Only trigger on significant lead changes mid-game
        if state.elapsed_pct < 0.30 or state.elapsed_pct > 0.90:
            return None
        if state.score_diff < 10:
            return None

        # Without raw play-by-play events, use score_diff as a proxy
        # A large mid-game lead implies momentum is with the leader
        sport_min_diff = {
            "nba": 15, "nfl": 14, "nhl": 2,
            "mlb": 5, "soccer": 2,
        }
        min_diff = sport_min_diff.get(state.sport, 10)
        if state.score_diff < min_diff:
            return None

        # I54: Increase elapsed_pct weight 0.10→0.25; penalize early leads (elapsed < 50%)
        confidence = min(0.78, 0.55 + state.score_diff * 0.01 + state.elapsed_pct * 0.25)
        if state.elapsed_pct < 0.50:
            confidence *= 0.85  # I54: early lead is less reliable → reduce confidence
        edge = min(0.15, 0.05 + state.score_diff * 0.005)

        return LiveEvent(
            game_id=state.game_id,
            sport=state.sport,
            event_type="momentum_shift",
            description=(
                f"Momentum shift: {state.leading_team} on a run, "
                f"leads by {state.score_diff} at {state.elapsed_pct:.0%}"
            ),
            confidence=confidence,
            elapsed_pct=state.elapsed_pct,
            score_diff=state.score_diff,
            market_side="YES",
            edge_estimate=edge,
        )
