"""
Graceful Degradation Manager — fleet-level health and position sizing control.

Implements a percentage-based 5-tier degradation model:

  Tier 0:  ≥85% healthy  → full operation (1.00× sizing)
  Tier 1:  ≥70% healthy  → reduced sizing (0.75×)
  Tier 2:  ≥50% healthy  → half sizing (0.50×), raise confidence threshold
  Tier 3:  ≥25% healthy  → emergency sizing (0.10×), high confidence only
  Tier 4:   <25% healthy → close-only mode (0.00× new positions)

Tiers are percentage-based so the fleet operates correctly regardless of how
many bots are enabled.  With 4 bots all healthy the ratio is 1.0 → Tier 0
(1.00×).  The old absolute-count approach required ≥6 healthy bots for Tier 0,
making full sizing physically unreachable with 4 registered bots.

Each BotStateMachine reports to DegradationManager via the `on_state_change`
callback. On every bot state change, the fleet tier is recomputed and logged.

OrderGateway reads `get_sizing_multiplier()` and `is_close_only_mode()` to
apply fleet-level risk reduction automatically on every order.

Integration:
  1. At startup: degradation_manager.register_bot(bot_name) for each bot
  2. In BaseBot scan loop: machine.record_health_ok() / machine.record_error()
  3. In OrderGateway: multiply kelly_fraction by degradation_manager.get_sizing_multiplier()
"""
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from structlog import get_logger

from bots.weather.engine.base_engine.monitoring.bot_state_machine import BotStateMachine

logger = get_logger()

# ── Degradation tier table ────────────────────────────────────────────────────
# (min_healthy_pct, sizing_multiplier, min_confidence_override, close_only_mode)
# min_healthy_pct: minimum ratio of healthy/registered bots for this tier to apply.
# Percentage-based so the fleet auto-calibrates to the number of enabled bots.
# Examples with 4 bots: 4/4=1.0→Tier0, 3/4=0.75→Tier1, 2/4=0.5→Tier2
# Examples with 7 bots: 6/7=0.857→Tier0, 5/7=0.714→Tier1, 3/7=0.43→Tier3
DEGRADATION_TIERS: List[Tuple[float, float, Optional[float], bool]] = [
    (0.85, 1.00, None,  False),   # Tier 0: ≥85% healthy → full sizing
    (0.70, 0.75, None,  False),   # Tier 1: ≥70% healthy → -25%
    (0.50, 0.50, 0.70,  False),   # Tier 2: ≥50% healthy → -50%, raise confidence
    (0.25, 0.10, 0.85,  False),   # Tier 3: ≥25% healthy → -90%, high confidence only
    (0.00, 0.00, 1.00,  True),    # Tier 4:  <25% healthy → close-only emergency
]


class DegradationManager:
    """
    Fleet-level degradation controller.

    Tracks one BotStateMachine per bot and computes the fleet-wide degradation
    tier based on how many bots are currently in the "healthy" state.

    Usage::

        mgr = DegradationManager()
        machine = mgr.register_bot("EnsembleBot")    # returns BotStateMachine

        # In scan loop:
        machine.record_health_ok()

        # In OrderGateway:
        multiplier = mgr.get_sizing_multiplier("EnsembleBot")
        if mgr.is_close_only_mode():
            # block new entries
    """

    def __init__(self, total_bots: int = 0, order_gateway=None):
        # total_bots is kept for backward-compatibility but is no longer used for
        # tier computation.  The tier is determined dynamically from the ratio of
        # healthy registered bots (len of _bot_machines with state=="healthy").
        self.total_bots = total_bots
        self.order_gateway = order_gateway   # Optional: set after construction

        self._bot_machines: Dict[str, BotStateMachine] = {}
        self._current_tier_index: int = 0
        self._tier_entered_at: Optional[datetime] = None
        self._tier_change_count: int = 0

    # ── Bot registration ──────────────────────────────────────────────────────

    def register_bot(self, bot_name: str) -> BotStateMachine:
        """
        Create and register a BotStateMachine for a bot.

        Returns the machine — store it in the bot for scan-cycle reporting.
        """
        machine = BotStateMachine(
            bot_name=bot_name,
            on_state_change=self._on_bot_state_change,
        )
        self._bot_machines[bot_name] = machine
        logger.debug("DegradationManager: registered bot %s", bot_name)
        return machine

    def get_machine(self, bot_name: str) -> Optional[BotStateMachine]:
        """Get the state machine for a specific bot."""
        return self._bot_machines.get(bot_name)

    # ── Internal tier recomputation ───────────────────────────────────────────

    def _on_bot_state_change(self, bot_name: str, new_state: str) -> None:
        """Called by BotStateMachine whenever any bot transitions state."""
        self._recompute_tier()

    def _recompute_tier(self) -> None:
        """Recompute fleet degradation tier. Logs if tier changes."""
        healthy_count = sum(
            1 for m in self._bot_machines.values() if m.state == "healthy"
        )
        registered = len(self._bot_machines)

        # Percentage-based comparison: health_ratio = healthy / registered.
        # Falls back to 1.0 (full) when no bots are registered yet so the fleet
        # starts at Tier 0 and degrades only as bots are added and fail.
        health_ratio = healthy_count / max(1, registered)

        # Find the best (lowest index = best sizing) tier whose threshold is met.
        new_tier_index = len(DEGRADATION_TIERS) - 1  # Default to emergency
        for i, (threshold_pct, _, _, _) in enumerate(DEGRADATION_TIERS):
            if health_ratio >= threshold_pct:
                new_tier_index = i
                break

        if new_tier_index != self._current_tier_index:
            old_tier = DEGRADATION_TIERS[self._current_tier_index]
            new_tier = DEGRADATION_TIERS[new_tier_index]
            self._tier_change_count += 1
            prev_index = self._current_tier_index
            self._current_tier_index = new_tier_index
            self._tier_entered_at = datetime.now(timezone.utc)

            log_fn = logger.warning if new_tier_index > prev_index else logger.info
            log_fn(
                "Fleet degradation tier: %d→%d (healthy=%d/%d=%.0f%%, sizing=%.0f%%→%.0f%%)",
                prev_index, new_tier_index,
                healthy_count, registered, health_ratio * 100,
                old_tier[1] * 100, new_tier[1] * 100,
            )

    # ── Query interface ───────────────────────────────────────────────────────

    def get_sizing_multiplier(self, bot_name: Optional[str] = None) -> float:
        """
        Get the effective position sizing multiplier.

        Returns min(fleet_multiplier, bot_state_multiplier) so that
        per-bot state machine penalties compound with fleet-level penalties.

        Args:
            bot_name: If provided, also applies per-bot state machine penalty.
        """
        _, fleet_mult, _, _ = DEGRADATION_TIERS[self._current_tier_index]

        if bot_name and bot_name in self._bot_machines:
            bot_mult = self._bot_machines[bot_name].get_position_size_multiplier()
            return min(fleet_mult, bot_mult)
        return fleet_mult

    def get_min_confidence_override(self) -> Optional[float]:
        """
        Return minimum confidence threshold override for this tier, or None.

        When not None, bots should skip trades where signal confidence < this value.
        """
        _, _, min_conf, _ = DEGRADATION_TIERS[self._current_tier_index]
        return min_conf

    def is_close_only_mode(self) -> bool:
        """True if the fleet is in emergency close-only mode (no new positions)."""
        _, _, _, close_only = DEGRADATION_TIERS[self._current_tier_index]
        return close_only

    def get_current_tier(self) -> int:
        """Return current degradation tier index (0=full, 4=emergency)."""
        return self._current_tier_index

    def get_fleet_status(self) -> Dict[str, Any]:
        """Full fleet health snapshot for dashboard and logging."""
        healthy = [n for n, m in self._bot_machines.items() if m.state == "healthy"]
        degraded = [n for n, m in self._bot_machines.items() if m.state == "degraded"]
        recovering = [n for n, m in self._bot_machines.items() if m.state == "recovering"]
        failed = [n for n, m in self._bot_machines.items() if m.state in ("failed", "safe_mode")]
        tier = DEGRADATION_TIERS[self._current_tier_index]

        return {
            "degradation_tier": self._current_tier_index,
            "tier_change_count": self._tier_change_count,
            "tier_entered_at": self._tier_entered_at.isoformat() if self._tier_entered_at else None,
            "healthy_bots": healthy,
            "degraded_bots": degraded,
            "recovering_bots": recovering,
            "failed_bots": failed,
            "healthy_count": len(healthy),
            "registered_bots": len(self._bot_machines),
            "sizing_multiplier": tier[1],
            "min_confidence_override": tier[2],
            "close_only_mode": tier[3],
            "bot_states": {n: m.state for n, m in self._bot_machines.items()},
            "bot_details": {n: m.to_dict() for n, m in self._bot_machines.items()},
        }

    def force_safe_mode(self, reason: str = "manual") -> None:
        """
        Force all bots into safe_mode (emergency stop).
        Used by PortfolioDrawdownBreaker when circuit trips.
        """
        logger.error("DegradationManager: forcing ALL bots to safe_mode (%s)", reason)
        for bot_name, machine in self._bot_machines.items():
            if machine.state not in ("safe_mode", "failed"):
                try:
                    machine.enter_safe_mode()
                except Exception:
                    pass  # ignore_invalid_triggers handles already-in-state
