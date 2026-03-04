"""
Airdrop activity tracker — monitor volume and market participation metrics
that could contribute to potential platform airdrops.

Tracks daily volume, unique markets traded, market-making activity,
and generates an "airdrop readiness" score for the dashboard.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional
from structlog import get_logger

logger = get_logger()


@dataclass
class AirdropMetrics:
    """Snapshot of airdrop-relevant activity metrics."""
    daily_volume_usd: float = 0.0
    weekly_volume_usd: float = 0.0
    monthly_volume_usd: float = 0.0
    unique_markets_traded: int = 0
    total_trades: int = 0
    maker_trades: int = 0
    taker_trades: int = 0
    consecutive_active_days: int = 0
    readiness_score: float = 0.0  # 0-100

    def to_dict(self) -> Dict:
        return {
            "daily_volume_usd": self.daily_volume_usd,
            "weekly_volume_usd": self.weekly_volume_usd,
            "monthly_volume_usd": self.monthly_volume_usd,
            "unique_markets_traded": self.unique_markets_traded,
            "total_trades": self.total_trades,
            "maker_trades": self.maker_trades,
            "taker_trades": self.taker_trades,
            "consecutive_active_days": self.consecutive_active_days,
            "readiness_score": self.readiness_score,
        }


class AirdropTracker:
    """
    Track trading activity metrics relevant to potential airdrops.

    Reads from the positions/trades tables and computes volume,
    market diversity, and consistency metrics.
    """

    def __init__(self, db=None):
        self._db = db
        self._last_metrics: Optional[AirdropMetrics] = None

    async def compute_metrics(self) -> AirdropMetrics:
        """Compute current airdrop readiness metrics from DB."""
        metrics = AirdropMetrics()
        if not self._db or not getattr(self._db, "session_factory", None):
            return metrics

        try:
            from sqlalchemy import text
            async with self._db.get_session() as session:
                # Daily/weekly/monthly volumes
                r = await session.execute(text("""
                    SELECT
                        COALESCE(SUM(CASE WHEN created_at >= NOW() - INTERVAL '1 day' THEN size * entry_price ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN created_at >= NOW() - INTERVAL '7 days' THEN size * entry_price ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN created_at >= NOW() - INTERVAL '30 days' THEN size * entry_price ELSE 0 END), 0),
                        COUNT(DISTINCT market_id),
                        COUNT(*)
                    FROM positions
                    WHERE created_at >= NOW() - INTERVAL '30 days'
                """))
                row = r.fetchone()
                if row:
                    metrics.daily_volume_usd = float(row[0] or 0)
                    metrics.weekly_volume_usd = float(row[1] or 0)
                    metrics.monthly_volume_usd = float(row[2] or 0)
                    metrics.unique_markets_traded = int(row[3] or 0)
                    metrics.total_trades = int(row[4] or 0)

                # Consecutive active days
                r2 = await session.execute(text("""
                    SELECT DISTINCT DATE(created_at) as trade_date
                    FROM positions
                    WHERE created_at >= NOW() - INTERVAL '90 days'
                    ORDER BY trade_date DESC
                """))
                dates = [row[0] for row in r2.fetchall()]
                metrics.consecutive_active_days = self._count_consecutive_days(dates)

        except Exception as e:
            logger.debug("AirdropTracker metrics computation failed: %s", e)

        # Compute readiness score (heuristic 0-100)
        metrics.readiness_score = self._compute_score(metrics)
        self._last_metrics = metrics
        return metrics

    def _count_consecutive_days(self, dates) -> int:
        """Count consecutive days from today backwards."""
        if not dates:
            return 0
        today = datetime.now(timezone.utc).date()
        streak = 0
        for i, d in enumerate(dates):
            expected = today if i == 0 else dates[i - 1]
            if hasattr(d, "date"):
                d = d.date() if hasattr(d, "date") else d
            diff = (expected - d).days if hasattr(expected, "days") else abs((expected - d).days) if hasattr(d, "__sub__") else 1
            if diff <= 1:
                streak += 1
            else:
                break
        return streak

    def _compute_score(self, m: AirdropMetrics) -> float:
        """Heuristic readiness score (0-100)."""
        score = 0.0
        # Volume contribution (max 40 points)
        score += min(40, m.monthly_volume_usd / 250)
        # Market diversity (max 20 points)
        score += min(20, m.unique_markets_traded * 2)
        # Consistency (max 20 points)
        score += min(20, m.consecutive_active_days)
        # Trade count (max 20 points)
        score += min(20, m.total_trades * 0.2)
        return min(100.0, score)

    @property
    def last_metrics(self) -> Optional[AirdropMetrics]:
        return self._last_metrics
