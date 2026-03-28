"""
Bayesian Elite Reliability (Element 4).

Per-user Beta(alpha, beta) from resolved trades. All counts from data; no invented numbers.
Prior: Beta(6, 10) = empirical Bayes centered at 37.5% population win rate (6/(6+10)).
S137: Updated from flat Beta(1, 1) to reflect observed 39.3% WR across 5,495 trades.
Traders with few resolved trades are shrunk toward the population mean instead of 50%.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from structlog import get_logger

logger = get_logger()


class EliteReliabilityTracker:
    """
    Tracks per-user reliability as Beta(correct+1, incorrect+1) from historical
    resolved trades. likelihood_ratio(user, side) returns odds for that user/side.
    """

    def __init__(self, db: Any, lookback_days: int = 365):
        self.db = db
        self.lookback_days = lookback_days
        self._cache: Dict[str, Dict[str, float]] = {}  # address -> { alpha_yes, beta_yes, alpha_no, beta_no }
        self._cat_cache: Dict[Tuple[str, str], Dict[str, float]] = {}  # (address, category) -> same shape

    # S137 C6: Empirical Bayes prior Beta(6, 10) centered at 37.5% population WR.
    # Was Beta(1, 1) = flat "I know nothing" prior → shrank toward 50%, masking that
    # 76% of tracked traders are unprofitable. Beta(6, 10) shrinks low-data traders
    # toward ~37.5% instead, giving more realistic early estimates.
    _PRIOR_ALPHA: int = 6   # pseudo-wins
    _PRIOR_BETA: int = 10   # pseudo-losses  (6/(6+10) = 37.5% ≈ observed population WR)

    @staticmethod
    def _build_beta_rec(r: Dict[str, Any]) -> Dict[str, float]:
        """Convert a row of resolution counts into Beta params."""
        yes_correct = int(r.get("yes_correct") or 0)
        yes_total = int(r.get("yes_total") or 0)
        no_correct = int(r.get("no_correct") or 0)
        no_total = int(r.get("no_total") or 0)
        yes_incorrect = yes_total - yes_correct
        no_incorrect = no_total - no_correct
        # S137 C6: Empirical Bayes prior Beta(6,10) instead of flat Beta(1,1)
        return {
            "alpha_yes": yes_correct + EliteReliabilityTracker._PRIOR_ALPHA,
            "beta_yes": yes_incorrect + EliteReliabilityTracker._PRIOR_BETA,
            "alpha_no": no_correct + EliteReliabilityTracker._PRIOR_ALPHA,
            "beta_no": no_incorrect + EliteReliabilityTracker._PRIOR_BETA,
            "yes_total": yes_total,
            "no_total": no_total,
        }

    async def refresh(self) -> None:
        """Load per-user resolution counts from DB and compute Beta params."""
        if not self.db or not getattr(self.db, "get_user_resolution_counts", None):
            self._cache = {}
            self._cat_cache = {}
            return
        rows = await self.db.get_user_resolution_counts(lookback_days=self.lookback_days)
        self._cache = {}
        for r in rows:
            addr = (r.get("user_address") or "").strip()
            if not addr:
                continue
            self._cache[addr.lower()] = self._build_beta_rec(r)

        # Per-category cache
        self._cat_cache = {}
        if getattr(self.db, "get_user_resolution_counts_by_category", None):
            try:
                cat_rows = await self.db.get_user_resolution_counts_by_category(
                    lookback_days=self.lookback_days
                )
                for r in cat_rows:
                    addr = (r.get("user_address") or "").strip()
                    cat = (r.get("category") or "unknown").strip().lower()
                    if not addr:
                        continue
                    self._cat_cache[(addr.lower(), cat)] = self._build_beta_rec(r)
            except Exception as e:
                logger.warning("Category reliability load failed: %s", e)

        logger.info("Elite reliability refreshed", n_users=len(self._cache),
                    n_category_entries=len(self._cat_cache))

        # S117: Persist to system_kv for instant startup next restart
        await self.save_to_cache()

    async def save_to_cache(self) -> None:
        """S117: Persist _cache and _cat_cache to system_kv for instant startup."""
        if not self.db or not getattr(self.db, "get_session", None):
            return
        try:
            from sqlalchemy import text as _txt
            # Convert tuple keys in _cat_cache to string keys for JSON serialization
            cat_serializable = {f"{k[0]}|{k[1]}": v for k, v in self._cat_cache.items()}
            payload = json.dumps({"cache": self._cache, "cat_cache": cat_serializable})
            async with self.db.get_session() as sess:
                await sess.execute(_txt(
                    "INSERT INTO system_kv (key, value, updated_at) "
                    "VALUES ('reliability_cache', :val, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value = :val, updated_at = NOW()"
                ), {"val": payload})
                await sess.commit()
            logger.debug("reliability_cache_saved", n_users=len(self._cache),
                         n_cat=len(self._cat_cache))
        except Exception as e:
            logger.debug("reliability_cache_save_failed", error=str(e))

    async def load_from_cache(self, max_age_hours: int = 24) -> bool:
        """S117: Load _cache and _cat_cache from system_kv. Returns True if loaded."""
        if not self.db or not getattr(self.db, "get_session", None):
            return False
        try:
            from sqlalchemy import text as _txt
            async with self.db.get_session() as sess:
                row = (await sess.execute(_txt(
                    "SELECT value, updated_at FROM system_kv WHERE key = 'reliability_cache'"
                ))).first()
            if row is None:
                return False
            # Check staleness
            from datetime import datetime, timezone, timedelta
            updated_at = row[1]
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - updated_at > timedelta(hours=max_age_hours):
                logger.info("reliability_cache_stale", age_hours=(datetime.now(timezone.utc) - updated_at).total_seconds() / 3600)
                return False
            data = json.loads(row[0])
            self._cache = data.get("cache", {})
            # Restore tuple keys in _cat_cache
            raw_cat = data.get("cat_cache", {})
            self._cat_cache = {}
            for k, v in raw_cat.items():
                parts = k.split("|", 1)
                if len(parts) == 2:
                    self._cat_cache[(parts[0], parts[1])] = v
            logger.info("reliability_cache_loaded", n_users=len(self._cache),
                        n_cat=len(self._cat_cache))
            return True
        except Exception as e:
            logger.debug("reliability_cache_load_failed", error=str(e))
            return False

    def _get_beta(self, address: str, side: str,
                  category: Optional[str] = None,
                  min_cat_samples: int = 5) -> Tuple[float, float]:
        """Return (alpha, beta) for this user and side. Side is YES or NO.

        If *category* is provided and the user has >= min_cat_samples resolved
        trades in that category, returns category-specific Beta params.
        Otherwise falls back to the user's overall stats.
        """
        key = (address or "").strip().lower()
        side_upper = (side or "YES").upper()
        is_yes = side_upper in ("YES", "BUY")

        # Try category-specific first
        if category and self._cat_cache:
            cat_key = (key, category.strip().lower())
            cat_rec = self._cat_cache.get(cat_key)
            if cat_rec:
                cat_samples = (cat_rec["yes_total"] + cat_rec["no_total"])
                if cat_samples >= min_cat_samples:
                    if is_yes:
                        return (cat_rec["alpha_yes"], cat_rec["beta_yes"])
                    return (cat_rec["alpha_no"], cat_rec["beta_no"])

        # Fallback: overall per-user stats
        rec = self._cache.get(key)
        if not rec:
            return (1.0, 1.0)  # Beta(1,1) = know nothing
        if is_yes:
            return (rec["alpha_yes"], rec["beta_yes"])
        return (rec["alpha_no"], rec["beta_no"])

    def mean(self, address: str, side: str, **kwargs: Any) -> float:
        """Posterior mean accuracy for this user/side (alpha/(alpha+beta))."""
        a, b = self._get_beta(address, side, **kwargs)
        if a + b <= 0:
            return 0.5
        return a / (a + b)

    def likelihood_ratio(self, address: str, side: str, **kwargs: Any) -> float:
        """
        Odds of outcome given this user traded this side: mean / (1 - mean).
        When they trade YES, how much more likely is YES? Returns odds (e.g. 1.67).
        For unknown user or no data, returns 1.0 (no update).

        Accepts optional *category* kwarg for per-category Beta lookup.
        """
        a, b = self._get_beta(address, side, **kwargs)
        if a + b <= 2:
            return 1.0  # Prior only (1,1) -> mean 0.5 -> odds 1.0
        mean = a / (a + b)
        if mean <= 0 or mean >= 1:
            return 1.0
        return mean / (1 - mean)

    def total_trade_count(self, address: str) -> int:
        """Return total resolved trades for this trader across all categories."""
        key = (address or "").strip().lower()
        rec = self._cache.get(key)
        if not rec:
            return 0
        return rec.get("yes_total", 0) + rec.get("no_total", 0)

    def overall_win_rate(self, address: str) -> float:
        """Return overall win rate (correct / total) across all sides and categories.

        Returns 0.5 (uninformative prior) if no resolved trades exist.
        """
        key = (address or "").strip().lower()
        rec = self._cache.get(key)
        if not rec:
            return 0.5
        total = rec.get("yes_total", 0) + rec.get("no_total", 0)
        if total == 0:
            return 0.5
        # alpha includes +1 prior, so correct = alpha - 1
        correct = (rec.get("alpha_yes", 1) - 1) + (rec.get("alpha_no", 1) - 1)
        return correct / total

    def category_trade_count(self, address: str, category: str) -> int:
        """Return total resolved trades for this trader in the given category."""
        if not category or not self._cat_cache:
            return 0
        key = (address or "").strip().lower()
        cat_key = (key, category.strip().lower())
        rec = self._cat_cache.get(cat_key)
        if not rec:
            return 0
        return rec.get("yes_total", 0) + rec.get("no_total", 0)
