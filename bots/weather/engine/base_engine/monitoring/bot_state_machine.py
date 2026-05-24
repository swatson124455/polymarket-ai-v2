"""
Bot Health State Machine — formal lifecycle management using `transitions`.

States: healthy → degraded → failed → recovering → healthy
        healthy/degraded → safe_mode (emergency branch)
        safe_mode → recovering → healthy

Guards prevent invalid transitions (e.g., recovering → healthy while DB still down).
Callbacks notify the DegradationManager on every state change.

Position sizing multipliers per state:
  healthy    → 1.00  (full size)
  degraded   → 0.50  (half size)
  recovering → 0.25  (quarter size)
  failed     → 0.00  (no new positions)
  safe_mode  → 0.00  (no new positions, position-reducing allowed)
"""
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime, timezone
from structlog import get_logger

try:
    from transitions import Machine
    TRANSITIONS_AVAILABLE = True
except ImportError:
    TRANSITIONS_AVAILABLE = False

logger = get_logger()

# ── State definitions ─────────────────────────────────────────────────────────

STATES: List[str] = ["healthy", "degraded", "failed", "recovering", "safe_mode"]

TRANSITIONS_DEF = [
    # Normal degradation (healthy → degraded)
    {"trigger": "degrade",               "source": "healthy",              "dest": "degraded",   "after": "_on_degraded"},
    # Re-entry: degraded can stay degraded (accumulate errors)
    {"trigger": "degrade",               "source": "degraded",             "dest": "degraded"},
    # Fatal crash
    {"trigger": "fail",                  "source": ["healthy", "degraded"], "dest": "failed",     "after": "_on_failed"},
    {"trigger": "fail",                  "source": "failed",               "dest": "failed"},    # idempotent
    # Start recovery
    {"trigger": "start_recover",         "source": ["failed", "safe_mode"], "dest": "recovering", "after": "_on_recovering"},
    # Full recovery — guarded: requires 3 consecutive health OKs
    {"trigger": "recover",               "source": "recovering",           "dest": "healthy",
     "conditions": "_is_health_ok",      "after": "_on_recovered"},
    # Partial recovery: recovering → degraded (health checks improving but not fully clear)
    {"trigger": "degrade_again",         "source": "recovering",           "dest": "degraded",   "after": "_on_degraded"},
    # Fast-track recovery from degraded (minor issues clear themselves)
    {"trigger": "recover_from_degraded", "source": "degraded",            "dest": "healthy",
     "conditions": "_is_health_ok",      "after": "_on_recovered"},
    # Safe mode: emergency stop (P&L breach, kill switch, manual)
    {"trigger": "enter_safe_mode",       "source": ["healthy", "degraded"], "dest": "safe_mode",  "after": "_on_safe_mode"},
    {"trigger": "enter_safe_mode",       "source": "safe_mode",            "dest": "safe_mode"},  # idempotent
]


class BotStateMachine:
    """
    Per-bot health state machine.

    Attach one to each BaseBot instance to get formal health tracking with guards.
    The DegradationManager receives callbacks on every state change and recomputes
    fleet-level position sizing multipliers.

    Usage::

        machine = degradation_manager.register_bot("EnsembleBot")

        # In scan loop — on success:
        machine.record_health_ok()

        # On error:
        machine.record_error(is_fatal=False)

        # Get sizing multiplier to apply to kelly_fraction:
        mult = machine.get_position_size_multiplier()
    """

    def __init__(
        self,
        bot_name: str,
        health_check_fn: Optional[Callable] = None,
        on_state_change: Optional[Callable[[str, str], None]] = None,
    ):
        self.bot_name = bot_name
        self._health_check_fn = health_check_fn        # Optional async fn() -> bool
        self._on_state_change_cb = on_state_change      # Called on every transition

        # State counters
        self._failure_count = 0
        self._consecutive_health_ok = 0
        self._state_entered_at: Dict[str, datetime] = {}
        self._transition_log: list = []                  # Last 20 transitions

        if TRANSITIONS_AVAILABLE:
            self.machine = Machine(
                model=self,
                states=STATES,
                transitions=TRANSITIONS_DEF,
                initial="healthy",
                ignore_invalid_triggers=False,  # I50: raise on invalid — we catch and log below
                queued=False,                   # synchronous (not thread-queued)
            )
        else:
            # Graceful fallback: expose .state attribute directly
            logger.warning(
                "transitions library not available — BotStateMachine using simplified fallback",
                bot_name=bot_name,
            )
            self.state = "healthy"

    # ── Guards ────────────────────────────────────────────────────────────────

    def _is_health_ok(self) -> bool:
        """Guard: only transition to healthy after 3 consecutive clean cycles."""
        return self._consecutive_health_ok >= 3

    # ── Transition callbacks ──────────────────────────────────────────────────

    def _on_degraded(self) -> None:
        self._state_entered_at["degraded"] = datetime.now(timezone.utc)
        self._log_transition("degraded")
        logger.warning("Bot state → DEGRADED", bot_name=self.bot_name,
                       failure_count=self._failure_count)
        if self._on_state_change_cb:
            self._on_state_change_cb(self.bot_name, "degraded")

    def _on_failed(self) -> None:
        self._failure_count += 1
        self._consecutive_health_ok = 0
        self._state_entered_at["failed"] = datetime.now(timezone.utc)
        self._log_transition("failed")
        logger.error("Bot state → FAILED (total failures: %d)", self._failure_count,
                     bot_name=self.bot_name)
        if self._on_state_change_cb:
            self._on_state_change_cb(self.bot_name, "failed")

    def _on_recovering(self) -> None:
        self._state_entered_at["recovering"] = datetime.now(timezone.utc)
        self._log_transition("recovering")
        logger.info("Bot state → RECOVERING", bot_name=self.bot_name)
        if self._on_state_change_cb:
            self._on_state_change_cb(self.bot_name, "recovering")

    def _on_recovered(self) -> None:
        self._consecutive_health_ok = 0   # Reset after successful recovery
        self._state_entered_at["healthy"] = datetime.now(timezone.utc)
        self._log_transition("healthy")
        logger.info("Bot state → HEALTHY (recovered from failure #%d)", self._failure_count,
                    bot_name=self.bot_name)
        if self._on_state_change_cb:
            self._on_state_change_cb(self.bot_name, "healthy")

    def _on_safe_mode(self) -> None:
        self._consecutive_health_ok = 0
        self._state_entered_at["safe_mode"] = datetime.now(timezone.utc)
        self._log_transition("safe_mode")
        logger.warning("Bot state → SAFE_MODE (position sizing zeroed)", bot_name=self.bot_name)
        if self._on_state_change_cb:
            self._on_state_change_cb(self.bot_name, "safe_mode")

    def _log_transition(self, dest: str) -> None:
        entry = {"state": dest, "at": datetime.now(timezone.utc).isoformat()}
        self._transition_log.append(entry)
        if len(self._transition_log) > 20:
            self._transition_log.pop(0)

    # ── Fallback state management (when transitions not available) ─────────────

    def _set_state_fallback(self, new_state: str) -> None:
        """Simple state setter used when transitions library is missing."""
        self.state = new_state
        if self._on_state_change_cb:
            self._on_state_change_cb(self.bot_name, new_state)

    # ── External interface ────────────────────────────────────────────────────

    def _safe_trigger(self, trigger_name: str) -> bool:
        """I50: Call a transitions trigger; log WARNING if the transition is blocked/invalid."""
        try:
            return bool(getattr(self, trigger_name)())
        except Exception as exc:
            # transitions raises MachineError for invalid triggers when ignore_invalid_triggers=False.
            # Log a WARNING so ops can see unexpected state machine paths.
            logger.warning(
                "BotStateMachine: blocked/invalid transition",
                bot_name=self.bot_name,
                trigger=trigger_name,
                current_state=self.state,
                error=str(exc),
            )
            return False

    def record_health_ok(self) -> None:
        """Call on each successful scan cycle with no errors."""
        self._consecutive_health_ok += 1

        if not TRANSITIONS_AVAILABLE:
            if self.state == "failed" and self._consecutive_health_ok >= 3:
                self._set_state_fallback("healthy")
            elif self.state == "degraded" and self._consecutive_health_ok >= 3:
                self._set_state_fallback("healthy")
            elif self.state == "safe_mode" and self._consecutive_health_ok >= 5:
                # Auto-recover from safe_mode after 5 consecutive clean scans.
                # safe_mode is set by drawdown breaker; if the underlying issue is resolved
                # (e.g. bad equity formula fixed), bots should be able to return to healthy.
                self._set_state_fallback("recovering")
            return

        current = self.state
        if current == "degraded" and self._consecutive_health_ok >= 3:
            self._safe_trigger("recover_from_degraded")
        elif current == "recovering" and self._consecutive_health_ok >= 3:
            self._safe_trigger("recover")
        elif current == "safe_mode" and self._consecutive_health_ok >= 5:
            # Transition safe_mode → recovering. After 3 more clean scans → healthy.
            # Conservative threshold (5 vs 3) to avoid premature recovery from genuine emergencies.
            self._safe_trigger("start_recover")

    def record_error(self, is_fatal: bool = False, exception: Optional[Exception] = None) -> None:
        """Call on scan cycle error. is_fatal=True forces immediate failed state."""
        self._consecutive_health_ok = 0

        if not TRANSITIONS_AVAILABLE:
            if is_fatal or self._failure_count >= 3:
                self._failure_count += 1
                self._set_state_fallback("failed")
            else:
                self._set_state_fallback("degraded")
            return

        if is_fatal or self._failure_count >= 3:
            self._safe_trigger("fail")
        else:
            self._safe_trigger("degrade")

    def get_position_size_multiplier(self) -> float:
        """Return sizing multiplier for the current state (0.0 = no new positions)."""
        return {
            "healthy":   1.00,
            "degraded":  0.50,
            "recovering": 0.25,
            "failed":    0.00,
            "safe_mode": 0.00,
        }.get(self.state, 0.0)

    def is_trading_allowed(self) -> bool:
        """True if this bot is allowed to open new positions."""
        return self.state in ("healthy", "degraded", "recovering")

    def to_dict(self) -> Dict[str, Any]:
        """Serializable snapshot for dashboard/DB writes."""
        return {
            "bot_name": self.bot_name,
            "state": self.state,
            "failure_count": self._failure_count,
            "consecutive_health_ok": self._consecutive_health_ok,
            "sizing_multiplier": self.get_position_size_multiplier(),
            "state_entered_at": {
                k: v.isoformat() for k, v in self._state_entered_at.items()
            },
            "recent_transitions": self._transition_log[-5:],
        }
