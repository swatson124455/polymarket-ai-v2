"""
Esports Event Detector — classifies game state updates into betting signals.

Mirrors sports/live/event_detector.py pattern. Per-game thresholds configurable.

Event types:
  LoL: baron_take, elder_dragon, team_wipe, gold_lead, tower_advantage
  CS2: economy_break, round_streak, map_clinch
  General: comeback_threshold, blowout

Usage::
    detector = EsportsEventDetector()
    events = detector.detect(game_state)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from structlog import get_logger

logger = get_logger()


@dataclass
class EsportsLiveEvent:
    """A detected in-game betting signal."""
    match_id: str
    game: str                # lol / cs2 / dota2 / valorant
    event_type: str          # baron_take / elder_dragon / economy_break / etc.
    description: str
    confidence: float        # 0.0 – 1.0
    map_number: int          # Which map in the series
    edge_estimate: float     # Estimated probability edge (0.0 – 0.30)
    market_side: str         # "YES" (bet on leader winning) or "NO"


class EsportsEventDetector:
    """
    Processes EsportsGameState snapshots and emits EsportsLiveEvent signals.

    Per-game thresholds are configurable via settings.
    """

    def __init__(self) -> None:
        self._triggered: Dict[str, Set[str]] = {}  # match_id → set of triggered event keys
        self._last_state: Dict[str, dict] = {}      # match_id → last game_state for delta detection

    def detect(self, state) -> List[EsportsLiveEvent]:
        """
        Classify an EsportsGameState update and return detected events.

        Args:
            state: EsportsGameState from the game monitor.

        Returns:
            List of detected EsportsLiveEvent objects.
        """
        from esports.live.esports_game_monitor import EsportsGameState
        if not isinstance(state, EsportsGameState):
            return []
        if state.status != "running":
            return []

        events = []
        triggered = self._triggered.setdefault(state.match_id, set())
        prev_state = self._last_state.get(state.match_id, {})

        if state.game == "lol":
            events.extend(self._detect_lol_events(state, triggered, prev_state))
        elif state.game == "cs2":
            events.extend(self._detect_cs2_events(state, triggered, prev_state))
        else:
            events.extend(self._detect_generic_events(state, triggered))

        # Update last state for delta detection
        self._last_state[state.match_id] = dict(state.game_state)

        return events

    def prune_finished(self, match_id: str) -> None:
        """Clean up tracking data for a finished match."""
        self._triggered.pop(match_id, None)
        self._last_state.pop(match_id, None)

    # ── LoL-specific detection ──────────────────────────────────────────

    def _detect_lol_events(
        self, state, triggered: Set[str], prev: dict
    ) -> List[EsportsLiveEvent]:
        """Detect LoL-specific events: baron, elder, gold lead, towers."""
        from config.settings import settings
        events = []
        gs = state.game_state

        gold_diff = abs(int(gs.get("gold_diff", 0)))
        tower_diff = abs(int(gs.get("tower_diff", 0)))
        dragon_diff = abs(int(gs.get("dragon_diff", 0)))

        gold_threshold = int(getattr(settings, "ESPORTS_LOL_GOLD_DIFF_THRESHOLD", 5000))
        tower_threshold = int(getattr(settings, "ESPORTS_LOL_TOWER_DIFF_THRESHOLD", 3))

        leading = state.team_a if int(gs.get("gold_diff", 0)) > 0 else state.team_b

        # Gold lead event
        key = f"gold_lead_{state.current_map}"
        if gold_diff >= gold_threshold and key not in triggered:
            events.append(EsportsLiveEvent(
                match_id=state.match_id,
                game="lol",
                event_type="gold_lead",
                description=f"{leading} has {gold_diff} gold lead on map {state.current_map}",
                confidence=min(0.85, 0.60 + gold_diff / 20000),
                map_number=state.current_map,
                edge_estimate=min(0.15, gold_diff / 30000),
                market_side="YES",
            ))
            triggered.add(key)

        # Tower advantage
        key = f"tower_advantage_{state.current_map}"
        if tower_diff >= tower_threshold and key not in triggered:
            events.append(EsportsLiveEvent(
                match_id=state.match_id,
                game="lol",
                event_type="tower_advantage",
                description=f"{leading} has {tower_diff} tower advantage on map {state.current_map}",
                confidence=min(0.80, 0.55 + tower_diff * 0.05),
                map_number=state.current_map,
                edge_estimate=min(0.12, tower_diff * 0.03),
                market_side="YES",
            ))
            triggered.add(key)

        # Baron take (detected via gold spike — PandaScore may provide objective data)
        prev_gold = abs(int(prev.get("gold_diff", 0)))
        if gold_diff - prev_gold >= 3000 and gold_diff > gold_threshold:
            key = f"baron_take_{state.current_map}_{gold_diff}"
            if key not in triggered:
                events.append(EsportsLiveEvent(
                    match_id=state.match_id,
                    game="lol",
                    event_type="baron_take",
                    description=f"Suspected baron take by {leading} (gold spike +{gold_diff - prev_gold})",
                    confidence=0.75,
                    map_number=state.current_map,
                    edge_estimate=0.10,
                    market_side="YES",
                ))
                triggered.add(key)

        return events

    # ── CS2-specific detection ──────────────────────────────────────────

    def _detect_cs2_events(
        self, state, triggered: Set[str], prev: dict
    ) -> List[EsportsLiveEvent]:
        """Detect CS2-specific events: round streak, economy break, map clinch."""
        from config.settings import settings
        events = []
        gs = state.game_state

        round_a = int(gs.get("round_score_a", 0))
        round_b = int(gs.get("round_score_b", 0))
        round_diff = abs(round_a - round_b)
        round_threshold = int(getattr(settings, "ESPORTS_CS2_ROUND_DIFF_THRESHOLD", 5))

        leading = state.team_a if round_a > round_b else state.team_b

        # Round streak (5+ consecutive round wins)
        prev_a = int(prev.get("round_score_a", 0))
        prev_b = int(prev.get("round_score_b", 0))

        # Large round differential
        key = f"round_lead_{state.current_map}"
        if round_diff >= round_threshold and key not in triggered:
            events.append(EsportsLiveEvent(
                match_id=state.match_id,
                game="cs2",
                event_type="round_streak",
                description=f"{leading} has {round_diff} round lead ({round_a}-{round_b}) on map {state.current_map}",
                confidence=min(0.85, 0.55 + round_diff * 0.04),
                map_number=state.current_map,
                edge_estimate=min(0.15, round_diff * 0.025),
                market_side="YES",
            ))
            triggered.add(key)

        # Map clinch point: one team at 12+ rounds
        if max(round_a, round_b) >= 12 and round_diff >= 3:
            key = f"map_clinch_{state.current_map}"
            if key not in triggered:
                events.append(EsportsLiveEvent(
                    match_id=state.match_id,
                    game="cs2",
                    event_type="map_clinch",
                    description=f"{leading} approaching map clinch ({round_a}-{round_b})",
                    confidence=min(0.90, 0.70 + round_diff * 0.04),
                    map_number=state.current_map,
                    edge_estimate=min(0.20, 0.08 + round_diff * 0.03),
                    market_side="YES",
                ))
                triggered.add(key)

        return events

    # ── Generic detection ───────────────────────────────────────────────

    def _detect_generic_events(
        self, state, triggered: Set[str]
    ) -> List[EsportsLiveEvent]:
        """Generic events for Dota 2, Valorant, etc."""
        events = []

        # Series blowout: one team up 2-0 in BO3
        if state.best_of >= 3 and state.score_diff >= 2:
            key = f"series_blowout_{state.match_id}"
            if key not in triggered:
                leading = state.leading_team
                events.append(EsportsLiveEvent(
                    match_id=state.match_id,
                    game=state.game,
                    event_type="blowout",
                    description=f"{leading} leads {state.score_maps_a}-{state.score_maps_b} in BO{state.best_of}",
                    confidence=0.85,
                    map_number=state.current_map,
                    edge_estimate=0.10,
                    market_side="YES",
                ))
                triggered.add(key)

        return events
