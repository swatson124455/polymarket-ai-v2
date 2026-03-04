"""
Gap detection for time-series data: trade gaps and price staleness.
Uses existing DB schema (trades, market_prices, markets); no pandas required.
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()


class GapDetector:
    """Detects gaps in trade data and price staleness for active markets."""

    def __init__(self, db: Any):
        self.db = db

    async def find_trade_gaps(
        self,
        market_id: Optional[str] = None,
        expected_interval_hours: float = 1.0,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Find gaps where no trades occurred for longer than expected_interval_hours.
        Returns list of {"market_id", "gap_start", "gap_end", "gap_duration_hours"}.
        """
        if not self.db or not self.db.session_factory:
            return []
        try:
            from sqlalchemy import select
            from base_engine.data.database import Trade

            async with self.db.get_session() as session:
                stmt = (
                    select(Trade.market_id, Trade.timestamp)
                    .where(Trade.timestamp.isnot(None))
                    .order_by(Trade.timestamp.asc())
                )
                if market_id:
                    stmt = stmt.where(Trade.market_id == market_id)
                stmt = stmt.limit(limit)
                result = await session.execute(stmt)
                rows = list(result.all())
            if len(rows) < 2:
                return []
            threshold_sec = expected_interval_hours * 3600
            gaps: List[Dict[str, Any]] = []
            for i in range(1, len(rows)):
                prev_ts = rows[i - 1][1]
                curr_ts = rows[i][1]
                if prev_ts is None or curr_ts is None:
                    continue
                delta_sec = (curr_ts - prev_ts).total_seconds()
                if delta_sec > threshold_sec:
                    gaps.append({
                        "market_id": rows[i][0],
                        "gap_start": prev_ts.isoformat() if hasattr(prev_ts, "isoformat") else str(prev_ts),
                        "gap_end": curr_ts.isoformat() if hasattr(curr_ts, "isoformat") else str(curr_ts),
                        "gap_duration_hours": round(delta_sec / 3600, 2),
                    })
            if gaps:
                logger.warning("Found %s trade gaps (threshold %.1fh)", len(gaps), expected_interval_hours)
            return gaps
        except Exception as e:
            logger.error("Error detecting trade gaps: %s", e, exc_info=True)
            return []

    async def find_markets_without_recent_prices(
        self,
        hours: float = 24.0,
        active_only: bool = True,
        limit_markets: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Find active markets that have no price row in the last `hours` hours.
        Returns list of {"market_id", "last_price_at", "hours_ago"}.
        """
        if not self.db or not self.db.session_factory:
            return []
        try:
            from sqlalchemy import select, func
            from base_engine.data.database import Market, MarketPrice

            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
            async with self.db.get_session() as session:
                # Subquery: market_id -> max(timestamp) from market_prices
                max_ts = (
                    select(MarketPrice.market_id, func.max(MarketPrice.timestamp).label("last_ts"))
                    .group_by(MarketPrice.market_id)
                ).subquery()
                stmt = (
                    select(Market.id, max_ts.c.last_ts)
                    .select_from(Market)
                    .outerjoin(max_ts, Market.id == max_ts.c.market_id)
                    .limit(limit_markets)
                )
                if active_only:
                    stmt = stmt.where(Market.active == True)
                result = await session.execute(stmt)
                rows = result.all()
            out: List[Dict[str, Any]] = []
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for mid, last_ts in rows:
                if last_ts is None:
                    out.append({"market_id": mid, "last_price_at": None, "hours_ago": None})
                    continue
                hours_ago = (now - last_ts).total_seconds() / 3600
                if hours_ago > hours:
                    out.append({
                        "market_id": mid,
                        "last_price_at": last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts),
                        "hours_ago": round(hours_ago, 2),
                    })
            if out:
                logger.warning(
                    "Found %s markets with no price in last %.1fh",
                    len(out),
                    hours,
                )
            return out
        except Exception as e:
            logger.error("Error detecting price staleness: %s", e, exc_info=True)
            return []

    async def check_continuity(
        self,
        trade_gap_hours: float = 24.0,
        price_stale_hours: float = 24.0,
    ) -> Dict[str, Any]:
        """
        Run trade-gap and price-staleness checks; return a small report.
        """
        report: Dict[str, Any] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "trade_gaps": [],
            "markets_without_recent_prices": [],
            "summary": {},
        }
        try:
            report["trade_gaps"] = await self.find_trade_gaps(expected_interval_hours=trade_gap_hours)
            report["markets_without_recent_prices"] = await self.find_markets_without_recent_prices(hours=price_stale_hours)
            report["summary"] = {
                "total_trade_gaps": len(report["trade_gaps"]),
                "markets_stale_prices": len(report["markets_without_recent_prices"]),
                "status": "OK" if not report["trade_gaps"] and not report["markets_without_recent_prices"] else "ISSUES_FOUND",
            }
            return report
        except Exception as e:
            report["summary"] = {"status": "ERROR", "error": str(e)}
            return report
