"""
MirrorBot Adaptive Safety Constraints — Session 82.

Pearl-inspired adaptive position limits that respond to drawdown state.
Instead of static MAX_POSITIONS=200, dynamically adjusts based on:
  - Recent win rate (last N trades)
  - Current drawdown depth
  - Consecutive loss streak

Gated by MIRROR_ADAPTIVE_SAFETY=true (default off).

Reference: Meta Pearl (JMLR Vol 25, 2024) — safety constraints module.
"""
from typing import Any, Dict, Optional

from structlog import get_logger
from config.settings import settings

logger = get_logger()


class MirrorAdaptiveSafety:
    """
    Adjusts position/daily limits based on recent performance.

    When active, overrides static MIRROR_MAX_CONCURRENT_POSITIONS and
    MIRROR_MAX_DAILY_EXPOSURE_PCT with dynamic values.
    """

    def __init__(self, db: Any = None):
        self._db = db
        self._recent_win_rate: float = 0.5
        self._consecutive_losses: int = 0
        self._drawdown_pct: float = 0.0
        self._last_refresh_scan: int = 0
        self._fitted = False

    async def refresh(self, scan_count: int = 0) -> None:
        """Refresh metrics from DB. Call every N scans (not every trade)."""
        if not getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False):
            return
        if not self._db or not getattr(self._db, "session_factory", None):
            return

        # Refresh every 20 scans (~15 min at 45s interval)
        if scan_count - self._last_refresh_scan < 20:
            return
        self._last_refresh_scan = scan_count

        try:
            from sqlalchemy import text
            async with self._db.get_session() as session:
                # Last 50 resolved trades
                rows = await session.execute(text(
                    "SELECT realized_pnl FROM paper_trades "
                    "WHERE bot_name = 'MirrorBot' "
                    "  AND realized_pnl IS NOT NULL "
                    "  AND side IN ('YES', 'NO') "
                    "ORDER BY created_at DESC LIMIT 50"
                ))
                pnls = [float(r[0]) for r in rows.fetchall()]

            if len(pnls) < 5:
                return

            # Win rate
            wins = sum(1 for p in pnls if p > 0)
            self._recent_win_rate = wins / len(pnls)

            # Consecutive losses (from most recent)
            streak = 0
            for p in pnls:
                if p <= 0:
                    streak += 1
                else:
                    break
            self._consecutive_losses = streak

            # Drawdown: cumulative P&L curve
            cum = 0.0
            peak = 0.0
            for p in reversed(pnls):  # oldest first
                cum += p
                peak = max(peak, cum)
            self._drawdown_pct = (peak - cum) / max(peak, 1.0) if peak > 0 else 0.0

            self._fitted = True
            logger.info(
                "mirror_adaptive_safety_refresh",
                win_rate=round(self._recent_win_rate, 2),
                consecutive_losses=self._consecutive_losses,
                drawdown_pct=round(self._drawdown_pct, 3),
                n_trades=len(pnls),
            )

        except Exception as e:
            logger.debug("mirror_adaptive_safety_refresh failed: %s", e)

    def get_adjusted_max_positions(self) -> int:
        """Return dynamically adjusted max positions."""
        base = int(getattr(settings, "MIRROR_MAX_CONCURRENT_POSITIONS", 200))

        if not self._fitted or not getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False):
            return base

        mult = 1.0

        # Reduce on losing streak: -10% per consecutive loss, floor 30%
        if self._consecutive_losses >= 3:
            mult *= max(0.30, 1.0 - self._consecutive_losses * 0.10)

        # Reduce on drawdown: -50% at 20% drawdown, floor 30%
        if self._drawdown_pct > 0.05:
            mult *= max(0.30, 1.0 - self._drawdown_pct * 2.5)

        # Boost on hot streak: +20% if win rate > 65%
        if self._recent_win_rate > 0.65 and self._consecutive_losses == 0:
            mult *= 1.20

        adjusted = max(10, int(base * mult))

        if adjusted != base:
            logger.info(
                "mirror_adaptive_max_positions",
                base=base,
                adjusted=adjusted,
                mult=round(mult, 2),
            )

        return adjusted

    def get_adjusted_daily_cap_mult(self) -> float:
        """Return multiplier for daily cap (1.0 = no change)."""
        if not self._fitted or not getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False):
            return 1.0

        if self._consecutive_losses >= 5:
            return 0.50  # Half daily cap during bad streak
        if self._drawdown_pct > 0.15:
            return 0.60
        if self._recent_win_rate > 0.65:
            return 1.15  # Slight boost

        return 1.0
