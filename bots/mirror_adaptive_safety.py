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

        # Refresh every 20 scans (~15 min at 45s interval).
        # S144: Always run immediately when unfitted (startup) so the circuit
        # breaker is active within the first scan cycle, not 15 minutes later.
        if self._fitted and (scan_count - self._last_refresh_scan < 20):
            return
        self._last_refresh_scan = scan_count

        try:
            from sqlalchemy import text
            async with self._db.get_session() as session:
                # Last 50 resolved trades
                rows = await session.execute(text(
                    "SELECT realized_pnl FROM trade_events "
                    "WHERE bot_name = 'MirrorBot' "
                    "  AND event_type IN ('EXIT', 'RESOLUTION') "
                    "  AND realized_pnl IS NOT NULL "
                    "ORDER BY event_time DESC LIMIT 50"
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

            # Drawdown: cumulative P&L curve normalised by bot capital (not P&L peak).
            # BUG-14 fix: previous code divided by max(peak, 1.0) — if recent P&L peak was
            # $200 and current is -$100, it would report 150% drawdown instead of 1.5%.
            cum = 0.0
            peak = 0.0
            for p in reversed(pnls):  # oldest first
                cum += p
                peak = max(peak, cum)
            _capital = float(getattr(settings, "MIRROR_TOTAL_CAPITAL", 20000))
            _high_water = max(peak, 0.0)  # high-water from start of window (0 if all losses)
            raw_dd = max(0.0, _high_water - cum) / max(_capital, 1.0)
            self._drawdown_pct = min(raw_dd, 1.0)  # clamp — should not exceed 100% of capital

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
        """Return dynamically adjusted max positions.

        S137 C4: Exponential decay on drawdown — exp(-8 * dd) means:
          dd=0%  → mult=1.00 (no change)
          dd=5%  → mult=0.67
          dd=10% → mult=0.45
          dd=20% → mult=0.20
        Recovery ratchet: consecutive_losses counter reduces mult further.
        Hot streak (WR > 65%) allows up to 1.2x base.
        """
        import math
        base = int(getattr(settings, "MIRROR_MAX_CONCURRENT_POSITIONS", 200))

        if not self._fitted or not getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False):
            return base

        # Exponential drawdown response
        mult = math.exp(-8.0 * self._drawdown_pct)

        # Recovery ratchet: each consecutive loss cuts a further 8%, floor 0.20
        if self._consecutive_losses >= 3:
            mult *= max(0.20, 1.0 - (self._consecutive_losses - 2) * 0.08)

        # Hot streak bonus
        if self._recent_win_rate > 0.65 and self._consecutive_losses == 0:
            mult = min(1.20, mult * 1.20)

        mult = max(0.10, min(1.20, mult))  # hard floor/ceiling
        adjusted = max(10, int(base * mult))

        if adjusted != base:
            logger.debug(
                "mirror_adaptive_max_positions",
                base=base,
                adjusted=adjusted,
                mult=round(mult, 3),
                drawdown_pct=round(self._drawdown_pct, 3),
                consecutive_losses=self._consecutive_losses,
            )

        return adjusted

    def get_adjusted_daily_cap_mult(self) -> float:
        """Return multiplier for daily cap (1.0 = no change).

        S137 C4: Exponential decay matching get_adjusted_max_positions.
        Never boosts above 1.0 — we don't chase on hot streaks for sizing.
        """
        import math
        if not self._fitted or not getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False):
            return 1.0

        # Exponential drawdown response (same decay constant as positions)
        mult = math.exp(-8.0 * self._drawdown_pct)

        # Consecutive losses ratchet
        if self._consecutive_losses >= 5:
            mult *= max(0.20, 1.0 - (self._consecutive_losses - 4) * 0.10)

        return max(0.10, min(1.0, mult))  # cap at 1.0 — never boost daily limit

    def get_adjusted_bet_size_mult(self) -> float:
        """Return per-trade size multiplier based on drawdown (1.0 = no change).

        S150: Half the decay constant of position limits (-4.0 vs -8.0) so sizing
        degrades more gently than position count. Combined with daily cap mult,
        provides defense-in-depth: drawdown shrinks both how many AND how big.
          dd=0%  → 1.00x
          dd=5%  → 0.82x
          dd=10% → 0.67x
          dd=20% → 0.45x
        Never boosts above 1.0.
        """
        import math
        if not self._fitted or not getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False):
            return 1.0

        mult = math.exp(-4.0 * self._drawdown_pct)
        return max(0.20, min(1.0, mult))
