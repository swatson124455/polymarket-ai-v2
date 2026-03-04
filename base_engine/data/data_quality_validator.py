"""
Enhanced Data Quality Validation
=================================
Comprehensive validation of market and price data quality.
Detects and logs issues for monitoring and debugging.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone
from structlog import get_logger
from sqlalchemy import select, func, and_, or_
from base_engine.data.database import Market, MarketPrice, DataQualityIssue

logger = get_logger()


class DataQualityValidator:
    """Validates data quality and logs issues"""
    
    def __init__(self, database):
        self.db = database
    
    async def validate_data_quality(self) -> Dict[str, Any]:
        """
        Comprehensive data quality validation.
        
        Detects:
        - Markets missing token IDs
        - Price anomalies (outside 0-1 range)
        - Stale prices (no updates in 24h for active markets)
        - Markets with YES prices but missing NO prices
        - Price sum mismatches (YES + NO ≠ 1.0)
        
        Returns:
            Dictionary with issue counts and summary
        """
        if self.db.session_factory is None:
            return {"error": "Database not available"}
        
        issues = {
            "missing_token_ids": 0,
            "price_anomalies": 0,
            "stale_prices": 0,
            "missing_no_prices": 0,
            "price_sum_mismatch": 0,
        }
        
        async with self.db.get_session() as session:
            # L5: Soft-resolve old unresolved issues instead of deleting.
            # Marks them as resolved so quarantine lifts, preserves history.
            from sqlalchemy import update as _sql_update, text as _sql_text
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)  # NaiveUTCDateTime
            await session.execute(
                _sql_update(DataQualityIssue)
                .where(DataQualityIssue.resolved_at.is_(None))
                .values(resolved_at=now_utc)
            )
            # L5: Purge resolved issues older than 30 days (retention policy)
            retention_cutoff = now_utc - timedelta(days=30)
            await session.execute(
                _sql_text("DELETE FROM data_quality_issues WHERE resolved_at IS NOT NULL AND resolved_at < :cutoff"),
                {"cutoff": retention_cutoff},
            )
            await session.commit()
            
            # 1. Check for markets without token IDs
            logger.info("Validating: Markets without token IDs...")
            result = await session.execute(
                select(Market).where(
                    or_(
                        Market.yes_token_id.is_(None),
                        Market.yes_token_id == ""
                    )
                )
            )
            missing_tokens = result.scalars().all()
            issues["missing_token_ids"] = len(missing_tokens)
            
            for market in missing_tokens:
                issue = DataQualityIssue(
                    market_id=market.id,
                    issue_type="missing_token_id",
                    description="Market has no YES token ID"
                )
                session.add(issue)
            
            logger.info(f"Found {issues['missing_token_ids']} markets without token IDs")
            
            # 2. Check for price anomalies (prices outside 0-1 range)
            logger.info("Validating: Price anomalies...")
            result = await session.execute(
                select(MarketPrice.market_id, MarketPrice.token_id, MarketPrice.price)
                .where(
                    or_(
                        MarketPrice.price < 0,
                        MarketPrice.price > 1
                    )
                )
                .distinct()
            )
            anomalies = result.all()
            issues["price_anomalies"] = len(anomalies)
            
            logged_markets = set()
            for anomaly in anomalies:
                if anomaly.market_id not in logged_markets:
                    issue = DataQualityIssue(
                        market_id=anomaly.market_id,
                        issue_type="price_anomaly",
                        description=f"Price {anomaly.price} outside valid range (0-1)"
                    )
                    session.add(issue)
                    logged_markets.add(anomaly.market_id)
            
            logger.info(f"Found {issues['price_anomalies']} price anomalies")
            
            # 3. Check for stale prices (no updates in 24+ hours for active markets)
            logger.info("Validating: Stale prices...")
            stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
            
            result = await session.execute(
                select(Market.id, Market.question, func.max(MarketPrice.timestamp).label("last_update"))
                .join(MarketPrice, Market.id == MarketPrice.market_id, isouter=True)
                .where(Market.active == True)
                .group_by(Market.id)
                .having(
                    or_(
                        func.max(MarketPrice.timestamp) < stale_threshold,
                        func.max(MarketPrice.timestamp).is_(None)
                    )
                )
            )
            stale = result.all()
            issues["stale_prices"] = len(stale)
            
            for market in stale[:50]:  # Limit to first 50 to avoid spam
                issue = DataQualityIssue(
                    market_id=market.id,
                    issue_type="stale_prices",
                    description="No price updates in 24+ hours for active market"
                )
                session.add(issue)
            
            logger.info(f"Found {issues['stale_prices']} markets with stale prices")
            
            # 4. Check for markets with YES prices but no NO prices
            logger.info("Validating: Missing NO prices...")
            
            # Get markets with YES prices
            result = await session.execute(
                select(Market.id, Market.no_token_id)
                .join(MarketPrice, and_(
                    Market.id == MarketPrice.market_id,
                    MarketPrice.side == "YES"
                ))
                .where(
                    and_(
                        Market.no_token_id.isnot(None),
                        Market.no_token_id != ""
                    )
                )
                .distinct()
            )
            markets_with_yes = result.all()
            
            missing_no_count = 0
            for market in markets_with_yes:
                # Check if this market has NO prices
                no_result = await session.execute(
                    select(func.count(MarketPrice.id))
                    .where(
                        and_(
                            MarketPrice.market_id == market.id,
                            MarketPrice.side == "NO"
                        )
                    )
                )
                no_count = no_result.scalar() or 0
                
                if no_count == 0:
                    issue = DataQualityIssue(
                        market_id=market.id,
                        issue_type="missing_no_prices",
                        description="Has YES prices but no NO prices"
                    )
                    session.add(issue)
                    missing_no_count += 1
            
            issues["missing_no_prices"] = missing_no_count
            logger.info(f"Found {issues['missing_no_prices']} markets missing NO prices")
            
            # 5. Check for YES + NO price sum mismatches (should be ~1.0)
            logger.info("Validating: Price sum mismatches...")
            
            # Get timestamps where we have both YES and NO prices
            result = await session.execute(
                select(
                    MarketPrice.market_id,
                    MarketPrice.timestamp,
                    func.sum(func.case((MarketPrice.side == "YES", MarketPrice.price), else_=0)).label("yes_sum"),
                    func.sum(func.case((MarketPrice.side == "NO", MarketPrice.price), else_=0)).label("no_sum")
                )
                .where(MarketPrice.side.in_(["YES", "NO"]))
                .group_by(MarketPrice.market_id, MarketPrice.timestamp)
                .having(
                    func.abs(
                        func.sum(func.case((MarketPrice.side == "YES", MarketPrice.price), else_=0)) +
                        func.sum(func.case((MarketPrice.side == "NO", MarketPrice.price), else_=0)) - 1.0
                    ) > 0.05
                )
                .limit(100)
            )
            mismatches = result.all()
            issues["price_sum_mismatch"] = len(mismatches)
            
            logged_markets = set()
            for mismatch in mismatches:
                if mismatch.market_id not in logged_markets:
                    price_sum = (mismatch.yes_sum or 0) + (mismatch.no_sum or 0)
                    issue = DataQualityIssue(
                        market_id=mismatch.market_id,
                        issue_type="price_sum_mismatch",
                        description=f"YES + NO prices sum to {price_sum:.3f} (expected ~1.0)"
                    )
                    session.add(issue)
                    logged_markets.add(mismatch.market_id)
            
            logger.info(f"Found {issues['price_sum_mismatch']} price sum mismatches")
            
            await session.commit()
        
        total_issues = sum(issues.values())
        logger.info(f"Data quality validation complete: {total_issues} total issues found")
        
        return {
            **issues,
            "total_issues": total_issues,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_quality_issues_summary(self) -> Dict[str, Any]:
        """Get summary of current quality issues"""
        if self.db.session_factory is None:
            return {"error": "Database not available"}
        
        async with self.db.get_session() as session:
            # Count by type
            result = await session.execute(
                select(DataQualityIssue.issue_type, func.count(DataQualityIssue.id))
                .group_by(DataQualityIssue.issue_type)
            )
            by_type = {row[0]: row[1] for row in result.all()}
            
            # Total count
            result = await session.execute(
                select(func.count(DataQualityIssue.id))
            )
            total = result.scalar() or 0
            
            return {
                "total_issues": total,
                "by_type": by_type,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    async def get_quarantined_markets(self, hours: int = 24) -> set:
        """
        L5: Return set of market_ids with unresolved data quality issues from the last N hours.

        Markets in quarantine should be skipped during trading scans.
        Issues that have been soft-resolved (resolved_at IS NOT NULL) are excluded.

        Returns:
            Set of market_id strings with active quality issues.
        """
        if self.db.session_factory is None:
            return set()
        try:
            async with self.db.get_session() as session:
                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
                result = await session.execute(
                    select(DataQualityIssue.market_id)
                    .where(
                        and_(
                            DataQualityIssue.market_id.isnot(None),
                            DataQualityIssue.resolved_at.is_(None),  # Unresolved only
                            DataQualityIssue.detected_at >= cutoff,
                        )
                    )
                    .distinct()
                )
                quarantined = {str(row[0]) for row in result.all() if row[0]}
                if quarantined:
                    logger.debug("L5: %d markets quarantined due to data quality issues", len(quarantined))
                return quarantined
        except Exception as e:
            logger.debug("get_quarantined_markets failed: %s", e)
            return set()
