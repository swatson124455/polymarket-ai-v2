"""
Portfolio Drawdown Circuit Breaker — high-water-mark P&L protection.

Trips when either:
  1. Daily P&L loss exceeds daily_loss_limit_pct (e.g., -5% from day open)
  2. Drawdown from peak exceeds drawdown_from_peak_pct (e.g., -10% from high-water mark)
  3. Manual trigger from external risk system

Once tripped:
  - Blocks all new risk-INCREASING orders (any BUY / new position)
  - Allows risk-REDUCING orders (SELL / close position) per FIA best practices
  - Auto-resets after cooldown_seconds if equity recovers above threshold

High-water-mark algorithm:
  - Peak equity only moves upward, never resets on a bad day
  - Daily equity resets at midnight UTC (tracks intraday loss separately)
  - Cooldown prevents rapid trip/reset cycling

Integration:
  OrderGateway checks is_tripped() before every new BUY order.
  DegradationManager can call trip_manual() when bot health ratio falls to emergency tier.
"""
import time
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()

# Defaults (can be overridden via constructor or settings)
_DEFAULT_DAILY_LOSS_LIMIT_PCT = 0.05        # 5% daily loss from open
_DEFAULT_DRAWDOWN_FROM_PEAK_PCT = 0.10      # 10% drawdown from all-time peak
_DEFAULT_COOLDOWN_SECONDS = 300.0           # 5 minutes before auto-reset attempt


class PortfolioDrawdownBreaker:
    """
    Portfolio-level circuit breaker with high-water-mark algorithm.

    Usage::

        breaker = PortfolioDrawdownBreaker()
        tripped = breaker.update_equity(current_portfolio_usd)

        # In OrderGateway.place_order():
        if side == "BUY" and breaker.is_tripped():
            return {"success": False, "error": "Portfolio drawdown circuit tripped"}

        # SELL (close) always allowed:
        if side == "SELL":
            assert breaker.allows_risk_reducing()  # always True
    """

    def __init__(
        self,
        daily_loss_limit_pct: float = _DEFAULT_DAILY_LOSS_LIMIT_PCT,
        drawdown_from_peak_pct: float = _DEFAULT_DRAWDOWN_FROM_PEAK_PCT,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ):
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.drawdown_from_peak_pct = drawdown_from_peak_pct
        self.cooldown_seconds = cooldown_seconds

        # Equity tracking
        self._current_equity: float = 0.0
        self._day_open_equity: float = 0.0    # Equity at start of current UTC day
        self._peak_equity: float = 0.0        # All-time high-water mark
        self._day_str: str = ""               # YYYY-MM-DD for daily boundary detection

        # Circuit state
        self._tripped: bool = False
        self._trip_reason: Optional[str] = None
        self._tripped_at: Optional[float] = None     # monotonic time
        self._trip_count: int = 0
        self._cooldown_until: float = 0.0            # monotonic time

        # History
        self._trip_history: list = []                # Last 10 trip events

    # ── Core equity update ────────────────────────────────────────────────────

    def update_equity(self, total_equity_usd: float) -> bool:
        """
        Update current portfolio equity.

        Args:
            total_equity_usd: Current total portfolio value in USD.

        Returns:
            True if this update caused the circuit to trip.
        """
        now_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Daily boundary reset
        if now_day != self._day_str:
            self._day_str = now_day
            self._day_open_equity = total_equity_usd if total_equity_usd > 0 else self._day_open_equity
            logger.debug("PortfolioDrawdownBreaker: new trading day — day_open=%.2f", self._day_open_equity)

        self._current_equity = total_equity_usd

        # Update high-water mark (only moves up)
        if total_equity_usd > self._peak_equity:
            self._peak_equity = total_equity_usd

        # If already tripped, check auto-reset
        if self._tripped:
            self._check_auto_reset()
            return False

        # ── Condition 1: Daily P&L loss ──
        if self._day_open_equity > 0:
            daily_pnl_pct = (total_equity_usd - self._day_open_equity) / self._day_open_equity
            if daily_pnl_pct <= -self.daily_loss_limit_pct:
                self._trip(
                    f"daily_loss={daily_pnl_pct * 100:.2f}% <= -{self.daily_loss_limit_pct * 100:.1f}%"
                )
                return True

        # ── Condition 2: Drawdown from peak ──
        if self._peak_equity > 0:
            drawdown_pct = (total_equity_usd - self._peak_equity) / self._peak_equity
            if drawdown_pct <= -self.drawdown_from_peak_pct:
                self._trip(
                    f"drawdown_from_peak={drawdown_pct * 100:.2f}% <= -{self.drawdown_from_peak_pct * 100:.1f}%"
                )
                return True

        return False

    # ── Internal trip/reset ───────────────────────────────────────────────────

    def _trip(self, reason: str) -> None:
        self._tripped = True
        self._trip_reason = reason
        self._tripped_at = time.monotonic()
        self._trip_count += 1
        self._cooldown_until = time.monotonic() + self.cooldown_seconds

        event = {
            "reason": reason,
            "equity": round(self._current_equity, 2),
            "peak_equity": round(self._peak_equity, 2),
            "trip_count": self._trip_count,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self._trip_history.append(event)
        if len(self._trip_history) > 10:
            self._trip_history.pop(0)

        logger.error(
            "PortfolioDrawdownBreaker TRIPPED: %s (trip #%d, equity=%.2f, peak=%.2f)",
            reason, self._trip_count, self._current_equity, self._peak_equity,
        )

    def _check_auto_reset(self) -> None:
        """Auto-reset if cooldown elapsed AND equity has recovered above threshold."""
        now = time.monotonic()
        if now < self._cooldown_until:
            return

        # Recovery threshold: equity must be within half the drawdown limit of peak
        recovery_threshold = self._peak_equity * (1.0 - self.drawdown_from_peak_pct / 2.0)
        if self._current_equity >= recovery_threshold:
            self._tripped = False
            self._trip_reason = None
            self._tripped_at = None
            logger.info(
                "PortfolioDrawdownBreaker AUTO-RESET (equity %.2f >= threshold %.2f)",
                self._current_equity, recovery_threshold,
            )

    # ── Manual controls ───────────────────────────────────────────────────────

    def trip_manual(self, reason: str = "manual") -> None:
        """Manually trip the circuit (e.g., from DegradationManager emergency tier)."""
        self._trip(f"manual:{reason}")

    def reset_manual(self) -> None:
        """Manually reset, bypassing cooldown (operator override)."""
        self._tripped = False
        self._trip_reason = None
        self._tripped_at = None
        self._cooldown_until = 0.0
        logger.warning("PortfolioDrawdownBreaker MANUALLY RESET (operator override)")

    def seed_equity(self, current_equity_usd: float) -> None:
        """
        Seed initial equity at startup (from DB portfolio value).
        Initializes day_open and peak to avoid false trips on restart.
        """
        self._current_equity = current_equity_usd
        if self._day_open_equity == 0.0:
            self._day_open_equity = current_equity_usd
        if current_equity_usd > self._peak_equity:
            self._peak_equity = current_equity_usd
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._day_str = today
        logger.info(
            "PortfolioDrawdownBreaker seeded: equity=%.2f, peak=%.2f, day_open=%.2f",
            current_equity_usd, self._peak_equity, self._day_open_equity,
        )

    # ── Query interface ───────────────────────────────────────────────────────

    def is_tripped(self) -> bool:
        """True if circuit is active (blocks new risk-increasing orders)."""
        return self._tripped

    def allows_risk_reducing(self) -> bool:
        """
        Per FIA best practices: risk-reducing orders (SELL/close) are ALWAYS allowed
        even when the circuit is tripped. Prevents being locked into losing positions.
        """
        return True

    def get_status(self) -> Dict[str, Any]:
        """Full status snapshot for logging / dashboard."""
        daily_pnl_pct = (
            (self._current_equity - self._day_open_equity) / self._day_open_equity * 100
            if self._day_open_equity > 0 else 0.0
        )
        drawdown_pct = (
            (self._current_equity - self._peak_equity) / self._peak_equity * 100
            if self._peak_equity > 0 else 0.0
        )
        cooldown_remaining = max(0.0, round(self._cooldown_until - time.monotonic(), 1))

        return {
            "tripped": self._tripped,
            "trip_reason": self._trip_reason,
            "trip_count": self._trip_count,
            "cooldown_remaining_s": cooldown_remaining,
            "current_equity_usd": round(self._current_equity, 2),
            "peak_equity_usd": round(self._peak_equity, 2),
            "day_open_equity_usd": round(self._day_open_equity, 2),
            "daily_pnl_pct": round(daily_pnl_pct, 3),
            "drawdown_from_peak_pct": round(drawdown_pct, 3),
            "limits": {
                "daily_loss_limit_pct": self.daily_loss_limit_pct * 100,
                "drawdown_from_peak_pct": self.drawdown_from_peak_pct * 100,
                "cooldown_seconds": self.cooldown_seconds,
            },
            "recent_trips": self._trip_history[-3:],
        }
