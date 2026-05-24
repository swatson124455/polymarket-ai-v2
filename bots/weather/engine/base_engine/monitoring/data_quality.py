"""
Data Quality Monitoring - Continuous data validation and quality checks.

Monitors:
- Data freshness
- Data completeness
- Data accuracy
- Anomaly detection
- Data drift detection
"""
import asyncio
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone, timedelta
from enum import Enum
from structlog import get_logger
from bots.weather.engine.base_engine.data.database import Database

logger = get_logger()


class DataQualityStatus(Enum):
    """Data quality status levels."""
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class DataQualityCheck:
    """Represents a single data quality check."""
    
    def __init__(
        self,
        name: str,
        status: DataQualityStatus,
        message: str = "",
        metric_value: Optional[float] = None,
        threshold: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.name = name
        self.status = status
        self.message = message
        self.metric_value = metric_value
        self.threshold = threshold
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }


class DataQualityMonitor:
    """
    Comprehensive data quality monitoring.
    
    Checks:
    - Data freshness (how old is the data)
    - Data completeness (are required fields present)
    - Data accuracy (validation rules)
    - Anomaly detection (unusual patterns)
    - Data drift (distribution changes)
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
        self.quality_history: List[Dict[str, Any]] = []
        self.max_history_size = 1000
        
        # Custom validators
        self.validators: Dict[str, Callable] = {}
        
        # Thresholds
        self.freshness_threshold_hours = 24
        self.completeness_threshold = 0.95  # 95% of required fields must be present
        self.anomaly_threshold_std = 3.0  # 3 standard deviations
    
    async def check_data_quality(self) -> Dict[str, Any]:
        """
        Run all data quality checks.
        
        Returns:
            Dictionary with overall status and individual check results
        """
        checks = {}
        
        # Check data freshness
        checks["freshness"] = await self._check_freshness()
        
        # Check data completeness
        checks["completeness"] = await self._check_completeness()
        
        # Check for anomalies
        checks["anomalies"] = await self._check_anomalies()
        
        # Run custom validators
        for name, validator in self.validators.items():
            try:
                if asyncio.iscoroutinefunction(validator):
                    result = await validator()
                else:
                    result = validator()
                checks[name] = result
            except Exception as e:
                logger.warning(f"Custom validator {name} failed: {str(e)}")
                checks[name] = DataQualityCheck(
                    name=name,
                    status=DataQualityStatus.UNKNOWN,
                    message=f"Validator error: {str(e)}"
                )
        
        # Determine overall status
        statuses = [check.status for check in checks.values() if isinstance(check, DataQualityCheck)]
        if not statuses:
            overall_status = DataQualityStatus.UNKNOWN
        elif any(s == DataQualityStatus.CRITICAL for s in statuses):
            overall_status = DataQualityStatus.CRITICAL
        elif any(s == DataQualityStatus.WARNING for s in statuses):
            overall_status = DataQualityStatus.WARNING
        else:
            overall_status = DataQualityStatus.GOOD
        
        result = {
            "overall": overall_status.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {name: check.to_dict() if isinstance(check, DataQualityCheck) else check for name, check in checks.items()}
        }
        
        # Store in history
        self.quality_history.append(result)
        if len(self.quality_history) > self.max_history_size:
            self.quality_history.pop(0)
        
        return result
    
    async def _check_freshness(self) -> DataQualityCheck:
        """Check data freshness."""
        if not self.db or not self.db.session_factory:
            return DataQualityCheck(
                name="freshness",
                status=DataQualityStatus.UNKNOWN,
                message="Database not available"
            )
        
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import text, func
                
                # Check latest market update
                result = await session.execute(
                    text("SELECT MAX(updated_at) FROM markets WHERE updated_at IS NOT NULL")
                )
                latest_update = result.scalar()
                
                if not latest_update:
                    return DataQualityCheck(
                        name="freshness",
                        status=DataQualityStatus.CRITICAL,
                        message="No market data found",
                        metric_value=0
                    )
                
                # Parse datetime if string
                if isinstance(latest_update, str):
                    latest_update = datetime.fromisoformat(latest_update.replace('Z', '+00:00'))
                
                age_hours = (datetime.now(timezone.utc) - latest_update.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                
                if age_hours > self.freshness_threshold_hours:
                    status = DataQualityStatus.CRITICAL
                    message = f"Data is {age_hours:.1f} hours old (threshold: {self.freshness_threshold_hours}h)"
                elif age_hours > self.freshness_threshold_hours * 0.5:
                    status = DataQualityStatus.WARNING
                    message = f"Data is {age_hours:.1f} hours old"
                else:
                    status = DataQualityStatus.GOOD
                    message = f"Data is fresh ({age_hours:.1f} hours old)"
                
                return DataQualityCheck(
                    name="freshness",
                    status=status,
                    message=message,
                    metric_value=round(age_hours, 2),
                    threshold=self.freshness_threshold_hours,
                    details={"latest_update": latest_update.isoformat() if hasattr(latest_update, 'isoformat') else str(latest_update)}
                )
        except Exception as e:
            return DataQualityCheck(
                name="freshness",
                status=DataQualityStatus.UNKNOWN,
                message=f"Freshness check failed: {str(e)}"
            )
    
    async def _check_completeness(self) -> DataQualityCheck:
        """Check data completeness."""
        if not self.db or not self.db.session_factory:
            return DataQualityCheck(
                name="completeness",
                status=DataQualityStatus.UNKNOWN,
                message="Database not available"
            )
        
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import text, func
                
                # Check markets table completeness
                total_markets = await session.execute(text("SELECT COUNT(*) FROM markets"))
                total = total_markets.scalar() or 0
                
                if total == 0:
                    return DataQualityCheck(
                        name="completeness",
                        status=DataQualityStatus.CRITICAL,
                        message="No markets in database",
                        metric_value=0.0
                    )
                
                # Check for required fields
                markets_with_category = await session.execute(
                    text("SELECT COUNT(*) FROM markets WHERE category IS NOT NULL AND category != ''")
                )
                with_category = markets_with_category.scalar() or 0
                
                markets_with_liquidity = await session.execute(
                    text("SELECT COUNT(*) FROM markets WHERE liquidity IS NOT NULL")
                )
                with_liquidity = markets_with_liquidity.scalar() or 0
                
                category_completeness = (with_category / total) if total > 0 else 0
                liquidity_completeness = (with_liquidity / total) if total > 0 else 0
                overall_completeness = (category_completeness + liquidity_completeness) / 2
                
                if overall_completeness < self.completeness_threshold:
                    status = DataQualityStatus.CRITICAL
                    message = f"Data completeness {overall_completeness:.1%} below threshold {self.completeness_threshold:.1%}"
                elif overall_completeness < self.completeness_threshold + 0.05:
                    status = DataQualityStatus.WARNING
                    message = f"Data completeness {overall_completeness:.1%} approaching threshold"
                else:
                    status = DataQualityStatus.GOOD
                    message = f"Data completeness {overall_completeness:.1%}"
                
                return DataQualityCheck(
                    name="completeness",
                    status=status,
                    message=message,
                    metric_value=round(overall_completeness, 4),
                    threshold=self.completeness_threshold,
                    details={
                        "total_markets": total,
                        "category_completeness": round(category_completeness, 4),
                        "liquidity_completeness": round(liquidity_completeness, 4)
                    }
                )
        except Exception as e:
            return DataQualityCheck(
                name="completeness",
                status=DataQualityStatus.UNKNOWN,
                message=f"Completeness check failed: {str(e)}"
            )
    
    async def _check_anomalies(self) -> DataQualityCheck:
        """Check for data anomalies."""
        if not self.db or not self.db.session_factory:
            return DataQualityCheck(
                name="anomalies",
                status=DataQualityStatus.UNKNOWN,
                message="Database not available"
            )
        
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import text
                
                # Check for price anomalies (prices should be between 0 and 1)
                result = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM markets 
                        WHERE (price < 0 OR price > 1) 
                        AND price IS NOT NULL
                    """)
                )
                price_anomalies = result.scalar() or 0
                
                # Check for negative liquidity
                result = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM markets 
                        WHERE liquidity < 0 AND liquidity IS NOT NULL
                    """)
                )
                liquidity_anomalies = result.scalar() or 0
                
                total_anomalies = price_anomalies + liquidity_anomalies
                
                if total_anomalies > 0:
                    status = DataQualityStatus.WARNING
                    message = f"Found {total_anomalies} data anomalies"
                else:
                    status = DataQualityStatus.GOOD
                    message = "No anomalies detected"
                
                return DataQualityCheck(
                    name="anomalies",
                    status=status,
                    message=message,
                    metric_value=float(total_anomalies),
                    details={
                        "price_anomalies": price_anomalies,
                        "liquidity_anomalies": liquidity_anomalies
                    }
                )
        except Exception as e:
            return DataQualityCheck(
                name="anomalies",
                status=DataQualityStatus.UNKNOWN,
                message=f"Anomaly check failed: {str(e)}"
            )
    
    def calculate_psi(
        self,
        expected: List[float],
        actual: List[float],
        n_bins: int = 10,
    ) -> float:
        """
        Population Stability Index (PSI) for feature drift detection.
        PSI < 0.1: no drift. 0.1-0.2: moderate. >0.2: significant drift.
        """
        import numpy as np
        if len(expected) < n_bins or len(actual) < n_bins:
            return 0.0
        e = np.array(expected, dtype=float)
        a = np.array(actual, dtype=float)
        bins = np.percentile(e, np.linspace(0, 100, n_bins + 1))
        bins[0] = -np.inf
        bins[-1] = np.inf
        e_counts = np.histogram(e, bins=bins)[0].astype(float)
        a_counts = np.histogram(a, bins=bins)[0].astype(float)
        e_pct = np.clip(e_counts / max(e_counts.sum(), 1), 0.0001, None)
        a_pct = np.clip(a_counts / max(a_counts.sum(), 1), 0.0001, None)
        psi = float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))
        return round(psi, 4)

    async def check_feature_drift_psi(
        self,
        feature_name: str,
        expected: List[float],
        actual: List[float],
    ) -> DataQualityCheck:
        """Check a single feature for distribution drift via PSI."""
        psi = self.calculate_psi(expected, actual)
        if psi > 0.2:
            status = DataQualityStatus.CRITICAL
            msg = f"Feature '{feature_name}' PSI={psi:.4f} — significant drift detected"
        elif psi > 0.1:
            status = DataQualityStatus.WARNING
            msg = f"Feature '{feature_name}' PSI={psi:.4f} — moderate drift"
        else:
            status = DataQualityStatus.GOOD
            msg = f"Feature '{feature_name}' PSI={psi:.4f} — stable"
        return DataQualityCheck(
            name=f"psi_{feature_name}",
            status=status,
            message=msg,
            metric_value=psi,
            threshold=0.2,
            details={"feature": feature_name, "n_expected": len(expected), "n_actual": len(actual)},
        )

    def add_validator(self, name: str, validator: Callable):
        """Add a custom data quality validator."""
        self.validators[name] = validator

    def get_quality_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent quality check history."""
        return self.quality_history[-limit:]
