"""
Data quality metrics and scoring for markets and pipeline.
Complements GapDetector with explicit scores and grades for dashboard and alerts.
"""
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


class QualityMetrics:
    """Calculate data quality scores per market and for the pipeline."""

    def __init__(self, db: Any):
        self.db = db

    async def calculate_market_quality(self, market_id: str) -> Dict[str, Any]:
        """
        Calculate quality score for a single market.
        Metrics: completeness, activity (trade count), price_coverage, timeliness.
        Returns overall_score (0-100), grade (A-F), and per-metric scores.
        """
        if not self.db or not self.db.session_factory:
            return {"error": "Database not available", "market_id": market_id}
        try:
            from sqlalchemy import select, func
            from bots.weather.engine.base_engine.data.database import Market, Trade, MarketPrice

            async with self.db.get_session() as session:
                # Fetch market
                result = await session.execute(select(Market).where(Market.id == market_id).limit(1))
                market = result.scalar_one_or_none()
                if not market:
                    return {"error": "Market not found", "market_id": market_id}

                scores: Dict[str, float] = {}

                # 1. Completeness (required fields present)
                required = ["question", "yes_token_id", "no_token_id", "created_at"]
                filled = sum(1 for f in required if getattr(market, f, None))
                scores["completeness"] = (filled / len(required)) * 100.0

                # 2. Activity (trade count, log scale; cap at 100)
                count_result = await session.execute(
                    select(func.count(Trade.id)).where(Trade.market_id == market_id)
                )
                trade_count = count_result.scalar_one() or 0
                scores["activity"] = min(100.0, (math.log10(trade_count + 1) / 2.0) * 100.0)

                # 3. Price coverage (expected vs actual points for last 7 days)
                price_result = await session.execute(
                    select(func.count(MarketPrice.id)).where(MarketPrice.market_id == market_id)
                )
                actual_points = price_result.scalar_one() or 0
                # Rough expected: 1h interval = 24*7 = 168 points per token; 2 tokens => 336
                expected_points = 336.0
                scores["price_coverage"] = (
                    min(100.0, (actual_points / expected_points) * 100.0) if expected_points else 0.0
                )

                # 4. Timeliness (last trade age for active markets)
                now = _naive_utc(datetime.now(timezone.utc))
                if getattr(market, "active", False):
                    latest = await session.execute(
                        select(func.max(func.coalesce(Trade.entry_time, Trade.timestamp)))
                        .where(Trade.market_id == market_id)
                    )
                    last_ts = latest.scalar_one_or_none()
                    if last_ts:
                        last_ts = _naive_utc(last_ts)
                        hours_old = (now - last_ts).total_seconds() / 3600.0
                        scores["timeliness"] = max(0.0, 100.0 - (hours_old / 48.0) * 100.0)
                    else:
                        scores["timeliness"] = 0.0
                else:
                    scores["timeliness"] = 100.0  # Closed markets don't need recent data

                weights = {
                    "completeness": 0.3,
                    "activity": 0.2,
                    "price_coverage": 0.3,
                    "timeliness": 0.2,
                }
                overall = sum(scores.get(k, 0) * w for k, w in weights.items())

                return {
                    "market_id": market_id,
                    "overall_score": round(overall, 2),
                    "grade": _grade(overall),
                    "scores": scores,
                }
        except Exception as e:
            logger.error("Error calculating market quality", market_id=market_id, error=str(e), exc_info=True)
            return {"error": str(e), "market_id": market_id}

    async def calculate_pipeline_quality(self) -> Dict[str, Any]:
        """
        Calculate overall pipeline quality from sync_log and latest trade.
        Metrics: freshness (latest trade/sync age), sync_success_rate (last 7 days).
        """
        if not self.db or not self.db.session_factory:
            return {"error": "Database not available"}
        try:
            from sqlalchemy import select
            from bots.weather.engine.base_engine.data.database import SyncLog, Trade
            from sqlalchemy import func

            now = _naive_utc(datetime.now(timezone.utc))
            metrics: Dict[str, Any] = {}

            # Freshness: age of latest trade (fallback: latest price, then latest sync)
            latest_trade = await self.db.get_latest_trade_timestamp()
            freshness_source = "trade"
            if latest_trade:
                latest_ts = _naive_utc(latest_trade)
            else:
                latest_ts = _naive_utc(await self.db.get_latest_price_timestamp())
                freshness_source = "price" if latest_ts else None
            if not latest_ts and freshness_source is None:
                latest_ts = _naive_utc(await self.db.get_latest_sync_completed_at())
                freshness_source = "sync" if latest_ts else None
            if latest_ts:
                hours_old = (now - latest_ts).total_seconds() / 3600.0
                metrics["freshness_score"] = max(0.0, 100.0 - (hours_old / 24.0) * 100.0)
                metrics["latest_trade_hours_ago"] = round(hours_old, 2)
                if freshness_source and freshness_source != "trade":
                    metrics["freshness_source"] = freshness_source
            else:
                metrics["freshness_score"] = 0.0
                metrics["latest_trade_hours_ago"] = None

            # Sync success rate (last 7 days)
            week_ago = now - timedelta(days=7)
            async with self.db.get_session() as session:
                stmt = (
                    select(SyncLog.status, func.count(SyncLog.id))
                    .where(SyncLog.started_at >= week_ago)
                    .group_by(SyncLog.status)
                )
                result = await session.execute(stmt)
                rows = result.all()
            total = sum(c for _, c in rows)
            success_count = sum(c for s, c in rows if s == "success")
            metrics["sync_success_rate"] = (success_count / total * 100.0) if total else 0.0
            metrics["sync_total_7d"] = total
            metrics["sync_success_7d"] = success_count

            overall = metrics["freshness_score"] * 0.4 + metrics["sync_success_rate"] * 0.6

            return {
                "overall_health": round(overall, 2),
                "grade": _grade(overall),
                "metrics": metrics,
                "timestamp": now.isoformat() if now else None,
            }
        except Exception as e:
            logger.error("Error calculating pipeline quality", error=str(e), exc_info=True)
            return {"error": str(e)}
